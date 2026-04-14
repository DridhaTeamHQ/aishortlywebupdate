from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from utils.logger import get_logger
from .models import CategoryName, IngestedArticle

from core.sources.timesofindia import TimesOfIndiaScraper
from core.sources.ndtv import NDTVScraper
from core.sources.indiatoday import IndiaTodayScraper
from core.sources.bbc import BBCScraper
from core.sources.reuters import ReutersScraper
from core.sources.aljazeera import AlJazeeraScraper
from core.sources.thehindu import TheHinduScraper


SCRAPER_REGISTRY: Dict[str, Callable[[], Any]] = {
    "toi": TimesOfIndiaScraper,
    "ndtv": NDTVScraper,
    "indiatoday": IndiaTodayScraper,
    "bbc": BBCScraper,
    "reuters": ReutersScraper,
    "aljazeera": AlJazeeraScraper,
    "thehindu": TheHinduScraper,
}

CATEGORY_KEYWORDS: Dict[CategoryName, List[str]] = {
    "business": [
        "economy", "market", "stock", "business", "company", "trade", "finance", "inflation", "gdp",
    ],
    "tech": [
        "technology", "tech", "ai", "artificial intelligence", "software", "chip", "startup", "internet", "cyber", "semiconductor",
        "algorithm", "digital", "platform", "machine learning", "copyright", "creative rights",
    ],
    "international": [
        "war", "conflict", "diplomatic", "sanction", "missile", "border", "ceasefire", "united nations", "nato",
    ],
    "national": [
        "india", "indian", "new delhi", "ministry", "government of india",
    ],
    "environment": [
        "climate", "environment", "pollution", "wildlife", "forest", "emission", "biodiversity", "ecology", "conservation", "habitat", "nature",
    ],
    "crime": [
        "crime", "police", "murder", "arrest", "fraud", "court", "investigation", "assault",
    ],
    "sports": [
        "sport", "sports", "match", "tournament", "league", "cup", "goal", "coach", "player", "cricket", "football", "tennis",
    ],
}

CATEGORY_SOURCE_HINTS: Dict[CategoryName, List[str]] = {
    "business": ["/business", "markets", "economy", "finance"],
    "tech": ["/technology", "/tech", "science"],
    "international": ["/world", "world-news", "international", "middle-east", "europe", "asia", "africa"],
    "national": ["/india", "/news/national", "nation"],
    "environment": ["environment", "climate", "sustainability"],
    "crime": ["crime", "police", "law-order", "courts"],
    "sports": ["/sport", "/sports", "cricket", "football", "tennis", "olympic"],
}

INDIA_MARKERS = [
    "india", "indian", "new delhi", "delhi", "mumbai", "bengaluru", "kolkata", "hyderabad", "chennai",
    "rajya sabha", "lok sabha", "bihar", "telangana", "andhra pradesh", "west bengal", "maharashtra", "karnataka",
]

INTERNATIONAL_MARKERS = [
    "war", "conflict", "missile", "uav", "airstrike", "air strike", "drone", "missiles", "nato", "united nations",
    "middle east", "iran", "russia", "israel", "ukraine", "china", "afghanistan", "pakistan", "asia", "europe", "uk", "england", "britain", "london", "norfolk",
    "africa", "diplomatic", "embassy", "foreign", "global", "international", "summit", "g20", "tariff", "attack",
    "bombing", "explosion", "fleet", "navy", "air force",
]

MIN_TITLE_CHARS = 16
MIN_BODY_CHARS = 120
IRRELEVANT_TEXT_TOKENS = (
    "advertisement",
    "subscribe to",
    "follow us on",
    "click here",
    "read more",
)
LOW_VALUE_URL_PATTERNS = (
    "/authors/",
    "/author/",
    "/opinion/",
    "/opinions/",
    "/education/",
    "/live-updates/",
    "/live-blog/",
    "/liveblog/",
)
LOW_VALUE_TITLE_PATTERNS = (
    "opinion |",
    "live:",
    "live updates",
    "how to download",
    "when and how to",
    "check official websites",
    "view result",
    "result to be out",
    "trailer",
    "box office",
    "dating",
    "relationship",
    "wife",
    "husband",
    "girlfriend",
    "boyfriend",
)


def _is_international_source(source_name: str) -> bool:
    source = (source_name or "").strip().lower()
    return any(token in source for token in ("bbc", "reuters", "aljazeera", "cnn", "ap", "reuterswire"))


def _is_local_india_politics(text: str) -> bool:
    local_markers = [
        "rajya sabha", "lok sabha", "chief minister", "cabinet", "parliament", "state", "assembly", "election", "governor",
    ]
    return any(marker in text for marker in local_markers)


@dataclass
class SourceConfig:
    name: str
    scraper: str
    url: str


class CategoryAgent:
    def __init__(
        self,
        category: CategoryName,
        sources: List[SourceConfig],
        max_links_per_source: int = 8,
        shared_cache: Optional[Dict[str, List[Dict[str, object]]]] = None,
        max_article_age_minutes: int = 30,
        require_published_time: bool = True,
    ):
        self.category = category
        self.sources = sources
        self.max_links_per_source = max_links_per_source
        self.shared_cache = shared_cache
        self.max_article_age_minutes = max_article_age_minutes
        self.require_published_time = require_published_time
        self.logger = get_logger(f"agent_{category}")

    def _parse_published_time(self, published_time: Optional[str]) -> Optional[datetime]:
        if not published_time:
            return None
        try:
            dt = datetime.fromisoformat(str(published_time).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _is_fresh_article(self, article: IngestedArticle) -> bool:
        published_dt = self._parse_published_time(article.published_time)
        if published_dt is None:
            return not self.require_published_time
        age = datetime.now(timezone.utc) - published_dt
        return age <= timedelta(minutes=self.max_article_age_minutes)

    def run(self) -> List[IngestedArticle]:
        collected: List[IngestedArticle] = []
        skipped_stale = 0
        for source in self.sources:
            scraper_factory = SCRAPER_REGISTRY.get(source.scraper)
            if not scraper_factory:
                self.logger.warning(f"Unknown scraper '{source.scraper}' for source {source.name}")
                continue

            rows = self._fetch_source_rows(source, scraper_factory)
            for row in rows:
                ingested = IngestedArticle(
                    category=self.category,
                    source=str(row.get("source", "")),
                    source_url=str(row.get("source_url", "")),
                    url=str(row.get("url", "")),
                    title=str(row.get("title", "")),
                    body=str(row.get("body", "")),
                    published_time=row.get("published_time"),
                    og_image=row.get("og_image"),
                    main_image=row.get("main_image"),
                )
                if not self._is_fresh_article(ingested):
                    skipped_stale += 1
                    continue
                if self._matches_category(ingested):
                    collected.append(ingested)

        self.logger.info(
            f"Category {self.category}: scraped {len(collected)} fresh articles, skipped_stale={skipped_stale}"
        )
        return collected

    def _fetch_source_rows(self, source: SourceConfig, scraper_factory: Callable[[], Any]) -> List[Dict[str, object]]:
        cache_key = f"{source.scraper}|{source.url}|{self.max_links_per_source}"
        if self.shared_cache is not None and cache_key in self.shared_cache:
            return self.shared_cache[cache_key]

        rows: List[Dict[str, object]] = []
        scraper = scraper_factory()
        try:
            if hasattr(scraper, "NEWS_URL"):
                setattr(scraper, "NEWS_URL", source.url)

            links = scraper.get_article_links(limit=self.max_links_per_source)
            for url in links:
                article = scraper.scrape_article(url)
                if not article:
                    continue

                rows.append(
                    {
                        "source": source.name,
                        "source_url": source.url,
                        "url": getattr(article, "url", url),
                        "title": getattr(article, "title", ""),
                        "body": getattr(article, "body", ""),
                        "published_time": getattr(article, "published_time", None),
                        "og_image": getattr(article, "og_image", None),
                        "main_image": getattr(article, "main_image", None),
                    }
                )
        except Exception as exc:
            self.logger.error(f"{source.name} scraping failed for {self.category}: {exc}")
        finally:
            try:
                scraper.close()
            except Exception:
                pass

        if self.shared_cache is not None:
            self.shared_cache[cache_key] = rows

        return rows

    def _has_keyword(self, text: str, keyword: str) -> bool:
        k = keyword.strip().lower()
        if not k:
            return False
        if " " in k:
            return k in text
        return re.search(rf"\b{re.escape(k)}\b", text) is not None

    def _is_international_theme(self, text: str) -> bool:
        if any(self._has_keyword(text, marker) for marker in INTERNATIONAL_MARKERS):
            return True
        if self._has_keyword(text, "us") and self._has_keyword(text, "israel"):
            return True
        return False

    def _source_url_matches_category(self, source_url: str, category: CategoryName) -> bool:
        low = (source_url or "").lower()
        hints = CATEGORY_SOURCE_HINTS.get(category, [])
        return any(h in low for h in hints)

    def _category_signal_score(self, text: str, category: CategoryName) -> int:
        keywords = CATEGORY_KEYWORDS.get(category, [])
        return sum(1 for kw in keywords if self._has_keyword(text, kw))

    def _is_content_relevant(self, article: IngestedArticle) -> bool:
        title = " ".join((article.title or "").split())
        body = " ".join((article.body or "").split())
        url = str(article.url or "").lower()
        if len(title) < MIN_TITLE_CHARS:
            return False
        if len(body) < MIN_BODY_CHARS:
            return False

        low = f"{title} {body}".lower()
        if any(token in low for token in IRRELEVANT_TEXT_TOKENS):
            return False
        if any(pattern in url for pattern in LOW_VALUE_URL_PATTERNS):
            return False
        if any(pattern in low for pattern in LOW_VALUE_TITLE_PATTERNS):
            return False
        return True

    def _matches_category(self, article: IngestedArticle) -> bool:
        if not self._is_content_relevant(article):
            return False

        content_text = f"{article.title} {article.body}".lower()
        text = f"{content_text} {article.url}".lower()
        source_low = str(article.source or "").lower()
        source_url_low = str(article.source_url or "").lower()

        is_international_source = _is_international_source(source_low)
        is_india_source = any(
            token in source_low for token in ("toi", "times of india", "ndtv", "india today", "the hindu")
        )
        has_india_marker = any(marker in text for marker in INDIA_MARKERS)
        is_local_politics = _is_local_india_politics(text)
        is_international_theme = self._is_international_theme(text)

        if self.category == "international":
            if self._source_url_matches_category(source_url_low, "national") and not is_international_theme:
                return False
            if has_india_marker and not is_international_theme and not is_international_source:
                return False
            if is_international_theme:
                return True
            if self._source_url_matches_category(source_url_low, "international") and not (has_india_marker and is_local_politics):
                return True
            if is_international_source and not (has_india_marker and is_local_politics):
                return True
            return any(self._has_keyword(content_text, kw) for kw in CATEGORY_KEYWORDS["international"])

        if self.category == "national":
            if self._source_url_matches_category(source_url_low, "national"):
                if is_international_theme and not is_local_politics:
                    return False
                return has_india_marker or self._category_signal_score(text, "national") > 0
            if is_india_source or has_india_marker:
                if is_international_theme and not is_local_politics:
                    return False
                return True
            return any(self._has_keyword(content_text, kw) for kw in CATEGORY_KEYWORDS["national"])

        if self.category == "environment":
            signal = self._category_signal_score(content_text, "environment")
            return (self._source_url_matches_category(source_url_low, "environment") and signal > 0) or any(
                self._has_keyword(content_text, kw) for kw in CATEGORY_KEYWORDS["environment"]
            )

        if self.category == "sports":
            signal = self._category_signal_score(content_text, "sports")
            return (self._source_url_matches_category(source_url_low, "sports") and signal > 0) or any(
                self._has_keyword(content_text, kw) for kw in CATEGORY_KEYWORDS["sports"]
            )

        if self.category == "crime":
            signal = self._category_signal_score(content_text, "crime")
            return (self._source_url_matches_category(source_url_low, "crime") and signal > 0) or any(
                self._has_keyword(content_text, kw) for kw in CATEGORY_KEYWORDS["crime"]
            )

        if self.category == "tech":
            signal = self._category_signal_score(content_text, "tech")
            return (self._source_url_matches_category(source_url_low, "tech") and signal > 0) or any(
                self._has_keyword(content_text, kw) for kw in CATEGORY_KEYWORDS["tech"]
            )

        if self.category == "business":
            signal = self._category_signal_score(content_text, "business")
            return (self._source_url_matches_category(source_url_low, "business") and signal > 0) or any(
                self._has_keyword(content_text, kw) for kw in CATEGORY_KEYWORDS["business"]
            )

        self.logger.warning(f"No keyword profile for category '{self.category}', defaulting to False")
        return False


class MultiAgentIngestion:
    def __init__(
        self,
        category_sources: Dict[CategoryName, List[Dict[str, str]]],
        max_links_per_source: int = 8,
        max_article_age_minutes: int = 30,
        require_published_time: bool = True,
    ):
        self.logger = get_logger("multi_agent_ingestion")
        self.category_sources = category_sources
        self.max_links_per_source = max_links_per_source
        self.max_article_age_minutes = max_article_age_minutes
        self.require_published_time = require_published_time

    def run(self) -> Dict[CategoryName, List[IngestedArticle]]:
        by_category: Dict[CategoryName, List[IngestedArticle]] = {}
        shared_cache: Dict[str, List[Dict[str, object]]] = {}

        for category, source_rows in self.category_sources.items():
            sources = [SourceConfig(**row) for row in source_rows]
            agent = CategoryAgent(
                category=category,
                sources=sources,
                max_links_per_source=self.max_links_per_source,
                shared_cache=shared_cache,
                max_article_age_minutes=self.max_article_age_minutes,
                require_published_time=self.require_published_time,
            )
            by_category[category] = agent.run()

        total = sum(len(v) for v in by_category.values())
        self.logger.info(f"Ingestion complete: {total} total articles")
        return by_category
