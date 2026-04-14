from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional


CategoryName = Literal["business", "tech", "international", "national", "environment", "crime", "sports"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class IngestedArticle:
    category: CategoryName
    source: str
    source_url: str
    url: str
    title: str
    body: str
    published_time: Optional[str]
    ingested_at: datetime = field(default_factory=utcnow)
    og_image: Optional[str] = None
    main_image: Optional[str] = None


@dataclass
class EventCluster:
    id: str
    canonical_title: str
    articles: List[IngestedArticle]
    source_groups: Dict[str, List[IngestedArticle]]
    start_time: datetime
    end_time: datetime
    dominant_category: CategoryName
    story_key: str = ""


@dataclass
class BreakingDecision:
    is_breaking: bool
    confidence: float
    reasons: List[str]
