"""Multi-agent orchestrator with event resolution, category intelligence and image quality checks."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from config.settings import get_settings
from core.cms import ArticleData, CMSPublisher
from core.intelligence import CategoryDecider, Summarizer, TeluguWriter
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


class HardenedOrchestrator:
    def __init__(self):
        self.logger = get_logger("orchestrator")
        self.settings = get_settings()

        self.publisher = CMSPublisher()
        self.summarizer = Summarizer()
        self.telugu_writer = TeluguWriter()
        self.category_decider = CategoryDecider()
        self.validator = ArticleValidator()
        self.memory = AgentMemory()

        self.ingestion = MultiAgentIngestion(
            category_sources=self.settings.category_sources,
            max_links_per_source=self.settings.max_links_per_source,
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

        self.max_publish_retries = 2
        self.max_login_retries = 2
        self.max_consecutive_publish_failures = 10
        self._published_titles: set = set()
        self._cancel_check: Optional[Callable[[], bool]] = None
        self._event_sink: Optional[Callable[[str, Dict[str, Any]], Awaitable[None] | None]] = None

    def set_runtime_controls(
        self,
        cancel_check: Optional[Callable[[], bool]] = None,
        event_sink: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    ) -> None:
        """Allow the worker to inject cancel checking and event reporting."""
        self._cancel_check = cancel_check
        self._event_sink = event_sink

    def _is_cancelled(self) -> bool:
        if self._cancel_check is not None:
            return self._cancel_check()
        return False

    async def _emit_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self._event_sink is None:
            return
        try:
            result = self._event_sink(event_type, payload)
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
            if not await self._safe_login():
                self.logger.critical("Login failed after retries")
                return

            if self.settings.scheduler_enabled:
                while True:
                    await self._run_once()
                    await asyncio.sleep(self.settings.scheduler_interval_minutes * 60)
            else:
                await self._run_once()
        finally:
            await self.publisher.stop()

    async def _run_once(self) -> None:
        metrics = PipelineMetrics()

        await self._emit_event("SCRAPE_STARTED", {})
        by_category = self.ingestion.run()
        metrics.record_category_counts(by_category)
        total_scraped = 0
        for cat, count in metrics.total_scraped_per_category.items():
            self.logger.info(f"metric.scraped category={cat} count={count}")
            total_scraped += count
        await self._emit_event("SCRAPE_DONE", {"total": total_scraped})

        all_articles = [item for rows in by_category.values() for item in rows]
        clusters = self.resolver.cluster(all_articles)
        metrics.clusters_formed = len(clusters)
        self.logger.info(f"metric.clusters_formed count={metrics.clusters_formed}")

        cluster_rows: List[Tuple[object, object]] = []
        for cluster in clusters:
            decision = self.breaking.classify(cluster)
            if decision.is_breaking:
                metrics.breaking_news_count += 1
            cluster_rows.append((cluster, decision))

        self.logger.info(f"metric.breaking_news_count count={metrics.breaking_news_count}")

        clusters_by_category: Dict[str, List[Tuple[object, object]]] = {}
        for cluster, decision in cluster_rows:
            category = str(getattr(cluster, "dominant_category", "")).strip().lower()
            if not category:
                continue
            clusters_by_category.setdefault(category, []).append((cluster, decision))

        published = 0
        consecutive_publish_failures = 0
        stop_run = False

        for step in self.publish_plan:
            category = str(step["category"])
            total_target = int(step["total"])
            breaking_target = int(step["breaking_target"])

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
                    float(getattr(row[1], "confidence", 0.0)),
                    getattr(row[0], "end_time", datetime.min.replace(tzinfo=timezone.utc)),
                ),
                reverse=True,
            )
            normal_pool.sort(
                key=lambda row: getattr(row[0], "end_time", datetime.min.replace(tzinfo=timezone.utc)),
                reverse=True,
            )
            # Prefer sources with better image hit-rate before BBC-heavy picks.
            breaking_pool.sort(key=lambda row: self._cluster_source_rank(row[0]))
            normal_pool.sort(key=lambda row: self._cluster_source_rank(row[0]))

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
                cluster_source = self._cluster_primary_source(cluster)
                cluster_articles = list(getattr(cluster, "articles", []) or [])
                cluster_articles.sort(key=lambda a: len(getattr(a, "body", "") or ""), reverse=True)
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
                        await self._emit_event("RUN_FINISHED", {"status": "cancelled"})
                        return
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

                    ok, fail_reason = await self._publish_article(article, selected_is_breaking, metrics)
                    if ok:
                        cat_published += 1
                        published += 1
                        if selected_is_breaking:
                            cat_breaking_published += 1
                        consecutive_publish_failures = 0
                        self.memory.mark_success(article_url)
                        published_from_cluster = True
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
                            f"Stopping run after {consecutive_publish_failures} consecutive publish failures to avoid a retry loop"
                        )
                        stop_run = True
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
        await self._emit_event("RUN_FINISHED", {"status": "completed", "published": published})

    def _pick_representative(self, articles: List[object]):
        return max(articles, key=lambda a: len(getattr(a, "body", "") or ""), default=None)

    def _cluster_primary_source(self, cluster: object) -> str:
        articles = list(getattr(cluster, "articles", []) or [])
        if not articles:
            return ""
        return str(getattr(articles[0], "source", "") or "").strip().lower()

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
            return False
        try:
            dt = datetime.fromisoformat(published_time_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
            return age > timedelta(hours=self.settings.max_article_age_hours)
        except Exception:
            return False

    def _decide_cms_category(self, article, title: str, body: str) -> str:
        hint = str(getattr(article, "category", "")).lower().strip()
        fallback = PIPELINE_TO_CMS_CATEGORY.get(hint, "National")
        source_low = str(getattr(article, "source", "")).lower()
        url_low = str(getattr(article, "url", "")).lower()
        text = f" {title} {body} ".lower()

        source_is_india = any(token in source_low for token in ("toi", "times of india", "the hindu"))
        url_has_india_news = ("/india/" in url_low) or ("/news/national/" in url_low)
        india_context = source_is_india or url_has_india_news or any(
            marker in text
            for marker in (
                " india ", " indian ", "new delhi", "delhi", "mumbai", "bengaluru", "kolkata", "chennai",
                "hyderabad", "rajya sabha", "lok sabha", "andhra pradesh", "telangana",
            )
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

        # Keep international pipeline items in International unless they came from India-specific sources/urls.
        if hint == "international" and not india_context:
            return "International"

        if india_context:
            if decided in {"International", "State"}:
                return "National"
            return decided

        # For non-India context, avoid National/State leakage.
        if decided in {"National", "State", "Telangana", "Andhra Pradesh"}:
            if env_signal:
                return "Environment"
            if tech_signal:
                return "Technology"
            return "International"

        if env_signal and decided in {"International", "Lifestyle"}:
            return "Environment"
        if tech_signal and decided in {"Politics", "International", "National"}:
            return "Technology"

        return decided
    def _build_hashtags(self, category: str, title: str, is_breaking: bool) -> str:
        tags = ["#breaking", "#news"] if is_breaking else ["#news", "#trending"]

        cat_tag = re.sub(r"\s+", "", category.strip().lower())
        if cat_tag:
            tags.append(f"#{cat_tag}")

        words = re.findall(r"[a-zA-Z]{4,}", title.lower())
        keywords = []
        for word in words:
            if word in STOPWORDS:
                continue
            if word not in keywords:
                keywords.append(word)
            if len(keywords) >= 2:
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

    def _build_image_query(self, article, summary_title: str, category: str) -> str:
        source = str(getattr(article, "source", "")).strip()
        title = (summary_title or getattr(article, "title", "") or "").strip()
        if source and title:
            return f"{title} {source} news photo"
        if title:
            return f"{title} news photo"
        return f"{category} breaking news photo"

    def _select_fallback_image_url(self, article) -> Optional[str]:
        source_low = str(getattr(article, "source", "") or "").lower()
        if any(token in source_low for token in ("bbc", "ndtv", "india today")):
            # These sources often expose branded, blocked, or low-quality CDN assets in fallback mode.
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
    async def _publish_article(self, article, is_breaking: bool, metrics: PipelineMetrics) -> Tuple[bool, str]:
        if self._is_cancelled():
            return False, "cancelled"

        await self._emit_event("STEP_STARTED", {"step": "summarize", "url": article.url})
        summary = self.summarizer.summarize(article.title, article.body)
        await self._emit_event("STEP_DONE", {"step": "summarize", "url": article.url, "ok": bool(summary)})
        if not summary:
            return False, "summary_failed"

        if self._is_cancelled():
            return False, "cancelled"

        await self._emit_event("STEP_STARTED", {"step": "telugu", "url": article.url})
        telugu = self.telugu_writer.write(summary["title"], summary["body"])
        await self._emit_event("STEP_DONE", {"step": "telugu", "url": article.url, "ok": bool(telugu)})
        if not telugu:
            return False, "telugu_failed"

        if self._is_cancelled():
            return False, "cancelled"

        await self._emit_event("STEP_STARTED", {"step": "image", "url": article.url})
        image_result = self.image_pipeline.select_best(
            article_url=article.url,
            title=article.title,
            fallback_urls=[article.main_image, article.og_image],
            article_context=(article.body or "")[:1200],
        )

        metrics.record_image_result(image_result.passed, image_result.rejection_reasons[0] if image_result.rejection_reasons else "")
        await self._emit_event("STEP_DONE", {"step": "image", "url": article.url, "ok": bool(image_result.passed)})

        if not image_result.passed and not image_result.needs_image:
            self.logger.warning(
                f"Skipping publish due to missing valid image. url={article.url} reasons={image_result.rejection_reasons}"
            )
            return False, "image_missing"

        if not image_result.local_path and not image_result.selected_url:
            fallback_url = self._select_fallback_image_url(article)
            if fallback_url:
                image_result.selected_url = fallback_url
                self.logger.warning(
                    f"Image quality pipeline returned no file; using source image URL fallback. url={article.url}"
                )
            else:
                self.logger.warning(
                    f"Skipping publish because no usable image path/url is available. url={article.url}"
                )
                return False, "image_missing"

        category = self._decide_cms_category(article, summary["title"], summary["body"])
        hashtag = self._build_hashtags(category=category, title=summary["title"], is_breaking=is_breaking)
        image_search_query = self._build_image_query(article, summary["title"], category)
        self.logger.info(f"publish.meta category={category} hashtag={hashtag}")

        validation = self.validator.validate(
            english_title=summary["title"],
            english_body=summary["body"],
            telugu_title=telugu["title"],
            telugu_body=telugu["body"],
            category=category,
            image_path=image_result.local_path,
            hashtag=hashtag,
            image_search_query=image_search_query,
            allow_missing_image=True,
        )
        if not validation.is_valid:
            self.logger.warning(f"Validation failed for article: {validation.error_message}")
            return False, "validation_failed"

        data = ArticleData(
            english_title=summary["title"],
            english_body=summary["body"],
            telugu_title=telugu["title"],
            telugu_body=telugu["body"],
            category=category,
            hashtag=hashtag,
            image_path=image_result.local_path,
            image_search_query=image_search_query,
            needs_image=image_result.needs_image,
            image_url=image_result.selected_url,
            image_metadata={
                "quality": image_result.metadata,
                "rejection_reasons": image_result.rejection_reasons,
            },
        )

        if self._is_cancelled():
            return False, "cancelled"
        await self._emit_event("STEP_STARTED", {"step": "publish", "url": article.url})
        workflow_ok = await self._execute_browser_workflow(data, article.url)
        await self._emit_event("STEP_DONE", {"step": "publish", "url": article.url, "ok": bool(workflow_ok)})
        if workflow_ok:
            return True, ""
        return False, "workflow_failed"
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
            if self.publisher.page:
                await self.publisher.page.reload()
            await asyncio.sleep(2)
            if not await self.publisher.create_article():
                return False

        if not await self.publisher.fill_form(data):
            if self.publisher.page:
                await self.publisher.page.reload()
            await asyncio.sleep(2)
            return False

        for _attempt in range(self.max_publish_retries):
            if await self.publisher.publish():
                return True
            await asyncio.sleep(2)
        return False


Orchestrator = HardenedOrchestrator




































