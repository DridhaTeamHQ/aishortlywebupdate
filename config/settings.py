import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List

from dotenv import load_dotenv


load_dotenv()


def _get_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _default_headless() -> bool:
    hosted_markers = (
        "RAILWAY_PROJECT_ID",
        "RAILWAY_ENVIRONMENT_ID",
        "RENDER",
        "RENDER_SERVICE_ID",
        "VERCEL",
        "VERCEL_ENV",
        "CI",
    )
    if any(os.getenv(marker) for marker in hosted_markers):
        return True

    # Linux containers without a display should default to headless mode.
    if os.name != "nt" and not os.getenv("DISPLAY"):
        return True

    return False


def _resolve_headless() -> bool:
    default_headless = _default_headless()
    configured = os.getenv("HEADLESS")
    resolved = _get_bool(configured, default_headless)

    # Hosted Linux workers should never try to launch a headed browser unless
    # the operator explicitly opts in. Old dashboard env vars like HEADLESS=false
    # can otherwise override safe defaults and break Playwright at runtime.
    if default_headless and not resolved:
        allow_headed_hosted = _get_bool(os.getenv("ALLOW_HOSTED_HEADED"), False)
        if not allow_headed_hosted:
            return True

    return resolved


def _get_image_mode(value: str | None) -> str:
    if not value:
        return "api"
    v = value.strip().lower()
    return "browser" if v == "browser" else "api"


def _parse_json_env(name: str, default: Any) -> Any:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


DEFAULT_CATEGORY_SOURCES: Dict[str, List[Dict[str, str]]] = {
    "business": [
        {"name": "Reuters", "scraper": "reuters", "url": "https://www.reuters.com/business/"},
        {"name": "TOI", "scraper": "toi", "url": "https://timesofindia.indiatimes.com/business"},
        {"name": "India Today", "scraper": "indiatoday", "url": "https://www.indiatoday.in/business"},
        {"name": "NDTV", "scraper": "ndtv", "url": "https://www.ndtv.com/business"},
        {"name": "Mint", "scraper": "thehindu", "url": "https://www.livemint.com/market"},
        {"name": "ET", "scraper": "thehindu", "url": "https://economictimes.indiatimes.com/markets"},
    ],
    "tech": [
        {"name": "Reuters", "scraper": "reuters", "url": "https://www.reuters.com/technology/"},
        {"name": "TOI", "scraper": "toi", "url": "https://timesofindia.indiatimes.com/technology"},
        {"name": "India Today", "scraper": "indiatoday", "url": "https://www.indiatoday.in/technology"},
        {"name": "NDTV", "scraper": "ndtv", "url": "https://www.ndtv.com/tech"},
        {"name": "ET Tech", "scraper": "thehindu", "url": "https://economictimes.indiatimes.com/tech"},
    ],
    "international": [
        {"name": "Reuters", "scraper": "reuters", "url": "https://www.reuters.com/world/"},
        {"name": "TOI", "scraper": "toi", "url": "https://timesofindia.indiatimes.com/world"},
        {"name": "India Today", "scraper": "indiatoday", "url": "https://www.indiatoday.in/world"},
        {"name": "AlJazeera", "scraper": "aljazeera", "url": "https://www.aljazeera.com/news"},
        {"name": "BBC", "scraper": "thehindu", "url": "https://www.bbc.com/news/world"},
        {"name": "WION", "scraper": "ndtv", "url": "https://www.wionews.com/world"},
    ],
    "national": [
        {"name": "The Hindu", "scraper": "thehindu", "url": "https://www.thehindu.com/news/national/"},
        {"name": "TOI", "scraper": "toi", "url": "https://timesofindia.indiatimes.com/india"},
        {"name": "India Today", "scraper": "indiatoday", "url": "https://www.indiatoday.in/india"},
        {"name": "NDTV", "scraper": "ndtv", "url": "https://www.ndtv.com/india"},
        {"name": "Hindustan Times", "scraper": "thehindu", "url": "https://www.hindustantimes.com/india-news"},
    ],
    "environment": [
        {"name": "Reuters", "scraper": "reuters", "url": "https://www.reuters.com/business/environment/"},
        {"name": "India Today", "scraper": "indiatoday", "url": "https://www.indiatoday.in/environment"},
        {"name": "TOI", "scraper": "toi", "url": "https://timesofindia.indiatimes.com/india"},
        {"name": "AlJazeera", "scraper": "aljazeera", "url": "https://www.aljazeera.com/climate-crisis"},
        {"name": "The Hindu", "scraper": "thehindu", "url": "https://www.thehindu.com/sci-tech/energy-and-environment/"},
    ],
    "crime": [
        {"name": "The Hindu", "scraper": "thehindu", "url": "https://www.thehindu.com/news/national/"},
        {"name": "TOI", "scraper": "toi", "url": "https://timesofindia.indiatimes.com/india"},
        {"name": "India Today", "scraper": "indiatoday", "url": "https://www.indiatoday.in/crime"},
        {"name": "NDTV", "scraper": "ndtv", "url": "https://www.ndtv.com/india"},
        {"name": "Hindustan Times", "scraper": "thehindu", "url": "https://www.hindustantimes.com/india-news"},
    ],
    "sports": [
        {"name": "TOI", "scraper": "toi", "url": "https://timesofindia.indiatimes.com/sports"},
        {"name": "The Hindu", "scraper": "thehindu", "url": "https://www.thehindu.com/sport/"},
        {"name": "India Today", "scraper": "indiatoday", "url": "https://www.indiatoday.in/sports"},
        {"name": "NDTV Sports", "scraper": "ndtv", "url": "https://sports.ndtv.com"},
        {"name": "ESPN", "scraper": "thehindu", "url": "https://www.espncricinfo.com/ci/content/story/index.html"},
    ],
}


def _parse_category_sources(name: str = "CATEGORY_SOURCES") -> Dict[str, List[Dict[str, str]]]:
    raw = _parse_json_env(name, DEFAULT_CATEGORY_SOURCES)
    if not isinstance(raw, dict):
        return DEFAULT_CATEGORY_SOURCES

    normalized: Dict[str, List[Dict[str, str]]] = {}
    for category, default_rows in DEFAULT_CATEGORY_SOURCES.items():
        rows = raw.get(category, default_rows)
        if not isinstance(rows, list) or not rows:
            normalized[category] = default_rows
            continue

        valid_rows: List[Dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name_val = str(row.get("name", "")).strip()
            scraper_val = str(row.get("scraper", "")).strip().lower()
            url_val = str(row.get("url", "")).strip()
            if not (name_val and scraper_val and url_val):
                continue
            valid_rows.append({"name": name_val, "scraper": scraper_val, "url": url_val})

        normalized[category] = valid_rows or default_rows

    return normalized


DEFAULT_SOURCE_CREDIBILITY = {
    "BBC": 0.9,
    "AlJazeera": 0.82,
    "TOI": 0.78,
    "NDTV": 0.72,
    "India Today": 0.79,
    "Reuters": 0.95,
    "The Hindu": 0.84,
}

DEFAULT_IMAGE_THRESHOLDS = {
    "min_width": 400,
    "min_height": 220,
    "min_file_size_bytes": 15000,
    "min_aspect_ratio": 0.35,
    "max_aspect_ratio": 3.2,
    "min_sharpness": 6.0,
    "min_relevance": 0.10,
    "vision_weight": 0.25,
    "min_vision_quality": 0.45,
    "min_vision_relevance": 0.35,
    "vision_max_candidates": 6,
    "max_probe_candidates": 12,
}

DEFAULT_CATEGORY_PUBLISH_PLAN: List[Dict[str, Any]] = [
    {"category": "international", "total": 5, "breaking_target": 3},
    {"category": "national", "total": 5, "breaking_target": 3},
    {"category": "business", "total": 5, "breaking_target": 3},
    {"category": "sports", "total": 5, "breaking_target": 0},
    {"category": "tech", "total": 5, "breaking_target": 3},
    {"category": "environment", "total": 5, "breaking_target": 3},
    {"category": "crime", "total": 5, "breaking_target": 3},
]


def _parse_publish_plan(name: str = "CATEGORY_PUBLISH_PLAN") -> List[Dict[str, Any]]:
    raw_plan = _parse_json_env(name, DEFAULT_CATEGORY_PUBLISH_PLAN)
    if not isinstance(raw_plan, list):
        return DEFAULT_CATEGORY_PUBLISH_PLAN

    normalized: List[Dict[str, Any]] = []
    seen = set()

    for row in raw_plan:
        if not isinstance(row, dict):
            continue
        category = str(row.get("category", "")).strip().lower()
        if not category or category in seen:
            continue

        try:
            total = int(row.get("total", 5))
        except Exception:
            total = 5

        try:
            breaking = int(row.get("breaking_target", 3))
        except Exception:
            breaking = 3

        if total <= 0:
            continue
        breaking = max(0, min(breaking, total))

        normalized.append({"category": category, "total": total, "breaking_target": breaking})
        seen.add(category)

    return normalized or DEFAULT_CATEGORY_PUBLISH_PLAN


@dataclass(frozen=True)
class Settings:
    cms_url: str
    cms_email: str
    cms_password: str
    cms_role: str
    source_url: str
    gemini_api_key: str | None
    openai_api_key: str | None
    ai_provider: str
    headless: bool
    slow_mo: int
    user_data_dir: str
    screenshots_dir: str
    downloads_dir: str
    image_mode: str

    max_articles: int
    max_links_per_source: int
    max_article_age_minutes: int
    require_published_time: bool

    scheduler_enabled: bool
    scheduler_interval_minutes: int
    recent_failure_skip_minutes: int
    story_dedupe_hours: int

    category_sources: Dict[str, List[Dict[str, str]]]
    category_publish_plan: List[Dict[str, Any]]

    breaking_min_sources: int
    breaking_window_minutes: int
    breaking_confidence_threshold: float
    source_credibility: Dict[str, float]

    resolver_title_similarity: float
    resolver_content_similarity: float
    resolver_time_window_minutes: int

    image_quality_thresholds: Dict[str, float]


def get_settings() -> Settings:
    return Settings(
        cms_url=os.getenv("CMS_URL", "").strip(),
        cms_email=os.getenv("CMS_EMAIL", "").strip(),
        cms_password=os.getenv("CMS_PASSWORD", "").strip(),
        cms_role=os.getenv("CMS_ROLE", "Content Writer").strip(),
        source_url=os.getenv("SOURCE_URL", "").strip(),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        ai_provider=os.getenv("AI_PROVIDER", "openai").strip().lower(),
        headless=_resolve_headless(),
        slow_mo=int(os.getenv("SLOW_MO", "0")),
        user_data_dir=os.getenv("USER_DATA_DIR", ".playwright").strip(),
        screenshots_dir=os.getenv("SCREENSHOTS_DIR", "artifacts/screenshots").strip(),
        downloads_dir=os.getenv("DOWNLOADS_DIR", "artifacts/downloads").strip(),
        image_mode=_get_image_mode(os.getenv("IMAGE_MODE")),

        max_articles=int(os.getenv("MAX_ARTICLES", str(sum(int(row.get("total", 0)) for row in DEFAULT_CATEGORY_PUBLISH_PLAN)))),
        max_links_per_source=int(os.getenv("MAX_LINKS_PER_SOURCE", "50")),
        max_article_age_minutes=int(
            os.getenv(
                "MAX_ARTICLE_AGE_MINUTES",
                str(int(os.getenv("MAX_ARTICLE_AGE_HOURS", "0")) * 60) if os.getenv("MAX_ARTICLE_AGE_HOURS") else "1440",
            )
        ),
        require_published_time=_get_bool(os.getenv("REQUIRE_PUBLISHED_TIME"), True),

        scheduler_enabled=_get_bool(os.getenv("SCHEDULER_ENABLED"), False),
        scheduler_interval_minutes=int(os.getenv("SCHEDULER_INTERVAL_MINUTES", "15")),
        recent_failure_skip_minutes=int(os.getenv("RECENT_FAILURE_SKIP_MINUTES", "45")),
        story_dedupe_hours=int(os.getenv("STORY_DEDUPE_HOURS", "48")),

        category_sources=_parse_category_sources("CATEGORY_SOURCES"),
        category_publish_plan=_parse_publish_plan("CATEGORY_PUBLISH_PLAN"),

        breaking_min_sources=int(os.getenv("BREAKING_MIN_SOURCES", "2")),
        breaking_window_minutes=int(os.getenv("BREAKING_WINDOW_MINUTES", "30")),
        breaking_confidence_threshold=float(os.getenv("BREAKING_CONFIDENCE_THRESHOLD", "0.6")),
        source_credibility=_parse_json_env("SOURCE_CREDIBILITY", DEFAULT_SOURCE_CREDIBILITY),

        resolver_title_similarity=float(os.getenv("RESOLVER_TITLE_SIMILARITY", "0.78")),
        resolver_content_similarity=float(os.getenv("RESOLVER_CONTENT_SIMILARITY", "0.45")),
        resolver_time_window_minutes=int(os.getenv("RESOLVER_TIME_WINDOW_MINUTES", "120")),

        image_quality_thresholds=_parse_json_env("IMAGE_QUALITY_THRESHOLDS", DEFAULT_IMAGE_THRESHOLDS),
    )



