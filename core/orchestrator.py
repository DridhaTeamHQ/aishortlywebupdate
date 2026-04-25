"""Single-flow orchestrator with event resolution, category intelligence and image quality checks."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from config.settings import get_settings
from core.cms import ArticleData, CMSPublisher
from core.intelligence import CategoryDecider, Summarizer
from core.media import ImageQualityPipeline
from core.memory import AgentMemory
from core.pipeline import BreakingNewsClassifier, EventResolver, MultiAgentIngestion, PipelineMetrics
from core.validator import ArticleValidator
from utils.logger import get_logger


PIPELINE_TO_CMS_CATEGORY = {
    "business": "Business",
    "tech": "Technology",
    "international": "International",
    "national": "National",
    "environment": "Environment",
    "crime": "Crime",
    "sports": "Sports",
}

STOPWORDS = {
    "the", "and", "with", "from", "into", "amid", "over", "after", "near", "their", "this", "that", "will",
    "have", "has", "been", "about", "under", "into", "more", "than", "what", "when", "where", "which", "india",
}

PRIORITY_KEYWORD_GROUPS: Dict[str, List[str]] = {
    "international": [
        "us", "usa", "white house", "narendra modi", "donald trump", "elon musk", "bill gates",
        "satya nadella", "sundar pichai", "obama", "rishi sunak", "dubai", "saudi", "gulf", "gcc",
        "singapore", "thailand", "russia", "eu", "nato", "un", "brics", "g7", "israel",
        "jai shankar", "jaishankar", "oscars",
    ],
    "national": [
        "narendra modi", "modi", "pm modi", "amit shah", "nithin gadkari", "arvind kejriwal", "rajnath",
        "minister", "cabinet", "parliament", "speaker", "lop", "rajyasabha", "rajya sabha",
        "supreme court", "cec", "ec", "election commission", "bollywood", "rahul gandhi", "kharge",
        "sonia gandhi", "bjp", "congress", "aap", "shiv sena", "rjd", "rss", "brs", "tdp", "ysrcp",
        "dmk", "aiadmk", "chandra babu", "naidu", "jagan", "vijay", "stalin", "mgr",
    ],
    "sports": [
        "cricket", "football", "tennis", "world cup", "ipl", "f1", "champions league", "fifa",
        "olympics", "wimbeldon", "wimbledon", "gambhir", "dhoni", "virat", "hardik", "suryakumar",
        "rohit", "bumrah", "gill", "sachin", "india", "pakistan", "england", "australia",
        "south africa", "new zealand",
    ],
    "technology": [
        "apple", "samsung", "xiaomi", "oppo", "vivo", "one plus", "nothing", "amazon", "flipkart",
        "myntra", "ajio", "whatsapp", "ai", "google",
    ],
    "business": [
        "nasa", "isro", "rbi", "cag", "nifty", "sensex", "bse", "nse", "results", "startup",
        "real estate", "banks", "gold", "silver", "pe", "vc", "fund", "ambani", "reliance",
        "adani", "mittal", "birla",
    ],
    "general": [
        "bollywood", "tollywood", "tamil", "kannada", "hollywood", "netflix", "amazon", "jio", "sony",
        "oscar", "filmfare", "review", "rating", "controversy", "trailer", "date", "exercise",
        "fitness", "tips", "obesity", "brain", "adulteration", "hospitals", "diagnostics", "preventive",
    ],
}

class HardenedOrchestrator:
    def __init__(self):
        self.logger = get_logger("orchestrator")
        self.settings = get_settings()

        self.publisher = CMSPublisher()
        self.summarizer = Summarizer()
        self.category_decider = CategoryDecider()
        self.validator = ArticleValidator()
        self.memory = AgentMemory()

        self.ingestion = MultiAgentIngestion(
            category_sources=self.settings.category_sources,
            max_links_per_source=self.settings.max_links_per_source,
            max_article_age_minutes=self.settings.max_article_age_minutes,
            require_published_time=self.settings.require_published_time,
        )
        self.resolver = EventResolver(
            title_similarity=self.settings.resolver_title_similarity,
            content_similarity=self.settings.resolver_content_similarity,
            time_window_minutes=self.settings.resolver_time_window_minutes,
        )
        self.breaking = BreakingNewsClassifier(
            source_credibility=self.settings.source_credibility,
            min_sources=self.settings.breaking_min_sources,
            max_window_minutes=self.settings.breaking_window_minutes,
            confidence_threshold=self.settings.breaking_confidence_threshold,
        )
        self.image_pipeline = ImageQualityPipeline(
            thresholds=self.settings.image_quality_thresholds,
            download_dir="downloads/images",
        )

        self.publish_plan = self._normalize_publish_plan(self.settings.category_publish_plan)

        # Per-run category filter (set via RUN_CATEGORY_FILTER env var by worker)
        category_filter = (os.getenv("RUN_CATEGORY_FILTER") or "").strip().lower()
        if category_filter and category_filter not in {"all", ""}:
            filtered = [step for step in self.publish_plan if str(step["category"]).lower() == category_filter]
            if filtered:
                self.publish_plan = filtered
                self.logger.info(f"run.category_filter applied={category_filter} steps={len(filtered)}")
            else:
                self.logger.warning(f"run.category_filter unknown={category_filter} — using full plan")

        self.max_publish_retries = 2
        self.max_login_retries = 2
        self.max_consecutive_publish_failures = 10

        # Fuzzy title dedup: track all titles published in this run
        self._published_titles: List[str] = []
        self._published_image_refs: set[str] = set()

        # Worker integration hooks
        self._cancel_check = None
        self._event_sink = None

    # ── Worker Integration ──────────────────────────────────────────
    def set_runtime_controls(self, cancel_check=None, event_sink=None):
        """Allow worker/job_runner to inject cancellation and event reporting."""
        self._cancel_check = cancel_check
        self._event_sink = event_sink

    def _is_cancelled(self) -> bool:
        if self._cancel_check:
            try:
                return bool(self._cancel_check())
            except Exception:
                return False
        return False

    async def _emit_event(self, event_type: str, data: dict | None = None):
        if self._event_sink:
            try:
                result = self._event_sink(event_type, data or {})
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

    def _normalize_publish_plan(self, raw_plan: List[Dict[str, Any]]) -> List[Dict[str, int | str]]:
        normalized: List[Dict[str, int | str]] = []
        seen = set()

        for row in raw_plan:
            category = str(row.get("category", "")).strip().lower()
            if not category or category in seen:
                continue
            try:
                total = int(row.get("total", 5))
            except Exception:
                total = 5
            try:
                breaking_target = int(row.get("breaking_target", 3))
            except Exception:
                breaking_target = 3

            if total <= 0:
                continue
            breaking_target = max(0, min(breaking_target, total))
            normalized.append({"category": category, "total": total, "breaking_target": breaking_target})
            seen.add(category)

        return normalized

    async def run(self) -> None:
        await self.publisher.start()
        try:
            if self._is_cancelled():
                return
            if not await self._safe_login():
                message = "CMS login failed after retries"
                self.logger.critical(message)
                await self._emit_event("ERROR", {"message": message})
                raise RuntimeError(message)

            if self.settings.scheduler_enabled:
                while not self._is_cancelled():
                    await self._run_once()
                    await asyncio.sleep(self.settings.scheduler_interval_minutes * 60)
            else:
                await self._run_once()
        finally:
            await self.publisher.stop()

    async def _run_once(self) -> None:
        metrics = PipelineMetrics()

        # ── Cross-run intelligence: preload recently published titles from Supabase ──
        try:
            supabase_titles = self.memory.get_recent_published_titles(within_hours=48)
            if supabase_titles:
                # Merge into _published_titles so _is_title_similar covers cross-run dupes
                existing = set(self._published_titles)
                for t in supabase_titles:
                    if t not in existing:
                        self._published_titles.append(t)
                self.logger.info(f"dedup.preloaded {len(supabase_titles)} titles from Supabase for cross-run dedup")
        except Exception as exc:
            self.logger.warning(f"Cross-run title preload failed (non-fatal): {exc}")

        if self._is_cancelled():
            return
        await self._emit_event("SCRAPE_STARTED", {})
        by_category = self.ingestion.run()
        metrics.record_category_counts(by_category)
        total_scraped = sum(len(rows) for rows in by_category.values())
        await self._emit_event("SCRAPE_DONE", {"total": total_scraped})
        await self._emit_event(
            "LOG",
            {
                "message": f"Scraped {total_scraped} articles across {sum(1 for rows in by_category.values() if rows)} categories",
            },
        )
        if total_scraped <= 0:
            message = "No articles scraped from configured sources"
            self.logger.error(message)
            await self._emit_event("ERROR", {"message": message})
            raise RuntimeError(message)
        if self._is_cancelled():
            return

        for cat, count in metrics.total_scraped_per_category.items():
            self.logger.info(f"metric.scraped category={cat} count={count}")
        active_category_count = sum(1 for rows in by_category.values() if rows)
        self.logger.info(f"metric.active_categories count={active_category_count}")

        all_articles = [item for rows in by_category.values() for item in rows]
        clusters = self.resolver.cluster(all_articles)
        metrics.clusters_formed = len(clusters)
        self.logger.info(f"metric.clusters_formed count={metrics.clusters_formed}")
        if self._is_cancelled():
            return

        cluster_rows: List[Tuple[object, object]] = []
        for cluster in clusters:
            decision = self.breaking.classify(cluster)
            if decision.is_breaking:
                metrics.breaking_news_count += 1
            cluster_rows.append((cluster, decision))

        self.logger.info(f"metric.breaking_news_count count={metrics.breaking_news_count}")
        await self._emit_event(
            "LOG",
            {
                "message": f"Resolved {len(cluster_rows)} story clusters with {metrics.breaking_news_count} breaking candidates",
            },
        )
        if not cluster_rows:
            message = "No candidate stories available after deduplication"
            self.logger.error(message)
            await self._emit_event("ERROR", {"message": message})
            raise RuntimeError(message)

        clusters_by_category: Dict[str, List[Tuple[object, object]]] = {}
        for cluster, decision in cluster_rows:
            category = str(getattr(cluster, "dominant_category", "")).strip().lower()
            if not category:
                continue
            clusters_by_category.setdefault(category, []).append((cluster, decision))

        published = 0
        consecutive_publish_failures = 0
        stop_run = False
        published_story_keys: set[str] = set()
        max_articles_per_run = self._max_articles_per_run()

        for step in self.publish_plan:
            if self._is_cancelled():
                stop_run = True
                break
            if max_articles_per_run and published >= max_articles_per_run:
                self.logger.info(
                    f"run.limit_reached published={published} max_articles={max_articles_per_run}"
                )
                break
            category = str(step["category"])
            total_target = int(step["total"])
            breaking_target = int(step["breaking_target"])
            remaining_slots = (max_articles_per_run - published) if max_articles_per_run else 0
            total_target, breaking_target = self._effective_publish_targets(
                total_target,
                breaking_target,
                active_category_count,
                remaining_slots=remaining_slots,
            )
            if total_target <= 0:
                break

            category_rows = clusters_by_category.get(category, [])
            if not category_rows:
                self.logger.info(
                    f"category.result category={category} published=0 breaking_published=0 target_total={total_target} target_breaking={breaking_target} reason=no_candidates"
                )
                continue

            breaking_pool = [row for row in category_rows if bool(getattr(row[1], "is_breaking", False))]
            normal_pool = [row for row in category_rows if not bool(getattr(row[1], "is_breaking", False))]

            breaking_pool.sort(
                key=lambda row: (
                    self._cluster_priority_score(row[0], category),
                    float(getattr(row[1], "confidence", 0.0)),
                    getattr(row[0], "end_time", datetime.min.replace(tzinfo=timezone.utc)),
                    -self._cluster_source_rank(row[0]),
                ),
                reverse=True,
            )
            normal_pool.sort(
                key=lambda row: (
                    self._cluster_priority_score(row[0], category),
                    getattr(row[0], "end_time", datetime.min.replace(tzinfo=timezone.utc)),
                    -self._cluster_source_rank(row[0]),
                ),
                reverse=True,
            )
            cat_published = 0
            cat_breaking_published = 0

            self.logger.info(
                f"category.plan category={category} target_total={total_target} target_breaking={breaking_target} candidates={len(category_rows)}"
            )
            attempted_urls: set[str] = set()
            source_failures: Dict[str, int] = {}

            while cat_published < total_target:
                selected: Optional[Tuple[object, object]] = None
                forced_breaking = False

                if cat_breaking_published < breaking_target and breaking_pool:
                    selected = self._pop_with_source_backoff(breaking_pool, source_failures)
                elif cat_breaking_published < breaking_target and normal_pool:
                    selected = self._pop_with_source_backoff(normal_pool, source_failures)
                    forced_breaking = True
                elif normal_pool:
                    selected = self._pop_with_source_backoff(normal_pool, source_failures)
                elif breaking_pool:
                    selected = self._pop_with_source_backoff(breaking_pool, source_failures)

                if selected is None:
                    break
                cluster, decision = selected
                cluster_story_key = self._cluster_story_key(cluster)
                if self._story_already_published(cluster_story_key, published_story_keys):
                    self.logger.info(
                        f"skip.duplicate_story story_key={cluster_story_key} title={getattr(cluster, 'canonical_title', '')}"
                    )
                    continue
                cluster_title = str(getattr(cluster, "canonical_title", "") or "").strip()
                if cluster_title and self._is_title_similar(cluster_title):
                    self.logger.info(
                        f"skip.duplicate_cluster_title title={cluster_title[:80]} story_key={cluster_story_key}"
                    )
                    continue
                cluster_source = self._cluster_primary_source(cluster)
                cluster_articles = list(getattr(cluster, "articles", []) or [])
                cluster_articles.sort(
                    key=lambda a: (
                        self._article_priority_score(a, category),
                        len(getattr(a, "body", "") or ""),
                    ),
                    reverse=True,
                )
                if not cluster_articles:
                    article = self._pick_representative(getattr(cluster, "articles", []))
                    if article:
                        cluster_articles = [article]
                if not cluster_articles:
                    continue

                selected_is_breaking = forced_breaking or bool(getattr(decision, "is_breaking", False))
                if forced_breaking:
                    self.logger.info(f"breaking.fallback_promote url={cluster_articles[0].url} category={category}")

                published_from_cluster = False
                for article in cluster_articles:
                    if self._is_cancelled():
                        stop_run = True
                        break

                    article_url = str(getattr(article, "url", "")).strip()
                    if not article_url or article_url in attempted_urls:
                        continue
                    attempted_urls.add(article_url)

                    if self.memory.is_success(article_url):
                        continue
                    if self.settings.scheduler_enabled and self.memory.is_recent_failure(article_url, within_minutes=self.settings.recent_failure_skip_minutes):
                        self.logger.info(f"skip.recent_failure url={article_url}")
                        continue

                    if self._is_article_too_old(getattr(article, "published_time", None)):
                        self.memory.mark_failed(article_url, "article_too_old")
                        continue
                    if self._is_low_signal_story(article, category, selected_is_breaking):
                        self.logger.info(f"skip.low_signal url={article_url} category={category}")
                        continue

                    ok, fail_reason, published_image_ref, published_summary_title = await self._publish_article(article, selected_is_breaking, metrics)
                    if ok:
                        cat_published += 1
                        published += 1
                        if selected_is_breaking:
                            cat_breaking_published += 1
                        consecutive_publish_failures = 0
                        self.memory.mark_success(article_url)
                        # Use AI-summarized title for cross-run dedup (matches what next run will generate)
                        dedup_title = (published_summary_title or "").strip() or getattr(cluster, "canonical_title", "")
                        if cluster_story_key:
                            published_story_keys.add(cluster_story_key)
                            self.memory.mark_story_success(
                                cluster_story_key,
                                article_url,
                                dedup_title,
                            )
                        # Persist to Supabase so future runs on any worker skip this article
                        self.memory.mark_published_in_supabase(
                            article_url,
                            title=dedup_title,
                            story_key=cluster_story_key or "",
                            image_url=published_image_ref,
                        )
                        published_from_cluster = True
                        break

                    if fail_reason == "cancelled":
                        stop_run = True
                        break

                    if fail_reason == "workflow_failed":
                        consecutive_publish_failures += 1
                        self.memory.mark_failed(article_url, "publish_failed")
                    else:
                        self.logger.info(f"skip.transient_failure url={article_url} reason={fail_reason}")

                    if cluster_source and fail_reason in {"image_missing", "workflow_failed"}:
                        source_failures[cluster_source] = source_failures.get(cluster_source, 0) + 1

                    if consecutive_publish_failures >= self.max_consecutive_publish_failures:
                        self.logger.error(
                            f"Too many consecutive publish failures ({consecutive_publish_failures}) in "
                            f"category={category} — skipping to next category"
                        )
                        # Break only this category's while-loop; do NOT abort other categories
                        consecutive_publish_failures = 0
                        break

                if stop_run:
                    break
                if not published_from_cluster:
                    continue
            self.logger.info(
                f"category.result category={category} published={cat_published} breaking_published={cat_breaking_published} "
                f"target_total={total_target} target_breaking={breaking_target}"
            )

            if stop_run:
                break

        self.logger.info(
            f"metric.image_quality pass={metrics.image_pass_count} fail={metrics.image_fail_count} reasons={dict(metrics.image_fail_reasons)}"
        )
        self.logger.info(f"run_complete published={published} total_candidates={len(cluster_rows)}")
        await self._emit_event(
            "LOG",
            {
                "message": f"Publishing complete: {published} published from {len(cluster_rows)} candidate stories",
            },
        )
        if published <= 0:
            message = "No articles were published"
            self.logger.error(message)
            await self._emit_event("ERROR", {"message": message})
            raise RuntimeError(message)
        await self._emit_event("RUN_FINISHED", {"published": published, "candidates": len(cluster_rows)})

    def _pick_representative(self, articles: List[object]):
        return max(articles, key=lambda a: len(getattr(a, "body", "") or ""), default=None)

    def _planned_articles_per_run(self) -> int:
        total = 0
        for step in self.publish_plan:
            try:
                total += max(0, int(step.get("total", 0)))
            except Exception:
                continue
        return total

    def _max_articles_per_run(self) -> int:
        configured = max(0, int(getattr(self.settings, "max_articles", 0)))
        planned = self._planned_articles_per_run()
        return max(configured, planned)

    def _effective_publish_targets(
        self,
        total_target: int,
        breaking_target: int,
        active_category_count: int,
        remaining_slots: int = 0,
    ) -> Tuple[int, int]:
        if active_category_count <= 0:
            total_target = 0 if remaining_slots == 0 else min(total_target, remaining_slots)
            return total_target, max(0, min(breaking_target, total_target))
        if remaining_slots > 0:
            if active_category_count == 1:
                total_target = remaining_slots
            else:
                total_target = min(total_target, remaining_slots)
        breaking_target = max(0, min(breaking_target, total_target))
        return total_target, breaking_target

    def _priority_keyword_score(self, text: str, category: str) -> int:
        low = f" {(text or '').lower()} "
        score = 0
        for keyword in PRIORITY_KEYWORD_GROUPS.get("general", []):
            if self._has_priority_keyword(low, keyword):
                score += 1
        category_bucket = "technology" if category == "tech" else category
        for keyword in PRIORITY_KEYWORD_GROUPS.get(category_bucket, []):
            if self._has_priority_keyword(low, keyword):
                score += 3
        return score

    def _has_priority_keyword(self, low_text: str, keyword: str) -> bool:
        token = (keyword or "").strip().lower()
        if not token:
            return False
        if " " in token:
            return token in low_text
        return re.search(rf"\b{re.escape(token)}\b", low_text) is not None

    def _article_priority_score(self, article: object, category: str) -> int:
        text = " ".join(
            str(value or "")
            for value in (
                getattr(article, "title", ""),
                getattr(article, "body", ""),
                getattr(article, "url", ""),
            )
        )
        return self._priority_keyword_score(text, category)

    def _cluster_priority_score(self, cluster: object, category: str) -> int:
        articles = list(getattr(cluster, "articles", []) or [])
        if not articles:
            return 0
        return max(self._article_priority_score(article, category) for article in articles)

    def _cluster_primary_source(self, cluster: object) -> str:
        articles = list(getattr(cluster, "articles", []) or [])
        if not articles:
            return ""
        return str(getattr(articles[0], "source", "") or "").strip().lower()

    def _cluster_story_key(self, cluster: object) -> str:
        story_key = str(getattr(cluster, "story_key", "") or "").strip()
        if story_key:
            return story_key
        title = str(getattr(cluster, "canonical_title", "") or "").strip()
        return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")

    def _story_already_published(self, story_key: str, published_story_keys: set[str]) -> bool:
        key = (story_key or "").strip()
        if not key:
            return False
        if key in published_story_keys:
            return True
        dedupe_hours = int(getattr(self.settings, "story_dedupe_hours", 48))
        return bool(self.memory.is_story_success(key, within_hours=dedupe_hours))

    # ── Intelligent Stemming Table ───────────────────────────────────────
    _STEM_SUFFIXES = [
        ("ies", "y"),     # economies → economy
        ("ves", "f"),     # halves → half
        ("ses", "s"),     # crises → crisis
        ("ches", "ch"),   # launches → launch
        ("shes", "sh"),   # crashes → crash
        ("xes", "x"),     # taxes → tax
        ("zes", "z"),     # buzzes → buzz
        ("ness", ""),     # weakness → weak (approximate)
        ("ment", ""),     # announcement → announce (approximate)
        ("ing", ""),      # leading → lead
        ("tion", ""),     # situation → situa (acceptable for matching)
        ("ed", ""),       # launched → launch
        ("er", ""),       # leader → lead
        ("ly", ""),       # reportedly → reported
        ("es", ""),       # surges → surg
        ("s", ""),        # steps → step
    ]

    def _stem(self, word: str) -> str:
        """Simple rule-based stemmer, no dependencies needed."""
        if len(word) <= 4:
            return word
        for suffix, replacement in self._STEM_SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                return word[: -len(suffix)] + replacement
        return word

    def _title_word_set(self, title: str) -> set[str]:
        """Extract significant stemmed words for fuzzy matching."""
        words = set()
        for token in re.findall(r"[a-z0-9]{3,}", (title or "").lower()):
            if token not in STOPWORDS:
                words.add(self._stem(token))
        return words

    def _title_entities(self, title: str) -> set[str]:
        """Extract proper nouns (capitalized words) — names, places, orgs."""
        entities = set()
        # Find capitalized words that aren't at sentence start
        words = (title or "").split()
        for i, word in enumerate(words):
            clean = re.sub(r"[^a-zA-Z0-9]", "", word)
            if not clean or len(clean) < 2:
                continue
            # Proper noun: starts uppercase and not a common stopword
            if clean[0].isupper() and clean.lower() not in STOPWORDS:
                entities.add(clean.lower())
        return entities

    def _is_title_similar(self, new_title: str) -> bool:
        """Smart similarity: stemmed Jaccard + entity overlap, checks all published titles."""
        new_words = self._title_word_set(new_title)
        new_entities = self._title_entities(new_title)
        if len(new_words) < 2:
            return False

        for published_title in self._published_titles:
            pub_words = self._title_word_set(published_title)
            if len(pub_words) < 2:
                continue

            # Layer 1: Stemmed word Jaccard similarity
            overlap = len(new_words & pub_words)
            union = len(new_words | pub_words)
            word_sim = (overlap / union) if union > 0 else 0.0

            # Layer 2: Entity overlap (proper nouns — much stronger signal)
            pub_entities = self._title_entities(published_title)
            if new_entities and pub_entities:
                entity_overlap = len(new_entities & pub_entities)
                entity_union = len(new_entities | pub_entities)
                entity_sim = (entity_overlap / entity_union) if entity_union > 0 else 0.0
            else:
                entity_sim = 0.0

            # Combined score: entities weigh 2× more than common words
            combined = (word_sim * 0.4) + (entity_sim * 0.6) if entity_sim > 0 else word_sim

            if combined >= 0.60:
                self.logger.info(
                    f"dedup.title_match score={combined:.2f} word_sim={word_sim:.2f} entity_sim={entity_sim:.2f} "
                    f"new={new_title[:50]} vs existing={published_title[:50]}"
                )
                return True

        return False

    def _pop_with_source_backoff(
        self,
        pool: List[Tuple[object, object]],
        source_failures: Dict[str, int],
    ) -> Optional[Tuple[object, object]]:
        if not pool:
            return None

        best_idx = 0
        best_key: Optional[Tuple[int, int]] = None
        for idx, row in enumerate(pool):
            source = self._cluster_primary_source(row[0])
            penalty = source_failures.get(source, 0)
            key = (penalty, idx)
            if best_key is None or key < best_key:
                best_key = key
                best_idx = idx

        return pool.pop(best_idx)

    def _cluster_source_rank(self, cluster: object) -> int:
        articles = list(getattr(cluster, "articles", []) or [])
        if not articles:
            return 3
        source = str(getattr(articles[0], "source", "")).lower()
        if any(tag in source for tag in ("toi", "times of india", "india today")):
            return 0
        if "aljazeera" in source:
            return 1
        if "bbc" in source:
            return 2
        if "ndtv" in source:
            return 5
        return 4

    def _is_article_too_old(self, published_time_str: Optional[str]) -> bool:
        if not published_time_str:
            return bool(self.settings.require_published_time)
        try:
            dt = datetime.fromisoformat(published_time_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
            return age > timedelta(minutes=self.settings.max_article_age_minutes)
        except Exception:
            return bool(self.settings.require_published_time)

    def _is_low_signal_story(self, article, category: str, want_breaking: bool) -> bool:
        if not want_breaking:
            return False

        title = str(getattr(article, "title", "") or "").lower()
        url = str(getattr(article, "url", "") or "").lower()
        text = f" {title} {url} "

        soft_patterns = (
            "holiday",
            "free entry",
            "best timings",
            "miracle garden",
            "shock you",
            "will shock you",
            "in photos",
            "watch:",
            "offers free",
            "how to",
            "review",
        )
        if any(pattern in text for pattern in soft_patterns):
            return True

        if category == "international" and any(pattern in text for pattern in ("eid", "tourist", "garden", "weekend", "break confirmed")):
            return True

        return False

    def _decide_cms_category(self, article, title: str, body: str) -> str:
        hint = str(getattr(article, "category", "")).lower().strip()
        fallback = PIPELINE_TO_CMS_CATEGORY.get(hint, "National")
        source_low = str(getattr(article, "source", "")).lower()
        source_url_low = str(getattr(article, "source_url", "")).lower()
        url_low = str(getattr(article, "url", "")).lower()
        text = f" {title} {body} ".lower()

        source_is_india = any(
            token in source_low
            for token in ("toi", "times of india", "the hindu", "ndtv", "india today", "hindustan times", "indian express")
        )
        source_has_world_section = any(
            marker in source_url_low or marker in url_low
            for marker in ("/world", "/middle-east/", "/rest-of-world/", "/us/", "/europe/", "/asia/", "/africa/")
        )
        url_has_india_news = ("/india/" in url_low) or ("/news/national/" in url_low)
        explicit_india_context = url_has_india_news or any(
            marker in text
            for marker in (
                " india ", " indian ", "new delhi", "delhi", "mumbai", "bengaluru", "kolkata", "chennai",
                "hyderabad", "rajya sabha", "lok sabha", "andhra pradesh", "telangana",
            )
        )
        india_context = explicit_india_context or (source_is_india and not source_has_world_section)
        telangana_signal = any(
            marker in text for marker in ("telangana", "hyderabad", "secunderabad", "warangal", "khammam", "nizamabad")
        )
        andhra_signal = any(
            marker in text for marker in ("andhra pradesh", "amaravati", "visakhapatnam", "vijayawada", "tirupati", "guntur")
        )

        env_signal = any(
            kw in text
            for kw in (
                "environment", "climate", "wildlife", "ecology", "ecological", "conservation", "biodiversity",
                "habitat", "restoration", "forest", "nature", "species",
            )
        )
        tech_signal = any(
            kw in text
            for kw in (
                "technology", " tech ", "artificial intelligence", " ai ", "software", "algorithm", "chip",
                "semiconductor", "digital", "cyber", "creative rights", "copyright",
            )
        )

        decided = self.category_decider.decide(
            title=title,
            body=body,
            source=getattr(article, "source", ""),
            pipeline_hint=hint,
        )
        if not decided:
            decided = fallback

        if hint == "environment" and env_signal:
            return "Environment"
        if hint == "tech" and tech_signal:
            return "Technology"
        if telangana_signal:
            return "Telangana"
        if andhra_signal:
            return "Andhra Pradesh"

        if hint == "international" and source_has_world_section and not explicit_india_context:
            return "International"

        # Keep international pipeline items in International unless they came from India-specific sources/urls.
        if hint == "international" and not india_context:
            return "International"

        if india_context:
            if decided in {"International", "State"}:
                return "National"
            return decided

        if not india_context:
            if env_signal:
                return "Environment"
            if tech_signal:
                return "Technology"
            return "International"

        return decided
    def _build_hashtags(self, category: str, title: str, is_breaking: bool) -> str:
        category_low = category.strip().lower()
        tags: List[str] = []
        if is_breaking and category_low not in {"sports", "entertainment"}:
            tags.append("#breaking")
        elif category_low == "sports":
            tags.append("#trending")

        cat_tag = re.sub(r"\s+", "", category_low)
        if cat_tag:
            tags.append(f"#{cat_tag}")

        words = re.findall(r"[a-zA-Z]{4,}", title.lower())
        keywords = []
        for word in words:
            if word in STOPWORDS:
                continue
            if word not in keywords:
                keywords.append(word)
            if len(keywords) >= 3:
                break

        for kw in keywords:
            tags.append(f"#{kw}")

        deduped = []
        seen = set()
        for tag in tags:
            if tag in seen:
                continue
            seen.add(tag)
            deduped.append(tag)

        hashtag = " ".join(deduped)
        return hashtag[:120]

    def _image_query_terms(self, article, summary_title: str) -> List[str]:
        blob = " ".join(
            value
            for value in (
                summary_title or "",
                getattr(article, "title", "") or "",
                (getattr(article, "body", "") or "")[:320],
            )
            if value
        ).lower()
        terms: List[str] = []
        blocked = STOPWORDS | {"photo", "photos", "image", "images", "video", "report", "reports", "story"}
        for token in re.findall(r"[a-z0-9]{4,}", blob):
            if token in blocked:
                continue
            if token not in terms:
                terms.append(token)
            if len(terms) >= 5:
                break
        return terms

    def _build_image_query(self, article, summary_title: str, category: str) -> str:
        title = (summary_title or getattr(article, "title", "") or "").strip()
        terms = self._image_query_terms(article, title)
        if terms:
            return f"{' '.join(terms)} {category.lower()} news photo"
        if title:
            return f"{title} news photo"
        return f"{category} breaking news photo"

    def _select_fallback_image_url(self, article) -> Optional[str]:
        source_low = str(getattr(article, "source", "") or "").lower()
        if "ndtv" in source_low:
            # NDTV image URLs frequently return 403s in API mode, so avoid wasting publish attempts on them.
            return None

        for raw in [getattr(article, "main_image", ""), getattr(article, "og_image", "")]:
            url = str(raw or "").strip()
            if not url:
                continue
            low = url.lower()
            if not low.startswith("http"):
                continue
            if low.startswith("data:image") or low.endswith(".svg"):
                continue

            # Never bypass branded/overlay URLs via fallback.
            if "ichef.bbci.co.uk/news/" in low and "/branded_news/" in low:
                continue
            if "static.files.bbci.co.uk" in low:
                continue

            try:
                if self.image_pipeline._is_blocked_image_url(url):
                    continue
            except Exception:
                pass

            return url
        return None
    def _build_image_ref(self, local_image_path: Optional[str], selected_image_url: str = "") -> str:
        selected = (selected_image_url or "").strip()
        if selected:
            return selected
        if local_image_path and os.path.exists(local_image_path):
            try:
                with open(local_image_path, "rb") as fh:
                    return f"sha1:{hashlib.sha1(fh.read()).hexdigest()}"
            except Exception:
                return ""
        return ""

    async def _publish_article(self, article, is_breaking: bool, metrics: PipelineMetrics) -> Tuple[bool, str, str, str]:
        if self._is_cancelled():
            return False, "cancelled", "", ""

        if getattr(article, "category", "") == "sports":
            is_breaking = False

        await self._emit_event("STEP_STARTED", {"step": "summarize", "url": article.url})
        summary = self.summarizer.summarize(article.title, article.body, max_retries=(2 if is_breaking else 3))
        await self._emit_event("STEP_DONE", {"step": "summarize", "url": article.url, "ok": bool(summary)})
        if self._is_cancelled():
            return False, "cancelled", "", ""
        if not summary:
            return False, "summary_failed", "", ""

        if self._is_title_similar(article.title):
            self.logger.info(f"skip.duplicate_source_title title={article.title[:60]} url={article.url}")
            return False, "duplicate_title", "", ""
        if self._is_title_similar(summary["title"]):
            self.logger.info(f"skip.duplicate_title title={summary['title'][:60]} url={article.url}")
            return False, "duplicate_title", "", ""

        category = self._decide_cms_category(article, summary["title"], summary["body"])
        image_search_query = self._build_image_query(article, summary["title"], category)

        await self._emit_event("STEP_STARTED", {"step": "image", "url": article.url})
        image_result = self.image_pipeline.select_best(
            article_url=article.url,
            title=article.title,
            fallback_urls=[article.main_image, article.og_image],
            article_context=(article.body or "")[:1200],
        )
        await self._emit_event("STEP_DONE", {"step": "image", "url": article.url, "ok": image_result.passed})
        if self._is_cancelled():
            return False, "cancelled", "", ""

        metrics.record_image_result(
            image_result.passed,
            image_result.rejection_reasons[0] if image_result.rejection_reasons else "",
        )

        local_image_path = image_result.local_path if (image_result.local_path and os.path.exists(image_result.local_path)) else None
        selected_image_url = str(getattr(image_result, "selected_url", "") or "").strip()
        image_ref = self._build_image_ref(local_image_path, selected_image_url)
        if image_ref:
            if image_ref in self._published_image_refs:
                self.logger.info(f"skip.duplicate_image image_ref={image_ref[:80]} url={article.url}")
                return False, "duplicate_image", "", ""
            if self.memory.is_image_used_in_supabase(image_ref):
                self.logger.info(f"skip.duplicate_image image_ref={image_ref[:80]} url={article.url}")
                return False, "duplicate_image", "", ""

        hashtag = self._build_hashtags(category=category, title=summary["title"], is_breaking=is_breaking)
        self.logger.info(f"publish.meta category={category} hashtag={hashtag} image_tier={'local' if local_image_path else 'search'}")

        allow_missing = local_image_path is None
        validation = self.validator.validate(
            english_title=summary["title"],
            english_body=summary["body"],
            category=category,
            image_path=local_image_path,
            hashtag=hashtag,
            image_search_query=image_search_query,
            allow_missing_image=allow_missing,
        )
        if not validation.is_valid:
            self.logger.warning(f"Validation failed for article: {validation.error_message}")
            return False, "validation_failed", "", ""

        data = ArticleData(
            english_title=summary["title"],
            english_body=summary["body"],
            category=category,
            hashtag=hashtag,
            image_path=local_image_path,
            image_search_query=image_search_query if not local_image_path else "",
            needs_image=(local_image_path is None),
            image_url=None,
            image_metadata={
                "quality": image_result.metadata,
                "rejection_reasons": image_result.rejection_reasons,
                "selected_url": selected_image_url,
            },
        )

        await self._emit_event("STEP_STARTED", {"step": "publish", "url": article.url})
        workflow_ok = await self._execute_browser_workflow(data, article.url)
        await self._emit_event("STEP_DONE", {"step": "publish", "url": article.url, "ok": bool(workflow_ok)})
        if self._is_cancelled():
            return False, "cancelled", "", ""
        if workflow_ok:
            summary_title = summary["title"].strip()
            self._published_titles.append(summary_title)
            source_title = str(getattr(article, "title", "") or "").strip()
            if source_title and source_title != summary_title:
                self._published_titles.append(source_title)
            final_image_ref = self._build_image_ref(data.image_path, selected_image_url)
            if final_image_ref:
                self._published_image_refs.add(final_image_ref)
            return True, "", final_image_ref, summary_title
        return False, "workflow_failed", "", ""

    async def _recover_browser_session(self) -> bool:
        if not await self.publisher.ensure_live_page():
            return False
        return await self.publisher.login()

    async def _safe_login(self) -> bool:
        for attempt in range(self.max_login_retries):
            try:
                if await self.publisher.login():
                    return True
                self.logger.warning(f"Login attempt {attempt + 1} failed")
            except Exception as exc:
                self.logger.error(f"Login error: {exc}")
            await asyncio.sleep(2)
        return False

    async def _execute_browser_workflow(self, data: ArticleData, url: str) -> bool:
        if not await self.publisher.create_article():
            if not await self._recover_browser_session():
                return False
            await asyncio.sleep(2)
            if not await self.publisher.create_article():
                return False

        if not await self.publisher.fill_form(data):
            if not await self._recover_browser_session():
                return False
            await asyncio.sleep(2)
            # Retry fill_form after browser recovery instead of silently dropping the article
            if not await self.publisher.fill_form(data):
                return False

        for _attempt in range(self.max_publish_retries):
            # Check for cancellation before crucial step
            if self._is_cancelled():
                self.logger.warning(f"Cancellation requested during browser workflow. url={url}")
                with suppress(Exception):
                    await self.publisher.stop()
                return False
                
            if await self.publisher.publish():
                return True
                
            await asyncio.sleep(2)
            if self.publisher.page is None or self.publisher.page.is_closed():
                if not await self._recover_browser_session():
                    return False
        return False












































Orchestrator = HardenedOrchestrator
