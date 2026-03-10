from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Dict, List

from utils.logger import get_logger
from .models import EventCluster, IngestedArticle, CategoryName


class EventResolver:
    def __init__(self, title_similarity: float = 0.78, content_similarity: float = 0.45, time_window_minutes: int = 180):
        self.title_similarity = title_similarity
        self.content_similarity = content_similarity
        self.time_window = timedelta(minutes=time_window_minutes)
        self.logger = get_logger("event_resolver")

    def cluster(self, articles: List[IngestedArticle]) -> List[EventCluster]:
        clusters: List[List[IngestedArticle]] = []

        for article in articles:
            placed = False
            for cluster_articles in clusters:
                # Check against ALL articles in cluster, not just first
                if any(self._is_same_story(article, existing) for existing in cluster_articles):
                    cluster_articles.append(article)
                    placed = True
                    break
            if not placed:
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
                )
            )

        self.logger.info(f"Clusters formed: {len(out)}")
        return out

    def _is_same_story(self, a: IngestedArticle, b: IngestedArticle) -> bool:
        if not self._within_time_window(a, b):
            return False

        title_ratio = SequenceMatcher(None, self._normalize(a.title), self._normalize(b.title)).ratio()
        content_ratio = self._jaccard(self._normalize(a.body), self._normalize(b.body))

        # Strong title match
        if title_ratio >= self.title_similarity:
            return True
        # Moderate title + content match
        if title_ratio >= self.title_similarity * 0.88 and content_ratio >= self.content_similarity:
            return True
        # Content-only match (same story, different headline)
        if content_ratio >= 0.55:
            return True
        return False

    def _within_time_window(self, a: IngestedArticle, b: IngestedArticle) -> bool:
        delta = abs(self._parse_time(a) - self._parse_time(b))
        return delta <= self.time_window

    def _normalize(self, text: str) -> str:
        text = text.lower()
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

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
