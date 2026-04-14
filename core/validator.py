"""STRICT VALIDATOR - Pre-Flight Validation Gate."""

import os
import re
from enum import Enum
from dataclasses import dataclass
from typing import Optional
from utils.logger import get_logger


class FailureType(str, Enum):
    CONTENT_VALIDATION_FAILURE = "content_validation_failure"
    LOGIN_FAILURE = "login_failure"
    NAVIGATION_FAILURE = "navigation_failure"
    REACT_STATE_CORRUPTION = "react_state_corruption"
    CATEGORY_SELECTION_FAILURE = "category_selection_failure"
    IMAGE_UPLOAD_FAILURE = "image_upload_failure"
    CROP_MODAL_STUCK = "crop_modal_stuck"
    PUBLISH_BUTTON_NOT_FOUND = "publish_button_not_found"
    PUBLISH_NO_OP = "publish_no_op"
    BROWSER_CRASH = "browser_crash"


class RecoveryAction(str, Enum):
    DISCARD_ARTICLE = "discard_article"
    RETRY_ACTION = "retry_action"
    RELOAD_PAGE = "reload_page"
    RESTART_BROWSER = "restart_browser"
    ABORT_PROCESS = "abort_process"


RECOVERY_MATRIX = {
    FailureType.CONTENT_VALIDATION_FAILURE: RecoveryAction.DISCARD_ARTICLE,
    FailureType.LOGIN_FAILURE: RecoveryAction.RETRY_ACTION,
    FailureType.NAVIGATION_FAILURE: RecoveryAction.RELOAD_PAGE,
    FailureType.REACT_STATE_CORRUPTION: RecoveryAction.RELOAD_PAGE,
    FailureType.CATEGORY_SELECTION_FAILURE: RecoveryAction.RETRY_ACTION,
    FailureType.IMAGE_UPLOAD_FAILURE: RecoveryAction.RETRY_ACTION,
    FailureType.CROP_MODAL_STUCK: RecoveryAction.DISCARD_ARTICLE,
    FailureType.PUBLISH_BUTTON_NOT_FOUND: RecoveryAction.RETRY_ACTION,
    FailureType.PUBLISH_NO_OP: RecoveryAction.RETRY_ACTION,
    FailureType.BROWSER_CRASH: RecoveryAction.RESTART_BROWSER,
}


VALID_CATEGORIES = [
    "Technology",
    "Crime",
    "Education",
    "Environment",
    "Finance",
    "Health",
    "Andhra Pradesh",
    "Telangana",
    "State",
    "International",
    "National",
    "Politics",
    "Sports",
    "Entertainment",
    "Lifestyle",
    "Spiritual",
    "Business",
]


@dataclass
class ValidationResult:
    is_valid: bool
    failure_type: Optional[FailureType] = None
    error_message: str = ""


class ArticleValidator:
    MIN_TITLE_LEN = 10
    MAX_TITLE_LEN = 80
    MIN_BODY_LEN = 50
    MAX_BODY_LEN = 380
    MIN_TELUGU_TITLE_PURITY = 72.0
    MIN_TELUGU_BODY_PURITY = 80.0
    ALLOWED_ENGLISH_TOKENS = {
        "us", "uk", "un", "ai", "pm", "cm", "bjp", "congress", "g20", "who", "isro",
        "nato", "iran", "israel", "india", "modi", "trump", "rahul", "gandhi", "rbi", "mea", "icc",
    }
    SOURCE_BOILERPLATE_PATTERNS = (
        r"(?:\b(?:the\s+times\s+of\s+india|times\s+of\s+india|india\s+today|bbc\s+news|al\s*jazeera)\b\.?\s*){1,}",
        r"\bof\s+india\.\s+of\s+india\b",
    )

    def __init__(self):
        self.logger = get_logger("validator")

    def validate(
        self,
        english_title: str,
        english_body: str,
        telugu_title: str = "",
        telugu_body: str = "",
        category: str = "",
        image_path: Optional[str] = None,
        hashtag: str = "",
        image_search_query: str = "",
        allow_missing_image: bool = False,
    ) -> ValidationResult:
        if not english_title or len(english_title) < self.MIN_TITLE_LEN:
            return ValidationResult(False, FailureType.CONTENT_VALIDATION_FAILURE, "English title too short")
        if len(english_title) > self.MAX_TITLE_LEN:
            return ValidationResult(False, FailureType.CONTENT_VALIDATION_FAILURE, "English title too long")
        if "," in english_title:
            return ValidationResult(False, FailureType.CONTENT_VALIDATION_FAILURE, "English title cannot contain commas")
        if "!" in english_title:
            return ValidationResult(False, FailureType.CONTENT_VALIDATION_FAILURE, "English title cannot contain exclamation marks")

        if not english_body or len(english_body) < self.MIN_BODY_LEN:
            return ValidationResult(False, FailureType.CONTENT_VALIDATION_FAILURE, "English body too short")
        if len(english_body) > self.MAX_BODY_LEN:
            return ValidationResult(False, FailureType.CONTENT_VALIDATION_FAILURE, "English body too long")
        if self._has_source_boilerplate(english_body):
            return ValidationResult(False, FailureType.CONTENT_VALIDATION_FAILURE, "English body contains source boilerplate")

        if category not in VALID_CATEGORIES:
            return ValidationResult(False, FailureType.CONTENT_VALIDATION_FAILURE, f"Invalid category: {category}")

        if image_path:
            if not os.path.exists(image_path):
                return ValidationResult(False, FailureType.CONTENT_VALIDATION_FAILURE, f"Image not found: {image_path}")
            if os.path.getsize(image_path) < 1000:
                return ValidationResult(False, FailureType.CONTENT_VALIDATION_FAILURE, "Image too small")
        elif image_search_query:
            pass
        elif not allow_missing_image:
            return ValidationResult(False, FailureType.CONTENT_VALIDATION_FAILURE, "Image (or search query) is required")

        if not hashtag or not hashtag.startswith("#"):
            self.logger.warning("Hashtag does not start with #")

        return ValidationResult(is_valid=True)

    def _telugu_percentage(self, text: str) -> float:
        if not text:
            return 0.0

        clean_chars = [c for c in text if not c.isspace()]
        total_chars = len(clean_chars)
        if total_chars == 0:
            return 0.0

        telugu_chars = sum(1 for c in clean_chars if "\u0C00" <= c <= "\u0C7F")

        allowed_english_chars = 0
        for token in re.findall(r"[A-Za-z]{2,}", text):
            if token.lower() in self.ALLOWED_ENGLISH_TOKENS:
                allowed_english_chars += len(token)

        purity_chars = min(total_chars, telugu_chars + allowed_english_chars)
        return (purity_chars / total_chars) * 100.0

    def get_recovery_action(self, failure_type: FailureType) -> RecoveryAction:
        return RECOVERY_MATRIX.get(failure_type, RecoveryAction.DISCARD_ARTICLE)

    def _has_source_boilerplate(self, text: str) -> bool:
        low = " ".join((text or "").split()).lower()
        if not low:
            return False
        return any(re.search(pattern, low, flags=re.IGNORECASE) for pattern in self.SOURCE_BOILERPLATE_PATTERNS)
