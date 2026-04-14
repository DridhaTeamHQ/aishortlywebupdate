"""Category decider for CMS taxonomy."""

from __future__ import annotations

import re

from utils.gemini_client import GeminiClient
from utils.logger import get_logger


class CategoryDecider:
    """Classifies article content into CMS category labels with India-context guardrails."""

    VALID_CATEGORIES = [
        "Technology",
        "Crime",
        "Education",
        "Business",
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
        "Environment",
        "Spiritual",
    ]

    PIPELINE_HINT_MAP = {
        "business": "Business",
        "tech": "Technology",
        "international": "International",
        "national": "National",
        "environment": "Environment",
        "crime": "Crime",
        "sports": "Sports",
    }

    INDIA_SOURCE_HINTS = (
        "toi",
        "the hindu",
        "times of india",
        "ndtv",
        "india today",
        "hindustan times",
        "indian express",
        "new indian express",
        "telangana today",
        "siasat",
        "eenadu",
    )
    GLOBAL_SOURCE_HINTS = ("guardian", "bbc", "reuters", "aljazeera", "al jazeera", "cnn", "associated press", "ap")

    ENVIRONMENT_KEYWORDS = [
        "environment", "climate", "wildlife", "ecology", "ecological", "forest", "biodiversity", "habitat",
        "conservation", "restore", "restoration", "species", "nature", "pollution", "emission", "sustainability",
    ]
    TECH_KEYWORDS = [
        "technology", "tech", "artificial intelligence", " ai ", "software", "chip", "semiconductor", "cyber", "internet",
        "platform", "algorithm", "openai", "model", "machine learning", "copyright", "creative rights", "digital",
    ]
    BUSINESS_KEYWORDS = [
        "market", "stock", "economy", "inflation", "company", "startup", "gdp", "trade", "business", "finance",
        "interest rate", "mortgage", "bank", "oil prices", "revenue",
    ]
    TELANGANA_KEYWORDS = [
        "telangana", "hyderabad", "secunderabad", "warangal", "khammam", "nizamabad", "karimnagar", "ktr", "revanth reddy",
        "ghmc", "huzurabad",
    ]
    ANDHRA_KEYWORDS = [
        "andhra pradesh", "amaravati", "visakhapatnam", "vijayawada", "tirupati", "guntur", "kadapa", "nellore",
        "ananthapur", "ananthapuramu", "chandrababu", "jagan",
    ]

    def __init__(self):
        self.logger = get_logger("category")
        self.client = GeminiClient()

    def decide(self, title: str, body: str, source: str = "", pipeline_hint: str = "") -> str:
        heuristic = self._heuristic_decide(title=title, body=body, source=source, pipeline_hint=pipeline_hint)

        if not self.client or not self.client.available:
            return heuristic

        prompt = f"""Choose exactly ONE CMS category for this news article.

Title: {title}
Body: {body[:1200]}
Source: {source}
Pipeline hint: {pipeline_hint or "none"}

Allowed categories (use exact text):
- Technology
- Crime
- Education
- Business
- Finance
- Health
- Andhra Pradesh
- Telangana
- State
- International
- National
- Politics
- Sports
- Entertainment
- Lifestyle
- Environment
- Spiritual

Rules:
1) India domestic governance/policy/civic updates -> National or Politics.
2) Non-India geopolitical/world events -> International.
3) Wildlife/climate/ecology/conservation -> Environment.
4) AI/technology/product/regulation stories -> Technology.
5) Business/economy/markets/companies -> Business or Finance.
6) Use Andhra Pradesh/Telangana only for clearly state-specific stories.
7) Do not return National/State for UK/US/Europe/global stories unless explicitly India-focused.

Return only the category name."""

        try:
            raw = self.client.generate_text(
                prompt,
                temperature=0,
                max_output_tokens=20,
            ).strip()

            model_choice = self._normalize_model_choice(raw)
            if not model_choice:
                self.logger.warning(f"Invalid category from model '{raw}', using heuristic '{heuristic}'")
                return heuristic

            return self._apply_guardrails(
                decided=model_choice,
                heuristic=heuristic,
                title=title,
                body=body,
                source=source,
                pipeline_hint=pipeline_hint,
            )
        except Exception as exc:
            self.logger.warning(f"Category model failed, using heuristic '{heuristic}': {exc}")
            return heuristic

    def _normalize_model_choice(self, raw: str) -> str:
        low = raw.lower().strip()
        for category in self.VALID_CATEGORIES:
            c_low = category.lower()
            if low == c_low or c_low in low:
                return category
        return ""

    def _has_keyword(self, text: str, keyword: str) -> bool:
        k = keyword.strip().lower()
        if not k:
            return False
        if " " in k:
            return k in text
        return re.search(rf"\b{re.escape(k)}\b", text) is not None

    def _contains_any(self, text: str, keywords: list[str]) -> bool:
        return any(self._has_keyword(text, k) for k in keywords)

    def _apply_guardrails(
        self,
        decided: str,
        heuristic: str,
        title: str,
        body: str,
        source: str,
        pipeline_hint: str,
    ) -> str:
        text = f" {title} {body} ".lower()
        source_low = (source or "").strip().lower()
        state_override = self._state_override(text)
        india_context = self._is_india_context(text, source_low)
        env_signal = self._contains_any(text, self.ENVIRONMENT_KEYWORDS)
        tech_signal = self._contains_any(text, self.TECH_KEYWORDS)
        business_signal = self._contains_any(text, self.BUSINESS_KEYWORDS)

        if state_override:
            return state_override

        if pipeline_hint == "environment" and env_signal:
            return "Environment"
        if pipeline_hint == "tech" and tech_signal:
            return "Technology"
        if pipeline_hint == "business" and business_signal:
            return "Business"

        if self._is_india_source(source_low):
            if decided in {"International", "State"}:
                return heuristic if heuristic in {"National", "Politics", "Telangana", "Andhra Pradesh", "Crime", "Business", "Finance"} else "National"

        if not india_context and decided in {"National", "State", "Andhra Pradesh", "Telangana"}:
            if env_signal:
                return "Environment"
            if tech_signal:
                return "Technology"
            if business_signal:
                return "Business"
            if heuristic in {"International", "Technology", "Environment", "Business", "Finance", "Sports", "Crime", "Health", "Entertainment"}:
                return heuristic
            return "International"

        if decided == "International" and india_context:
            if heuristic in {"Politics", "National", "Telangana", "Andhra Pradesh"}:
                return heuristic
            return "National"

        if pipeline_hint == "national" and decided == "International" and india_context:
            return "National"

        if pipeline_hint == "international" and decided in {"National", "State"} and not india_context:
            return "International"

        if env_signal and decided in {"International", "National", "Lifestyle"}:
            return "Environment"

        if tech_signal and decided in {"Politics", "International", "National"}:
            return "Technology"

        if business_signal and decided in {"International", "National"}:
            return "Business"

        return decided

    def _is_india_source(self, source_low: str) -> bool:
        source_norm = (source_low or "").strip().lower()
        return any(token in source_norm for token in self.INDIA_SOURCE_HINTS)

    def _is_global_source(self, source_low: str) -> bool:
        source_norm = (source_low or "").strip().lower()
        return any(token in source_norm for token in self.GLOBAL_SOURCE_HINTS)

    def _is_india_context(self, text: str, source_low: str) -> bool:
        india_markers = [
            " india ", " indian ", "new delhi", "delhi", "mumbai", "bengaluru", "kolkata", "chennai", "hyderabad",
            "times of india", "the hindu", "rajya sabha", "lok sabha", "bihar", "telangana", "andhra pradesh",
            "west bengal", "maharashtra", "uttar pradesh", "tamil nadu", "kerala", "karnataka", "gujarat",
        ]
        if self._is_india_source(source_low):
            return True
        return any(marker in text for marker in india_markers)

    def _heuristic_decide(self, title: str, body: str, source: str, pipeline_hint: str) -> str:
        text = f" {title} {body} ".lower()

        state_override = self._state_override(text)
        if state_override:
            return state_override

        if self._contains_any(text, self.ENVIRONMENT_KEYWORDS):
            return "Environment"

        if self._contains_any(text, self.TECH_KEYWORDS):
            return "Technology"

        if any(k in text for k in ["cricket", "football", "soccer", "tennis", "badminton", "hockey", "olympic", "world cup", "tournament", "match", "coach", "player", "goal"]):
            return "Sports"

        if any(k in text for k in ["murder", "arrest", "crime", "police", "fraud", "court", "investigation", "assault"]):
            return "Crime"

        if any(k in text for k in ["hospital", "health", "disease", "medical", "doctor", "vaccine"]):
            return "Health"

        if any(k in text for k in ["school", "college", "university", "exam", "education", "student"]):
            return "Education"

        if self._contains_any(text, self.BUSINESS_KEYWORDS):
            return "Business"

        if any(k in text for k in ["election", "parliament", "assembly", "minister", "party", "rajya sabha", "lok sabha", "government"]):
            return "Politics"

        is_india_context = self._is_india_context(text, (source or "").strip().lower())

        if pipeline_hint and pipeline_hint in self.PIPELINE_HINT_MAP:
            hinted = self.PIPELINE_HINT_MAP[pipeline_hint]
            if hinted in self.VALID_CATEGORIES:
                if hinted == "International" and is_india_context:
                    return "National"
                if hinted in {"National", "State"} and not is_india_context and self._is_global_source((source or "").strip().lower()):
                    return "International"
                return hinted

        return "National" if is_india_context else "International"

    def _state_override(self, text: str) -> str:
        if any(keyword in text for keyword in self.TELANGANA_KEYWORDS):
            return "Telangana"
        if any(keyword in text for keyword in self.ANDHRA_KEYWORDS):
            return "Andhra Pradesh"
        return ""
