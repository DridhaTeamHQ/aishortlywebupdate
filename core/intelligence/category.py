"""Category decider for CMS taxonomy."""

from __future__ import annotations

import re

try:
    from utils.model_client import GeminiClient
except Exception:
    from utils import GeminiClient
from utils.logger import get_logger


class CategoryDecider:
    """Classifies article content into CMS category labels with India-context guardrails."""

    # Locked to the live CMS category set. Nothing else may be emitted.
    VALID_CATEGORIES = [
        "Assembly Elections",
        "Technology",
        "Lifestyle",
        "State",
        "International",
        "National",
        "Entertainment",
        "Finance",
        "Sports",
    ]

    PIPELINE_HINT_MAP = {
        "business": "Finance",
        "tech": "Technology",
        "international": "International",
        "national": "National",
        "politics": "National",   # CMS has no generic Politics desk
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

    ELECTION_KEYWORDS = [
        "assembly election", "assembly polls", "assembly elections", "lok sabha election", "general election",
        "by-election", "bypoll", "by-poll", "vote count", "counting of votes", "exit poll", "election commission",
        "poll campaign", "election rally", "voter turnout", "constituency", "candidate list", "manifesto",
        "nomination filed", "ballot",
    ]
    ENTERTAINMENT_KEYWORDS = [
        "movie", "film", "box office", "trailer", "teaser", "actor", "actress", "bollywood", "tollywood",
        "hollywood", "ott", "web series", "song", "album", "celebrity", "cinema", "director", "release date",
    ]
    LIFESTYLE_KEYWORDS = [
        "health", "wellness", "fitness", "diet", "recipe", "food", "travel", "tourism", "fashion", "beauty",
        "lifestyle", "yoga", "mental health", "skincare", "relationship", "parenting",
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

Allowed categories (use exact text, choose ONLY from this list):
- Assembly Elections
- Technology
- Lifestyle
- State
- International
- National
- Entertainment
- Finance
- Sports

Rules:
1) India domestic governance/policy/civic updates -> National.
2) Indian election / assembly / party-poll campaign coverage -> Assembly Elections.
3) Non-India geopolitical/world events -> International.
4) Clearly state-specific Indian stories (Telangana, Andhra, single-state focus) -> State.
5) AI/technology/product/regulation stories -> Technology.
6) Business/economy/markets/companies/banking -> Finance.
7) Movies/celebrity/music/TV/OTT -> Entertainment.
8) Health/food/travel/wellness/culture features -> Lifestyle.
9) Sports of any kind -> Sports.
10) Do not return National/State for UK/US/Europe/global stories unless explicitly India-focused.

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
        election_signal = self._contains_any(text, self.ELECTION_KEYWORDS)
        tech_signal = self._contains_any(text, self.TECH_KEYWORDS)
        business_signal = self._contains_any(text, self.BUSINESS_KEYWORDS)

        # Hard hint overrides first.
        if pipeline_hint == "tech" or tech_signal:
            return "Technology"
        if pipeline_hint == "business" or business_signal:
            return "Finance"
        if pipeline_hint == "sports":
            return "Sports"

        # Indian election/poll coverage gets the dedicated CMS desk.
        if election_signal and india_context:
            return "Assembly Elections"

        # Regional Indian stories → State.
        if state_override:
            return state_override

        if self._is_india_source(source_low):
            if decided in {"International", "State"}:
                return heuristic if heuristic in {"National", "State", "Finance", "Sports", "Entertainment", "Lifestyle"} else "National"

        if not india_context and decided in {"National", "State"}:
            if heuristic in {"International", "Technology", "Finance", "Sports", "Entertainment", "Lifestyle"}:
                return heuristic
            return "International"

        if decided == "International" and india_context:
            if heuristic in {"National", "State", "Assembly Elections"}:
                return heuristic
            return "National"

        if pipeline_hint == "national" and decided == "International" and india_context:
            return "National"

        if pipeline_hint == "international" and decided in {"National", "State"} and not india_context:
            return "International"

        return decided if decided in self.VALID_CATEGORIES else (heuristic or "National")

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
        is_india_context = self._is_india_context(text, (source or "").strip().lower())

        if self._contains_any(text, self.TECH_KEYWORDS):
            return "Technology"

        if any(k in text for k in ["cricket", "football", "soccer", "tennis", "badminton", "hockey", "olympic", "world cup", "tournament", "match", "coach", "player", "goal"]):
            return "Sports"

        # Indian election coverage → dedicated desk; only when clearly poll-related.
        if self._contains_any(text, self.ELECTION_KEYWORDS) and is_india_context:
            return "Assembly Elections"

        if self._contains_any(text, self.ENTERTAINMENT_KEYWORDS):
            return "Entertainment"

        if self._contains_any(text, self.BUSINESS_KEYWORDS):
            return "Finance"

        if self._contains_any(text, self.LIFESTYLE_KEYWORDS):
            return "Lifestyle"

        # Single-state Indian focus → State.
        state_override = self._state_override(text)
        if state_override:
            return state_override

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
        # The live CMS has a single "State" desk (no per-state labels).
        if any(keyword in text for keyword in self.TELANGANA_KEYWORDS):
            return "State"
        if any(keyword in text for keyword in self.ANDHRA_KEYWORDS):
            return "State"
        return ""
