"""
STRICT DATA CONTRACT
Intelligence Agent -> Browser Executor

NO DEVIATIONS ALLOWED.
"""

from typing import TypedDict, Optional, Dict, Literal
from dataclasses import dataclass
from enum import Enum

class Category(str, Enum):
    # Mirrors the live CMS category set exactly.
    ASSEMBLY_ELECTIONS = "Assembly Elections"
    TECHNOLOGY = "Technology"
    LIFESTYLE = "Lifestyle"
    STATE = "State"
    INTERNATIONAL = "International"
    NATIONAL = "National"
    ENTERTAINMENT = "Entertainment"
    FINANCE = "Finance"
    SPORTS = "Sports"

class ContentBlock(TypedDict):
    headline: str  # validated length
    summary: str   # validated length

class ImageAsset(TypedDict):
    path: str
    source: Literal["og", "bing", "google"]
    watermark_free: bool

class QualityMetrics(TypedDict):
    english_score: int
    telugu_score: int
    telugu_unicode_pct: float
    category_confidence: float

class ArticlePayload(TypedDict):
    # Source
    source: str
    url: str
    
    # Content
    english: ContentBlock
    telugu: ContentBlock
    
    # Metadata
    category: str  # Use string to match CMS exact values
    hashtag: str
    
    # Media
    image: Optional[ImageAsset]
    
    # Validation (Gatekeeping)
    quality: QualityMetrics
    
    # Execution Flags
    push_notification: bool # Explicitly enabled
    ready_to_publish: bool  # Final seal of approval
