from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Dict, List, Set

from utils.logger import get_logger
from .models import CategoryName, EventCluster, IngestedArticle


class EventResolver:
    _STOPWORDS = {
        "the", "and", "for", "with", "from", "into", "amid", "over", "after", "before", "under", "near",
        "this", "that", "their", "your", "will", "have", "has", "been", "about", "what", "when", "where",
        "which", "today", "live", "update", "updates", "latest", "video", "photos", "photo", "watch", "report",
        "reports", "news", "story", "stories", "says", "said", "say", "here", "why", "new", "still", "just",
        "more", "than", "until", "till", "continue", "continues", "continuing",
    }
    _GENERIC_NEWS_TOKENS = {
        "official", "officials", "minister", "president", "government", "leader", "leaders", "country",
        "countries", "people", "person", "issue", "issues", "move", "moves", "plan", "plans", "meeting",
        "talk", "talks", "warning", "warn", "warns", "threat", "threats", "major", "fresh",
    }
    _TOKEN_CANONICAL = {
        "attacks": "strike",
        "attack": "strike",
        "airstrikes": "strike",
        "airstrike": "strike",
        "strikes": "strike",
        "strike": "strike",
        "postpones": "delay",
        "postponed": "delay",
        "postpone": "delay",
        "delays": "delay",
        "delayed": "delay",
        "threatened": "threat",
        "threatens": "threat",
        "threatening": "threat",
        "iranian": "iran",
        "israeli": "israel",
        "lebanese": "lebanon",
        "american": "us",
    }

    def __init__(self, title_similarity: float = 0.78, content_similarity: float = 0.45, time_window_minutes: int = 180):
        self.title_similarity = title_similarity
        self.content_similarity = content_similarity
        self.time_window = timedelta(minutes=time_window_minutes)
        self.logger = get_logger("event_resolver")

    def cluster(self, articles: List[IngestedArticle]) -> List[EventCluster]:
        clusters: List[List[IngestedArticle]] = []

        for article in articles:
            best_idx = -1
            best_score = 0.0
            for idx, cluster_articles in enumerate(clusters):
                score = self._cluster_match_score(article, cluster_articles)
                if score > best_score:
                    best_idx = idx
                    best_score = score
            if best_idx >= 0:
                clusters[best_idx].append(article)
            else:
                clusters.append([article])

        out: List[EventCluster] = []
        for idx, group in enumerate(clusters, start=1):
            source_groups: Dict[str, List[IngestedArticle]] = {}
            for item in group:
                source_groups.setdefault(item.source, []).append(item)

            dominant_category = self._dominant_category(group)
            start = min(self._parse_time(a) for a in group)
            end = max(self._parse_time(a) for a in group)
            canonical_title = max(group, key=lambda a: len(a.title)).title
            story_key = self._cluster_story_key(group)
            cluster_id = hashlib.sha1(f"{idx}:{self._normalize(canonical_title)}".encode("utf-8")).hexdigest()[:12]

            out.append(
                EventCluster(
                    id=cluster_id,
                    canonical_title=canonical_title,
                    articles=group,
                    source_groups=source_groups,
                    start_time=start,
                    end_time=end,
                    dominant_category=dominant_category,
                    story_key=story_key,
                )
            )

        self.logger.info(f"Clusters formed: {len(out)}")
        return out

    def _cluster_match_score(self, article: IngestedArticle, cluster_articles: List[IngestedArticle]) -> float:
        best = 0.0
        for existing in cluster_articles:
            score = self._story_match_score(article, existing)
            if score > best:
                best = score
        return best

    def _is_same_story(self, a: IngestedArticle, b: IngestedArticle) -> bool:
        return self._story_match_score(a, b) > 0.0

    def _story_match_score(self, a: IngestedArticle, b: IngestedArticle) -> float:
        if not self._within_time_window(a, b):
            return 0.0

        title_ratio = SequenceMatcher(None, self._normalize(a.title), self._normalize(b.title)).ratio()
        content_ratio = self._jaccard(self._normalize(a.body), self._normalize(b.body))
        title_overlap = self._token_jaccard(self._title_features(a), self._title_features(b))
        lead_overlap = self._token_jaccard(self._lead_features(a), self._lead_features(b))
        story_overlap = self._token_jaccard(self._story_features(a), self._story_features(b))
        numbers_compatible = self._numbers_compatible(a, b)

        if title_ratio >= self.title_similarity:
            return title_ratio
        if title_overlap >= 0.52 and (lead_overlap >= 0.22 or content_ratio >= self.content_similarity * 0.7) and numbers_compatible:
            return max(title_overlap, story_overlap, content_ratio)
        if lead_overlap >= 0.38 and story_overlap >= 0.30 and numbers_compatible:
            return max(lead_overlap, story_overlap)
        if story_overlap >= 0.42 and lead_overlap >= 0.24 and numbers_compatible:
            return max(story_overlap, lead_overlap)
        if title_ratio >= self.title_similarity * 0.82 and content_ratio >= self.content_similarity:
            return max(title_ratio, content_ratio)
        # Content-only match: different headlines, nearly identical body text
        if content_ratio >= 0.55 and numbers_compatible:
            return content_ratio
        return 0.0

    def _within_time_window(self, a: IngestedArticle, b: IngestedArticle) -> bool:
        delta = abs(self._parse_time(a) - self._parse_time(b))
        return delta <= self.time_window

    def _normalize(self, text: str) -> str:
        text = text.lower()
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _canonical_token(self, token: str) -> str:
        token = (token or "").lower().strip()
        if not token:
            return ""
        token = self._TOKEN_CANONICAL.get(token, token)
        if token.endswith("ies") and len(token) > 5:
            token = f"{token[:-3]}y"
        elif token.endswith("ing") and len(token) > 6:
            token = token[:-3]
        elif token.endswith("ed") and len(token) > 5:
            token = token[:-2]
        elif token.endswith("es") and len(token) > 5:
            token = token[:-2]
        elif token.endswith("s") and len(token) > 4:
            token = token[:-1]
        return self._TOKEN_CANONICAL.get(token, token)

    def _salient_tokens(self, text: str) -> List[str]:
        raw_tokens = self._normalize(text).split()
        tokens: List[str] = []
        for raw in raw_tokens:
            token = self._canonical_token(raw)
            if len(token) < 3:
                continue
            if token in self._STOPWORDS or token in self._GENERIC_NEWS_TOKENS:
                continue
            tokens.append(token)
        return tokens

    def _token_bigrams(self, tokens: List[str]) -> Set[str]:
        return {f"{tokens[idx]} {tokens[idx + 1]}" for idx in range(len(tokens) - 1)}

    def _lead_text(self, body: str) -> str:
        clean = " ".join((body or "").split())
        if not clean:
            return ""
        parts = re.split(r"(?<=[.!?])\s+", clean)
        return " ".join(parts[:2])[:320]

    def _title_features(self, article: IngestedArticle) -> Set[str]:
        tokens = self._salient_tokens(article.title)
        return set(tokens)

    def _lead_features(self, article: IngestedArticle) -> Set[str]:
        tokens = self._salient_tokens(self._lead_text(article.body))
        return set(tokens)

    def _story_features(self, article: IngestedArticle) -> Set[str]:
        title_tokens = self._salient_tokens(article.title)
        lead_tokens = self._salient_tokens(self._lead_text(article.body))
        return set(title_tokens + lead_tokens) | self._token_bigrams(title_tokens) | self._token_bigrams(lead_tokens)

    def _token_jaccard(self, a: Set[str], b: Set[str]) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    def _number_tokens(self, text: str) -> Set[str]:
        return set(re.findall(r"\b\d+\b", self._normalize(text)))

    def _numbers_compatible(self, a: IngestedArticle, b: IngestedArticle) -> bool:
        nums_a = self._number_tokens(f"{a.title} {self._lead_text(a.body)}")
        nums_b = self._number_tokens(f"{b.title} {self._lead_text(b.body)}")
        if not nums_a or not nums_b:
            return True
        return bool(nums_a & nums_b)

    def _cluster_story_key(self, group: List[IngestedArticle]) -> str:
        feature_scores: Dict[str, int] = {}
        for article in group:
            title_tokens = self._salient_tokens(article.title)
            lead_tokens = self._salient_tokens(self._lead_text(article.body))
            for token in title_tokens:
                feature_scores[token] = feature_scores.get(token, 0) + 3
            for token in lead_tokens:
                feature_scores[token] = feature_scores.get(token, 0) + 1
            for phrase in self._token_bigrams(title_tokens):
                feature_scores[phrase] = feature_scores.get(phrase, 0) + 4

        ranked = sorted(
            feature_scores.items(),
            key=lambda row: (-row[1], 0 if " " in row[0] else 1, -len(row[0]), row[0]),
        )
        picked = [feature for feature, _ in ranked[:8]]
        if not picked:
            fallback = self._normalize(max(group, key=lambda item: len(item.title)).title)
            return hashlib.sha1(fallback.encode("utf-8")).hexdigest()[:16]
        base = "|".join(sorted(picked))
        return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]

    def _jaccard(self, a: str, b: str) -> float:
        set_a = set(a.split())
        set_b = set(b.split())
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)

    def _parse_time(self, article: IngestedArticle) -> datetime:
        raw = (article.published_time or "").strip()
        if raw:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                pass
        return article.ingested_at

    def _dominant_category(self, items: List[IngestedArticle]) -> CategoryName:
        counts: Dict[CategoryName, int] = {}
        for item in items:
            counts[item.category] = counts.get(item.category, 0) + 1
        return max(counts.items(), key=lambda row: row[1])[0]
