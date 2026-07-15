"""English summarizer for Shortly-style factual short news cards."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional

try:
    from utils.model_client import GeminiClient
except Exception:
    from utils import GeminiClient
from utils.logger import get_logger


class Summarizer:
    """Creates concise, professional English title/body for CMS."""

    _TITLE_BODY_JSON_SCHEMA = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["title", "body"],
    }

    _SMALL_WORDS = frozenset(
        {
            "a", "an", "the", "and", "but", "or", "for", "nor", "on", "at", "to", "by", "of",
            "in", "with", "against", "as", "into", "from", "up", "out", "over",
        }
    )
    _ACTION_VERBS = frozenset(
        {
            "hits", "hit", "faces", "face", "surges", "surge", "falls", "fall", "widens", "widen",
            "rises", "rise", "tightens", "tighten", "warns", "warn", "vows", "vow", "pushes", "push",
            "blocks", "block", "approves", "approve", "orders", "order", "targets", "target",
            "slams", "slam", "rejects", "reject", "backs", "back", "sues", "sue", "asks", "ask",
            "announces", "announce", "jokes", "joke", "jolts", "jolt", "squeezes", "squeeze",
            "halts", "halt", "scrambles", "scramble", "opens", "open", "launches", "launch",
            "soars", "soar", "plunges", "plunge", "cuts", "cut", "rocks", "rock", "empties", "empty", "deepens", "deepen",
        }
    )
    _ALWAYS_UPPER = frozenset({"us", "uk", "uae", "eu", "un", "pm", "cm", "rbi", "cji", "gdp", "ai", "isro", "nato", "mea", "icc"})
    _COMMON_PROPER_NOUNS = frozenset({"india", "indian", "iran", "israel", "china", "russia", "ukraine", "pakistan", "uae", "saudi", "congress", "bjp", "modi", "trump", "rahul", "gandhi", "bbc", "aljazeera", "guardian"})
    _HOOK_TOKENS = frozenset(
        {
            "warning", "surge", "slump", "standoff", "deadline", "setback", "boost", "risk",
            "impact", "pressure", "escalation", "breakthrough", "turning", "crunch", "shock",
            "chaos", "row", "jolt", "scramble", "fallout", "squeeze", "blow", "flashpoint",
            "alert", "rattle", "hit", "surge", "plunge",
        }
    )
    _GENERIC_BODY_PHRASES = (
        "this development",
        "this comes amid",
        "this move",
        "this strategy",
        "this reflects",
        "officials said",
        "the development is likely",
    )
    _SOURCE_BOILERPLATE_PATTERNS = (
        r"(?:\b(?:the\s+times\s+of\s+india|times\s+of\s+india|india\s+today|bbc\s+news|al\s*jazeera)\b\.?\s*){1,}",
        r"\bof\s+india\.\s+of\s+india\b",
    )
    _LOW_VALUE_SENTENCE_PATTERNS = (
        r"\bis a vital waterway\b",
        r"\bis a key waterway\b",
        r"\bis a vital maritime route\b",
        r"\bis a major maritime route\b",
        r"^(?:it is now under scrutiny again)\b",
        r"^(?:this (?:development|situation|scrutiny)|the move)\s+(?:could|may|might)\s+(?:impact|affect|signal)\b",
        r"\bunder close watch in the coming days\b",
        r"\baffecting regional dynamics\b",
    )
    _WEAK_ENDING_PATTERNS = (
        r"^(?:this|the)\s+(?:development|move|situation|decision|scrutiny|incident|case|trend|shift)\s+"
        r"(?:could|may|might|would|is likely to)\b",
        r"^(?:this|the)\s+(?:development|move|situation|decision|incident|case|trend|shift)\s+"
        r"(?:highlights|highlighted|underscores|underscored|reflects|reflected|shows|showed|signals|signalled)\b",
        r"^(?:it|this)\s+(?:may|might|could)\s+(?:signal|highlight|underscore|reflect)\b",
        r"\b(?:regional dynamics|broader tensions|wider tensions|broader concerns|larger questions)\b",
    )
    _UNCERTAINTY_MARKERS = (
        "appear to show",
        "appears to show",
        "images circulating online",
        "online images",
        "circulating online",
        "social media",
        "viral images",
        "viral video",
        "unverified",
        "not independently verified",
        "reportedly",
        "alleged",
        "allegedly",
        "purportedly",
    )
    _CAUTION_OUTPUT_MARKERS = (
        "appear to show",
        "appears to show",
        "reportedly",
        "alleged",
        "allegedly",
        "unverified",
        "not independently verified",
        "purportedly",
    )
    _POSITIVE_VERIFICATION_MARKERS = (
        "bbc verify",
        "verified by",
        "investigation confirms",
        "verified images",
        "confirmed by officials",
        "confirmed by the military",
        "confirmed through flight radar",
    )
    _GENERIC_DESIGNATION_WORDS = (
        "communications and surveillance aircraft",
        "surveillance aircraft",
        "surveillance plane",
        "surveillance jet",
        "communications aircraft",
        "aircraft",
        "plane",
        "jet",
    )
    _NEWSROOM_PATTERN_NOTE = (
        "Newsroom style pattern: use sentence-case headlines, precise active verbs, "
        "named actors or exact identifiers early, and restrained wording. "
        "If evidence comes from photos, video, social posts or other external material, attribute it clearly "
        "and be explicit about what remains unconfirmed."
    )
    _LONGFORM_TO_ACRONYM = {
        "United Nations": "UN",
        "United States": "US",
        "European Union": "EU",
        "prime minister": "PM",
        "chief minister": "CM",
        "Board of Control for Cricket in India": "BCCI",
        "International Cricket Council": "ICC",
    }
    _TITLE_STYLE_REPLACEMENTS = (
        (r"\bwarns that\b", "warns"),
        (r"\bannounces\b", "releases"),
        (r"\brebuffs\b", "rejects"),
        (r"\bpostpones\b", "delays"),
        (r"\bto accept deportees\b", "accepts deportees"),
        (r"\bfull schedule for\b", "schedule for"),
        (r"\bagreement\b", "deal"),
        (r"\bunder (US|UK|EU|UN|UAE|NATO) deal\b", r"in \1 deal"),
        (r"\bactions\b", "moves"),
        (r"\bdozens remain missing\b", "dozens missing"),
        (r"\bover legal fears\b", "over legal risk"),
        (r"\bthreaten sovereignty\b", "put sovereignty at risk"),
        (r"\bpro-palestinian voices\b", "Palestine supporters"),
        (r"\bTwenty20\b", "T20"),
    )
    _ONGOING_TOKENS = frozenset(
        {
            "war", "conflict", "talks", "clashes", "clash", "operation", "probe", "investigation",
            "search", "rescue", "ceasefire", "negotiation", "standoff", "strike", "bombing", "protest",
            "hearing", "case", "crisis", "exchange", "campaign", "offensive",
        }
    )
    _CONSEQUENCE_TOKENS = frozenset(
        {
            "expected", "likely", "could", "may", "will", "pressure", "impact", "risk", "debate",
            "scrutiny", "jobs", "investment", "expansion", "growth", "costs", "prices", "strategy",
            "focus", "next", "ahead", "boost", "fallout", "warning",
        }
    )
    _GENERIC_SENTENCE_STARTS = (
        "this development",
        "this move",
        "this comes amid",
        "meanwhile",
        "in a major move",
        "here is why",
    )
    _STYLE_STOPWORDS = frozenset(
        {
            "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "in", "into",
            "is", "it", "its", "of", "on", "or", "that", "the", "their", "this", "to", "was", "were", "will", "with",
        }
    )
    _TITLE_COMPARISON_STOPWORDS = frozenset(
        {
            "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it",
            "of", "on", "or", "that", "the", "this", "to", "was", "were", "will", "with", "after",
            "amid", "over", "under", "new", "latest", "says", "say",
        }
    )
    _STYLE_BANK = """
Headline style patterns (factual, high-click, non-clickbait):
1) Actor + strong action + stake.
   Example: "US Won't Repeat China-Era Mistakes With India: Landau"
2) Match/report style with quote fragment.
   Example: "SKY Asks Brook, 'How Much More Do We Need?'"
3) Data or legal/business angle with clear consequence.
   Example: "Court Orders Airline to Pay Rs 1.08 Crore Over Cancelled Seats"
4) Public-interest alert framing.
   Example: "Iran Launches 7 Missiles, 131 Drones at UAE: Defence Ministry"
5) Conflict/controversy framing with sharp consequence.
   Example: "US House Rejects Bid to End Iran War, Debate Intensifies"
"""

    def __init__(self):
        self.logger = get_logger("summarizer")
        self.client = GeminiClient()
        self._training_examples = self._load_training_examples()

    def _load_training_examples(self) -> List[Dict[str, str]]:
        path = Path(__file__).resolve().parents[2] / "docs" / "headline_training_examples.jsonl"
        examples: List[Dict[str, str]] = []
        if not path.exists():
            self.logger.warning(f"Headline training file missing: {path}")
            return examples

        try:
            with path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        self.logger.warning("Skipping invalid headline training row")
                        continue

                    source_title = " ".join(str(item.get("source_title", "")).split())
                    source_body = " ".join(str(item.get("source_body", "")).split())
                    target_title = " ".join(str(item.get("target_title", "")).split())
                    target_body = " ".join(str(item.get("target_body", "")).split())
                    category = " ".join(str(item.get("category", "")).split())
                    style = " ".join(str(item.get("style", "")).split())
                    if not (source_title and target_title and target_body):
                        continue

                    style_text = " ".join(
                        value for value in [category, style, source_title, source_body, target_title, target_body] if value
                    )
                    item["_style_tokens"] = self._tokenize_style_text(style_text)
                    examples.append(item)
        except Exception as exc:
            self.logger.warning(f"Unable to load headline training examples: {exc}")
            return []

        self.logger.info(f"Loaded {len(examples)} headline training examples")
        return examples

    def _tokenize_style_text(self, text: str) -> frozenset[str]:
        tokens = set()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]+", (text or "").lower()):
            if len(token) < 3:
                continue
            if token in self._STYLE_STOPWORDS:
                continue
            tokens.add(token)
        return frozenset(tokens)

    def _pick_style_examples(self, title: str, body: str, limit: int = 3) -> List[Dict[str, str]]:
        if not self._training_examples:
            return []

        article_tokens = self._tokenize_style_text(f"{title} {body[:900]}")
        if not article_tokens:
            return []

        ranked = []
        for idx, example in enumerate(self._training_examples):
            example_tokens = example.get("_style_tokens", frozenset())
            if not example_tokens:
                continue
            overlap = len(article_tokens & example_tokens)
            if overlap == 0:
                continue

            title_overlap = len(article_tokens & self._tokenize_style_text(example.get("source_title", "")))
            style_overlap = len(article_tokens & self._tokenize_style_text(example.get("style", "")))
            score = (overlap * 3) + (title_overlap * 4) + style_overlap
            ranked.append((score, idx, example))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in ranked[:limit]]

    def _build_dynamic_style_examples(self, title: str, body: str, limit: int = 3) -> str:
        examples = self._pick_style_examples(title, body, limit=limit)
        if not examples:
            return ""

        lines = ["Closest reference examples from our training set:"]
        for idx, example in enumerate(examples, start=1):
            category = example.get("category", "General")
            style = example.get("style", "general")
            lines.append(
                f"{idx}) Category={category}; Style={style}; Source='{example['source_title']}'; "
                f"Better title='{example['target_title']}'; Better body='{example['target_body']}'"
            )
        lines.append("Follow the punch, specificity, and sentence rhythm of these examples without copying them.")
        return "\n".join(lines)

    def _house_style_block(self) -> str:
        """Fixed gold-standard examples that define the house voice and framing."""
        examples = [
            (
                "Technology",
                "Indian Railways to introduce AI waitlist forecasts",
                "Indian Railways will roll out an AI-powered booking system that predicts a ticket's "
                "waitlist-confirmation odds at the moment of booking. Slated for August 2026, the upgrade "
                "is built to sharpen travel planning, giving passengers clearer, real-time insight into "
                "seat availability.",
            ),
            (
                "Sports",
                "Hardik Pandya cleared for Afghanistan ODI series",
                "Hardik Pandya will return for India's three-match ODI series against Afghanistan, opening "
                "Sunday in Dharamsala. The 32-year-old, sidelined from several Mumbai Indians IPL games by "
                "back spasms, was passed fit by the BCCI's Centre of Excellence after completing five days "
                "of match simulations and full 10-over bowling spells.",
            ),
            (
                "National",
                "AAP exits INDIA bloc, demands clear agenda",
                "The Aam Aadmi Party has formally walked out of the opposition INDIA bloc, sealing the split "
                "by skipping Monday's alliance meeting. Confirming the move, AAP Rajya Sabha MP Sanjay Singh "
                "declared the party is no longer part of the alliance and pressed the remaining members to "
                "spell out a clear legislative agenda.",
            ),
            (
                "National",
                "ED raids six sites in Punjab, UP and Delhi-NCR",
                "The Enforcement Directorate searched six premises across Punjab, Uttar Pradesh and Delhi-NCR "
                "on Tuesday under the Prevention of Money Laundering Act. Targeting homes and offices in "
                "Ludhiana, Jalandhar, Bareilly and Noida, the raids are part of a money-laundering probe "
                "tied to Hampton Sky Realty Ltd, officials said.",
            ),
            (
                "International",
                "Navy coordinates rescue of 24 Indians after tanker attack",
                "Twenty-four Indian seafarers were rescued after a missile strike on the Palau-flagged tanker "
                "MT Marivex off Oman's Masirah coast. Tipped off by a crew member's relative, MRCC Mumbai "
                "worked with Oman's maritime rescue centre to divert a nearby ship and scramble two "
                "helicopters, bringing the entire crew to safety.",
            ),
            (
                "Finance",
                "Rupee slips 17 paise to 95.35 against the dollar",
                "The rupee weakened 17 paise to 95.35 against the US dollar in early trade, pressured by "
                "global headwinds. A firm dollar, climbing crude prices and persistent geopolitical tension "
                "soured investor sentiment, keeping the currency under strain on the foreign-exchange market.",
            ),
        ]
        lines = [
            "HOUSE STYLE — match the voice, tightness, and framing of these cards. "
            "Notice the headline and the first sentence always carry the SAME fact, and nothing is "
            "copied from a source. Do NOT reuse their content:",
        ]
        for category, title, body in examples:
            lines.append(f"[{category}] TITLE: {title}")
            lines.append(f"[{category}] BODY: {body}")
        return "\n".join(lines)

    def _title_case_headline(self, title: str) -> str:
        if not title:
            return title
        words = title.split()
        if not words:
            return title

        result = []
        for i, word in enumerate(words):
            m = re.match(r"^([^A-Za-z0-9]*)([A-Za-z0-9.-]+)([^A-Za-z0-9]*)$", word)
            if m:
                prefix, core, suffix = m.group(1), m.group(2), m.group(3)
                core_alpha = core.replace(".", "")
                if core_alpha.isupper() and 2 <= len(core_alpha) <= 6:
                    result.append(f"{prefix}{core.upper()}{suffix}")
                    continue

            low = word.lower()
            core_low = re.sub(r"[^a-z0-9]", "", low)
            if core_low in self._ALWAYS_UPPER:
                result.append(word.upper())
                continue

            is_edge = i == 0 or i == len(words) - 1
            if is_edge or low not in self._SMALL_WORDS:
                result.append(word[:1].upper() + low[1:])
            else:
                result.append(low)
        return " ".join(result)

    def _clean_title_noise(self, title: str) -> str:
        clean = " ".join((title or "").split())
        if not clean:
            return clean
        clean = re.sub(r"\s*[|\-]\s*(bbc|reuters|guardian|al\s*jazeera|the\s*hindu|toi|times\s*of\s*india)\s*$", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s+live\s+updates?\s*$", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s+", " ", clean)
        return clean.strip(" -|:")

    def _normalize_acronyms(self, title: str) -> str:
        out = (title or "").strip()
        if not out:
            return out
        replacements = {
            r"\bU\s*\.\s*S\s*\.?\b": "US",
            r"\bU\s*\.\s*K\s*\.?\b": "UK",
            r"\bU\s*\.\s*N\s*\.?\b": "UN",
            r"\bE\s*\.\s*U\s*\.?\b": "EU",
            r"\bU\s*\.\s*A\s*\.\s*E\s*\.?\b": "UAE",
            r"\bR\s*s\b": "Rs",
        }
        for pattern, repl in replacements.items():
            out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
        out = re.sub(r"\b(US|UK|UN|EU|UAE)\.(?=\s+[A-Z])", r"\1", out)
        out = re.sub(r"\bUS-([a-z])", lambda m: f"US-{m.group(1).upper()}", out)
        return out

    def _designation_key(self, text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (text or "").lower())

    def _extract_designations(self, text: str) -> Dict[str, str]:
        normalized_text = self._normalize_acronyms(text or "")
        found: Dict[str, str] = {}
        for match in re.finditer(r"\b[A-Z]{1,4}-\d{1,4}[A-Z]?\b", normalized_text):
            token = match.group(0).strip()
            found[self._designation_key(token)] = token
        return found

    def _designation_regex(self, designation: str) -> str:
        chunks = re.findall(r"[A-Za-z]+|\d+", designation or "")
        if not chunks:
            return r"$^"
        return r"\b" + r"[\s-]*".join(re.escape(chunk) for chunk in chunks) + r"\b"

    def _restore_designations(self, text: str, source_title: str, source_body: str = "") -> str:
        out = text or ""
        source_designations = self._extract_designations(f"{source_title} {source_body}")
        if not out or not source_designations:
            return out
        for designation in source_designations.values():
            out = re.sub(self._designation_regex(designation), designation, out, flags=re.IGNORECASE)
        return out

    def _credibility_profile(self, source_title: str, source_body: str) -> Dict[str, bool]:
        text = f" {self._normalize_acronyms(source_title)} {self._normalize_acronyms(source_body)} ".lower()
        has_images = any(token in text for token in (" image ", " images ", " footage ", " video "))
        has_uncertainty = any(marker in text for marker in self._UNCERTAINTY_MARKERS)
        image_claim = has_images and any(marker in text for marker in ("appear to show", "appears to show", "circulating online", "viral"))
        return {
            "needs_caution": has_uncertainty or image_claim,
            "image_claim": image_claim,
        }

    def _has_caution_marker(self, text: str) -> bool:
        low = f" {(text or '').lower()} "
        return any(marker in low for marker in self._CAUTION_OUTPUT_MARKERS)

    def _source_has_positive_verification(self, source_title: str, source_body: str) -> bool:
        text = f" {self._normalize_acronyms(source_title)} {self._normalize_acronyms(source_body)} ".lower()
        if "not independently verified" in text or "remain unconfirmed" in text or "remains unconfirmed" in text:
            return False
        return any(marker in text for marker in self._POSITIVE_VERIFICATION_MARKERS)

    def _introduces_false_verification(self, title: str, body: str, source_title: str, source_body: str) -> bool:
        if self._source_has_positive_verification(source_title, source_body):
            return False
        output = f" {self._normalize_acronyms(title)} {self._normalize_acronyms(body)} ".lower()
        return any(marker in output for marker in self._POSITIVE_VERIFICATION_MARKERS)

    def _first_cautious_source_sentence(self, source_title: str, source_body: str) -> str:
        candidates = [source_title] + self._split_sentences(source_body)
        for candidate in candidates:
            normalized = self._normalize_body_punctuation(candidate)
            if any(marker in normalized.lower() for marker in self._UNCERTAINTY_MARKERS):
                return self._restore_designations(normalized, source_title, source_body)
        return ""

    def _enforce_cautious_body_framing(self, body: str, source_title: str, source_body: str) -> str:
        profile = self._credibility_profile(source_title, source_body)
        out = body or ""
        if not profile["needs_caution"] or self._has_caution_marker(out):
            return out

        safe_lead = self._first_cautious_source_sentence(source_title, source_body)
        if not safe_lead:
            return out

        rebuilt: List[str] = [safe_lead]
        for sentence in self._split_sentences(out):
            normalized = self._normalize_body_punctuation(sentence)
            if SequenceMatcher(None, normalized.lower(), safe_lead.lower()).ratio() >= 0.72:
                continue
            if self._is_duplicate_sentence(normalized, rebuilt):
                continue
            rebuilt.append(normalized)
        return self._normalize_body_punctuation(" ".join(rebuilt))

    def _enforce_cautious_title(self, title: str, source_title: str, source_body: str, max_title: int) -> str:
        profile = self._credibility_profile(source_title, source_body)
        out = self._restore_designations(title, source_title, source_body)
        source_designations = self._extract_designations(f"{source_title} {source_body}")
        if len(source_designations) == 1:
            expected_designation = next(iter(source_designations.values()))
            if not re.search(self._designation_regex(expected_designation), out, flags=re.IGNORECASE):
                for generic_label in self._GENERIC_DESIGNATION_WORDS:
                    candidate = re.sub(
                        rf"\b{re.escape(generic_label)}\b",
                        expected_designation,
                        out,
                        count=1,
                        flags=re.IGNORECASE,
                    )
                    if candidate != out:
                        out = candidate
                        break
        if not profile["needs_caution"] or self._has_caution_marker(out):
            return self._smart_truncate_title(out, max_title)

        source_clean = self._normalize_acronyms(self._clean_title_noise(source_title))
        source_clean = re.sub(r"\bImages circulating online\b", "Online images", source_clean, flags=re.IGNORECASE)
        source_clean = re.sub(r"\bappear to show significant damage\b", "appear to show damage", source_clean, flags=re.IGNORECASE)
        source_clean = self._normalize_title_punctuation(source_clean)
        source_clean = self._remove_title_commas(source_clean)
        source_clean = self._restore_designations(
            self._restore_proper_nouns(self._to_sentence_case_headline(source_clean), source_title, source_body),
            source_title,
            source_body,
        )
        return self._smart_truncate_title(source_clean, max_title)

    def _has_designation_drift(self, title: str, body: str, source_title: str, source_body: str) -> bool:
        source_designations = self._extract_designations(f"{source_title} {source_body}")
        if not source_designations:
            return False
        output_designations = self._extract_designations(f"{title} {body}")
        source_keys = set(source_designations.keys())
        return any(key not in source_keys for key in output_designations)

    def _passes_credibility_checks(self, title: str, body: str, source_title: str, source_body: str) -> bool:
        profile = self._credibility_profile(source_title, source_body)
        if profile["needs_caution"] and not self._has_caution_marker(f"{title} {body}"):
            return False
        source_designations = self._extract_designations(f"{source_title} {source_body}")
        if len(source_designations) == 1:
            expected_designation = next(iter(source_designations.values()))
            combined_output = self._normalize_acronyms(f"{title} {body}")
            if not re.search(self._designation_regex(expected_designation), combined_output, flags=re.IGNORECASE):
                return False
            if profile["image_claim"]:
                normalized_title = self._normalize_acronyms(title)
                if not re.search(self._designation_regex(expected_designation), normalized_title, flags=re.IGNORECASE):
                    return False
        if self._introduces_false_verification(title, body, source_title, source_body):
            return False
        if self._has_designation_drift(title, body, source_title, source_body):
            return False
        return True

    def _credibility_prompt_note(self, source_title: str, source_body: str) -> str:
        profile = self._credibility_profile(source_title, source_body)
        if not profile["needs_caution"]:
            return ""
        if profile["image_claim"]:
            return (
                "Credibility note: The source describes an image-based or online claim. "
                "Keep that uncertainty explicit and do not present the claim as confirmed fact."
            )
        return (
            "Credibility note: The source uses tentative or unverified wording. "
            "Preserve that caution clearly in the final copy."
        )

    def _normalize_title_punctuation(self, title: str) -> str:
        out = self._ascii_punct(" ".join((title or "").split()))
        if not out:
            return out
        out = re.sub(r"\s+([,.;:!?])", r"\1", out)
        out = re.sub(
            r",\s+(escalates|intensifies|deepens|widens|triggers|raises|sparks)\b",
            lambda m: f": {m.group(1)}",
            out,
            flags=re.IGNORECASE,
        )
        return out.strip()

    def _remove_title_commas(self, title: str) -> str:
        out = " ".join((title or "").split())
        if not out:
            return out
        # The CMS forbids commas in titles. The model is told to avoid them and to
        # use a colon for an intentional separator, so a stray comma here is almost
        # always a list separator — replace it with a space, never a colon (which
        # produced artefacts like "central: Western Iran").
        out = re.sub(r"\s*,\s*", " ", out)
        out = re.sub(r"\s*:\s*:\s*", ": ", out)
        out = re.sub(r"\s{2,}", " ", out)
        return out.strip(" ,.-:")

    def _to_sentence_case_headline(self, title: str) -> str:
        out = " ".join((title or "").split())
        if not out:
            return out

        out = out[0].upper() + out[1:] if out else out

        for acr in sorted(self._ALWAYS_UPPER, key=len, reverse=True):
            out = re.sub(rf"\b{re.escape(acr)}\b", acr.upper(), out, flags=re.IGNORECASE)

        def _cap_after_colon(match: re.Match) -> str:
            return f": {match.group(1).upper()}"

        out = re.sub(r":\s*([a-z])", _cap_after_colon, out)
        return out

    def _restore_proper_nouns(self, generated_title: str, source_title: str, source_body: str = "") -> str:
        out = generated_title or ""
        src = f"{source_title or ''} {source_body or ''}".strip()
        if not out or not src:
            return out

        # Only treat a word as a proper noun if it appears capitalised
        # MID-sentence in the source. Sentence-initial capitals ("Paper setters
        # are kept...") are NOT proper nouns and must not be force-capitalised
        # everywhere in the title (which produced "Paper setters", "Maritime Rescue").
        proper_map: Dict[str, str] = {}
        for sentence in self._split_sentences(src):
            words = sentence.split()
            for idx, raw in enumerate(words):
                token = re.sub(r"[^A-Za-z'\u2019-]", "", raw)
                if len(token) < 3 or token.isupper():
                    continue
                if idx == 0:
                    continue  # skip the first word of each sentence
                if token[0].isupper():
                    proper_map[token.lower()] = token

        for token in self._COMMON_PROPER_NOUNS:
            proper_map.setdefault(token.lower(), token[:1].upper() + token[1:])
        for low, canon in proper_map.items():
            out = re.sub(rf"\b{re.escape(low)}\b", canon, out, flags=re.IGNORECASE)
        return out

    def _has_title_hook(self, title: str) -> bool:
        clean = " ".join((title or "").split())
        if not clean:
            return False
        words = clean.split()
        if len(words) < 4 or len(words) > 14:
            return False
        low = clean.lower()
        if low.startswith(("the ", "a ", "an ", "in ", "on ")):
            return False
        has_action = any(re.search(rf"\b{re.escape(v)}\b", low) for v in self._ACTION_VERBS)
        has_hook_token = any(re.search(rf"\b{re.escape(v)}\b", low) for v in self._HOOK_TOKENS)
        has_interest_signal = (
            bool(re.search(r"\b\d+\b", clean))
            or ("'" in clean)
            or (":" in clean)
            or ("," in clean)
            or ("?" in clean)
        )
        return has_action or has_hook_token or has_interest_signal

    def _has_body_hook(self, body: str) -> bool:
        clean = " ".join((body or "").split())
        if not clean:
            return False
        first = re.split(r"(?<=[.!?])\s+", clean)[0]
        if len(first) < 45:
            return False
        low = first.lower()
        has_number = bool(re.search(r"\b\d+\b", first))
        has_action = any(re.search(rf"\b{re.escape(v)}\b", low) for v in self._ACTION_VERBS)
        return has_number or has_action

    def _looks_template_body(self, body: str) -> bool:
        clean = " ".join((body or "").split())
        if not clean:
            return True
        low = clean.lower()
        this_starts = len(re.findall(r"(?<![a-z])this\s", low))
        generic_hits = sum(1 for p in self._GENERIC_BODY_PHRASES if p in low)
        return this_starts >= 2 or generic_hits >= 2

    def _has_source_boilerplate(self, body: str) -> bool:
        low = " ".join((body or "").split()).lower()
        if not low:
            return False
        return any(re.search(pattern, low, flags=re.IGNORECASE) for pattern in self._SOURCE_BOILERPLATE_PATTERNS)

    _UNICODE_PUNCT = {
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
        "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
        "“": '"', "”": '"', "„": '"', "″": '"',
        "…": "...", " ": " ", " ": " ", " ": " ", "​": "",
    }

    def _ascii_punct(self, text: str) -> str:
        """Convert smart hyphens, curly quotes and friends to plain ASCII."""
        if not text:
            return text
        for needle, repl in self._UNICODE_PUNCT.items():
            if needle in text:
                text = text.replace(needle, repl)
        return text

    def _normalize_body_punctuation(self, body: str) -> str:
        text = self._ascii_punct(" ".join((body or "").split()))
        if not text:
            return text
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        text = re.sub(r"\s*-\s*(?:The\s+Times\s+of\s+India|Times\s+of\s+India|India\s+Today|BBC\s+News|Al\s*Jazeera)\.?", ".", text, flags=re.IGNORECASE)
        text = re.sub(r"(?:\b(?:The\s+Times\s+of\s+India|Times\s+of\s+India|India\s+Today|BBC\s+News|Al\s*Jazeera)\b\.?\s*)+", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+'", " '", text)
        text = re.sub(r"'\s+", "' ", text)
        # "US.-Iran" / "US." before a capital or hyphen → "US"
        text = re.sub(r"\bUS\.(?=[-A-Z])", "US", text)
        if text[-1] not in ".!?":
            text = f"{text}."
        text = re.sub(r"\.\.+", ".", text)
        return text

    def _source_mentions_acronym(self, acronym: str, source_title: str, source_body: str) -> bool:
        return re.search(rf"\b{re.escape(acronym)}\b", f"{source_title} {source_body}", flags=re.IGNORECASE) is not None

    def _add_first_mention_acronyms(self, text: str, source_title: str, source_body: str) -> str:
        out = text
        for long_form, acronym in self._LONGFORM_TO_ACRONYM.items():
            if not self._source_mentions_acronym(acronym, source_title, source_body):
                continue
            pattern = rf"\b{re.escape(long_form)}\b(?!\s*\({re.escape(acronym)}\))"
            out = re.sub(pattern, f"{long_form} ({acronym})", out, count=1, flags=re.IGNORECASE)
        return out

    def _preferred_trump_reference(self, source_title: str, source_body: str) -> str:
        source_text = self._normalize_acronyms(f"{source_title} {source_body}")
        if re.search(r"\bDonald Trump\b", source_text, flags=re.IGNORECASE):
            return "Donald Trump"
        return "Trump"

    def _clean_actor_artifacts(self, text: str, source_title: str, source_body: str) -> str:
        out = text or ""
        source_text = self._normalize_acronyms(f"{source_title} {source_body}")

        if "trump" in source_text.lower():
            preferred = self._preferred_trump_reference(source_title, source_body)
            out = re.sub(r"\bformer\s+Trump\s+Donald\s+Trump\b", preferred, out, flags=re.IGNORECASE)
            out = re.sub(r"\bTrump\s+Donald\s+Trump\b", preferred, out, flags=re.IGNORECASE)
            out = re.sub(r"\bDonald\s+Trump\s+Donald\s+Trump\b", "Donald Trump", out, flags=re.IGNORECASE)
            out = re.sub(r"\bformer\s+Donald\s+Trump\b", preferred, out, flags=re.IGNORECASE)
            out = re.sub(r"\bTrump\s+Trump\b", "Trump", out, flags=re.IGNORECASE)

        return re.sub(r"\s+", " ", out).strip()

    def _inject_named_actor(self, text: str, source_title: str, source_body: str) -> str:
        out = text
        source_low = f"{source_title} {source_body}".lower()

        if "trump" in source_low:
            preferred_trump = self._preferred_trump_reference(source_title, source_body)
            out = re.sub(
                r"\b(?:former\s+)?(?:the\s+)?u\.?s\.?\s+president(?:\s+donald\s+trump)?\b",
                preferred_trump,
                out,
                count=1,
                flags=re.IGNORECASE,
            )
            out = re.sub(r"\bformer\s+president\s+donald\s+trump\b", preferred_trump, out, count=1, flags=re.IGNORECASE)
            out = re.sub(r"\bPresident\s+Donald\s+Trump\b", preferred_trump, out, count=1, flags=re.IGNORECASE)
            out = re.sub(r"\bPresident Trump\b", preferred_trump, out, count=1, flags=re.IGNORECASE)
            out = self._clean_actor_artifacts(out, source_title, source_body)

        kharge_match = re.search(r"\bkharge\b", f"{source_title} {source_body}", flags=re.IGNORECASE)
        if kharge_match:
            out = re.sub(r"\b(?:the\s+)?congress president\b", "Congress president Kharge", out, count=1, flags=re.IGNORECASE)

        return out

    def _is_low_value_sentence(self, sentence: str) -> bool:
        low = sentence.lower().strip()
        if not low:
            return True
        return any(re.search(pattern, low, flags=re.IGNORECASE) for pattern in self._LOW_VALUE_SENTENCE_PATTERNS)

    def _looks_broken_sentence(self, sentence: str) -> bool:
        normalized = self._normalize_body_punctuation(sentence)
        if not normalized:
            return True
        if not normalized.endswith((".", "!", "?")):
            return True
        if self._has_dangling_tail(normalized):
            return True

        low = normalized.lower()
        if re.search(
            r"\b(?:comes|came|falls|fell|rises|rose|surges|surged|jumps|jumped|opens|opened|follows|followed|starts|started)"
            r"\s+(?:as|after|before|because|since|while|when|if)\s+(?:the|a|an|his|her|their|its|this|that)?\s*"
            r"(?:us|uk|eu|un|uae|government|military|president|trump|biden|iran|israel|india|russia|china|officials?)\.$",
            low,
        ):
            return True
        if re.match(
            r"^(?:since|after|before|amid|during|as)\s+(?:the\s+)?[a-z0-9-]+(?:\s+[a-z0-9-]+){0,4}[.!?]$",
            low,
        ):
            return True
        return False

    def _clean_body_copy(self, body: str, source_title: str, source_body: str) -> str:
        out = self._normalize_body_punctuation(body)
        out = self._normalize_acronyms(out)
        out = re.sub(r"\bTwenty20\b", "T20", out, flags=re.IGNORECASE)
        out = self._inject_named_actor(out, source_title, source_body)
        out = self._clean_actor_artifacts(out, source_title, source_body)
        out = self._add_first_mention_acronyms(out, source_title, source_body)
        out = self._restore_designations(out, source_title, source_body)
        out = re.sub(r"\bThe statement follows\b", "This follows", out, count=1, flags=re.IGNORECASE)
        out = re.sub(r"\bThe Congress leader\b", "Kharge", out, count=1, flags=re.IGNORECASE)
        out = self._enforce_cautious_body_framing(out, source_title, source_body)

        cleaned_sentences: List[str] = []
        for sentence in self._split_sentences(out):
            normalized = self._normalize_body_punctuation(sentence)
            if re.match(r"^[a-z]", normalized):
                continue
            if self._is_low_value_sentence(normalized):
                continue
            if self._looks_broken_sentence(normalized):
                continue
            if self._is_duplicate_sentence(normalized, cleaned_sentences):
                continue
            cleaned_sentences.append(normalized)

        while len(cleaned_sentences) > 1 and self._is_weak_ending_sentence(cleaned_sentences[-1], title=source_title):
            candidate = self._normalize_body_punctuation(" ".join(cleaned_sentences[:-1]))
            if len(cleaned_sentences) <= 3 or len(candidate) < 220:
                break
            cleaned_sentences.pop()

        out = self._normalize_body_punctuation(" ".join(cleaned_sentences))
        out = self._restore_designations(out, source_title, source_body)
        out = self._enforce_cautious_body_framing(out, source_title, source_body)
        out = self._clean_actor_artifacts(out, source_title, source_body)
        return self._normalize_body_punctuation(out)

    def _body_too_close_to_source(self, body: str, source_body: str) -> bool:
        body_sentences = self._split_sentences(body)
        source_sentences = self._split_sentences(source_body)
        if not body_sentences or not source_sentences:
            return False

        normalized_body = " ".join((body or "").split()).lower()
        normalized_source = " ".join((source_body or "").split()).lower()
        if SequenceMatcher(None, normalized_body, normalized_source).ratio() >= 0.80:
            return True

        # Any single sentence lifted near-verbatim from the source is unacceptable.
        for sentence in body_sentences:
            normalized_sentence = self._normalize_body_punctuation(sentence).lower()
            if len(normalized_sentence) < 40:
                continue
            for source_sentence in source_sentences:
                normalized_source_sentence = self._normalize_body_punctuation(source_sentence).lower()
                if SequenceMatcher(None, normalized_sentence, normalized_source_sentence).ratio() >= 0.86:
                    return True
        return False

    def _source_context_tail(self, source_title: str, source_body: str) -> str:
        text = f" {source_title} {source_body} ".lower()
        if "hormuz" in text:
            return "The move keeps pressure on Iran over reopening the Strait of Hormuz."
        if any(token in text for token in {"gold", "bullion", "silver", "market volatility"}):
            return "The sharp move kept attention on how global volatility is feeding into local bullion prices."
        if any(token in text for token in {"court", "judge", "ruling", "case"}):
            return "The ruling is likely to keep the case under close watch in the coming days."
        if any(token in text for token in {"deportees", "deportation", "third-country"}):
            return "The deal adds to Washington's options for third-country deportations."
        if any(token in text for token in {"ipl", "bcci", "t20"}):
            return "The announcement sets the stage for the new season later this month."
        if any(token in text for token in {"troops", "lebanon", "sovereignty", "invasion"}):
            return "The warning adds to pressure over the widening ground operation in the region."
        return "The development is likely to keep the issue under close watch in the coming days."

    def _smart_truncate_title(self, title: str, max_title: int) -> str:
        out = " ".join((title or "").split()).strip(" ,.-:")
        if len(out) <= max_title:
            return out

        clipped = out[: max_title + 1]
        cut_points = [
            clipped.rfind(": "),
            clipped.rfind(" - "),
            clipped.rfind(" "),
        ]
        cut = max(cut_points)
        if cut >= max_title - 12:
            return clipped[:cut].rstrip(" ,.-:")
        return out[:max_title].rstrip(" ,.-:")

    def _clean_title_copy(self, title: str, source_title: str, source_body: str = "") -> str:
        out = self._normalize_acronyms(title)
        out = self._inject_named_actor(out, source_title, source_body)
        for pattern, repl in self._TITLE_STYLE_REPLACEMENTS:
            out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
        out = re.sub(r"^(US|UK|EU|UN|UAE|NATO):\s*(US|UK|EU|UN|UAE|NATO):\s*(US|UK|EU|UN|UAE|NATO)\b", r"\1, \2, \3", out)
        if "member states" in f"{source_title} {source_body}".lower():
            out = re.sub(r"\bUS,\s*UK,\s*EU\b", "US, UK, EU members", out, count=1, flags=re.IGNORECASE)
        out = re.sub(r"\s+", " ", out)
        return out

    def _retitle_from_source(self, source_title: str, source_body: str, max_title: int) -> str:
        candidates: List[str] = []

        boosted = self._boost_title_punch(source_title, source_title, source_body, max_title=max_title)
        if boosted:
            candidates.append(boosted)

        source_sentences = self._split_sentences(source_body)
        for sentence in source_sentences[:3]:
            normalized = self._normalize_body_punctuation(sentence).strip()
            if len(normalized) < 24:
                continue
            for chunk in re.split(r"[,:;]\s+", normalized):
                candidate = chunk.strip(" .,-")
                if len(candidate) < 16:
                    continue
                candidate = self._clean_title_noise(candidate)
                candidate = self._clean_title_copy(candidate, source_title, source_body)
                candidate = self._normalize_title_punctuation(candidate)
                candidate = self._remove_title_commas(candidate)
                candidate = self._restore_designations(
                    self._restore_proper_nouns(self._to_sentence_case_headline(candidate), source_title, source_body),
                    source_title,
                    source_body,
                )
                candidate = self._enforce_cautious_title(candidate, source_title, source_body, max_title)
                candidate = self._smart_truncate_title(candidate, max_title)
                if candidate:
                    candidates.append(candidate)

        fallback = self._title_from_source(source_title, max_title=max_title, source_body=source_body)
        if fallback:
            candidates.append(fallback)

        seen: set[str] = set()
        for candidate in candidates:
            key = candidate.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            if not self._title_too_close_to_source(candidate, source_title):
                return candidate

        return candidates[0] if candidates else ""

    _VAGUE_TITLE_PATTERNS = (
        r"\b(?:dominant|strong|impressive|commanding|brilliant|stunning|big|major|huge|massive|great)\s+"
        r"(?:performance|showing|win|victory|display|effort|result|moment|day)\b",
        r"\b(?:delivers?|delivered|produces?|puts?\s+on|makes?)\s+(?:a\s+)?"
        r"(?:dominant|strong|commanding|big|major|great|fine)\b",
        r"\bmakes?\s+(?:a\s+)?(?:big|major|key|bold|surprise)\s+(?:move|decision|statement|announcement)\b",
        r"\b(?:key|major|big|important)\s+(?:development|update|move)\b$",
    )

    def _is_vague_title(self, title: str) -> bool:
        low = " ".join((title or "").split()).lower()
        if not low:
            return False
        return any(re.search(pattern, low) for pattern in self._VAGUE_TITLE_PATTERNS)

    _TITLE_BAD_STARTS = (
        "and", "but", "or", "so", "nor", "yet", "because", "also", "however",
        "he", "she", "it", "they", "we", "you", "his", "her", "its", "their",
        "this", "that", "these", "those", "him", "them",
    )

    def _significant_words(self, text: str) -> set[str]:
        words = set()
        for token in re.findall(r"[a-z0-9]{3,}", (text or "").lower()):
            if token not in self._STYLE_STOPWORDS:
                words.add(token)
        return words

    def _title_matches_lede(self, title: str, body: str) -> bool:
        """True if the title shares enough content with the body's first sentence."""
        sentences = self._split_sentences(body or "")
        if not sentences:
            return True
        title_words = self._significant_words(title)
        lede_words = self._significant_words(sentences[0])
        if not title_words or not lede_words:
            return True
        overlap = len(title_words & lede_words)
        # A real headline restates the lede's subject/action — expect 2+ shared
        # significant words (or 1 when the title is very short).
        return overlap >= 2 or (overlap >= 1 and len(title_words) <= 4)

    def _title_starts_bad(self, title: str) -> bool:
        clean = " ".join((title or "").split())
        if not clean:
            return True
        first = re.sub(r"[^a-z]", "", clean.split()[0].lower())
        return first in self._TITLE_BAD_STARTS

    def _is_bad_title(self, title: str, body: str) -> bool:
        clean = " ".join((title or "").split())
        if not clean:
            return True
        if self._title_starts_bad(clean):
            return True
        if not self._title_matches_lede(clean, body):
            return True
        return False

    def _title_well_formed(self, title: str) -> bool:
        """A rebuilt title is acceptable if it names a subject and is specific,
        even when the body's own lede is weak."""
        return bool(title) and not self._title_starts_bad(title) and not self._is_vague_title(title)

    def _lede_subject(self, source_title: str, body: str) -> str:
        """The main subject (first proper noun) to lead a headline with."""
        for text in (source_title, body):
            for token in re.findall(r"\b[A-Z][A-Za-z]{2,}\b", text or ""):
                if token.lower() in self._STYLE_STOPWORDS:
                    continue
                return token
        return ""

    def _title_from_body_lede(self, body: str, max_title: int, source_title: str = "") -> str:
        sentences = self._split_sentences(body or "")
        if not sentences:
            return ""

        # Pick the most newsworthy sentence (numbers + named entities win), not
        # necessarily the first — the model sometimes leads with a minor detail.
        def score(sentence: str) -> int:
            sc = 0
            if re.search(r"\d", sentence):
                sc += 3
            sc += min(len(re.findall(r"\b[A-Z][a-z]{2,}\b", sentence)), 3)
            if re.match(r"^\s*(?:He|She|It|They|His|Her|Its|Their|This|That)\b", sentence):
                sc -= 2
            return sc

        best = max(sentences, key=score)
        lede = best.rstrip(" .!?")

        # Strip a leading pronoun (+ adverb) and name the actual subject.
        m = re.match(r"^\s*(?:He|She|It|They)\s+(?:also|then|now|later|reportedly|further)?\s*", lede)
        if m:
            subject = self._lede_subject(source_title, body)
            rest = lede[m.end():].strip()
            lede = f"{subject} {rest}".strip() if subject else rest

        # Drop a trailing subordinate clause to keep the headline tight.
        for sep in (", after ", " after ", ", following ", ", as ", ", with ", ", and ", " located ", " to limit ", " aimed at "):
            idx = lede.find(sep)
            if 18 < idx < max_title + 25:
                lede = lede[:idx]
                break

        lede = self._remove_title_commas(self._normalize_title_punctuation(lede))
        lede = self._to_sentence_case_headline(lede)
        if len(lede) > max_title:
            lede = self._smart_truncate_title(lede, max_title)
        return lede.strip(" ,.-:")

    def _title_too_close_to_source(self, title: str, source_title: str) -> bool:
        clean_title = re.sub(r"[^a-z0-9\s]", "", (title or "").lower()).split()
        clean_source = re.sub(r"[^a-z0-9\s]", "", (source_title or "").lower()).split()
        if not clean_title or not clean_source:
            return False
        if clean_title == clean_source:
            return True
        if len(clean_title) >= 4 and len(clean_source) >= 4 and clean_title[:4] == clean_source[:4]:
            return True
        joined_title = " ".join(clean_title)
        joined_source = " ".join(clean_source)
        if joined_title in joined_source or joined_source in joined_title:
            return True

        content_title = [token for token in clean_title if token not in self._TITLE_COMPARISON_STOPWORDS]
        content_source = [token for token in clean_source if token not in self._TITLE_COMPARISON_STOPWORDS]
        if content_title and content_source:
            title_set = set(content_title)
            source_set = set(content_source)
            overlap = len(title_set & source_set)
            shorter = min(len(title_set), len(source_set))
            if shorter and overlap >= max(shorter - 1, 3):
                return True
            union = len(title_set | source_set)
            if union and (overlap / union) >= 0.75:
                return True

        similarity = SequenceMatcher(None, " ".join(clean_title), " ".join(clean_source)).ratio()
        return similarity >= 0.82

    # Attributive adjectives / quantifiers that essentially never end a real
    # sentence — if the body ends on one, a trailing noun was dropped (truncated).
    _DANGLING_ADJECTIVES = frozenset({
        "global", "international", "national", "domestic", "regional", "local",
        "central", "federal", "potential", "ongoing", "various", "several",
        "multiple", "other", "such", "key", "major", "minor", "recent", "general",
        "overall", "total", "annual", "monthly", "daily", "initial", "final",
        "further", "additional", "economic", "political", "social", "financial",
        "military", "digital", "senior", "junior", "former", "current", "upcoming",
        "alleged", "possible", "likely", "rising", "growing", "broader", "wider",
        "the", "a", "an", "this", "that", "these", "those", "his", "her", "their", "its",
    })

    def _has_dangling_tail(self, body: str) -> bool:
        text = " ".join((body or "").split()).lower()
        if not text:
            return False
        # Trailing attributive adjective / determiner → a noun was clearly cut off.
        last_word = re.search(r"([a-z]+)\.$", text)
        if last_word and last_word.group(1) in self._DANGLING_ADJECTIVES:
            return True
        if re.search(r"\b(?:in|on|at|to|for|from|with|by|of|as|into|over|under|about|between|through|across|and|or|but|so|yet)\.$", text):
            return True
        # A bare hyphenated attributive compound at the very end almost always
        # means the noun was dropped: "...cross-border.", "...two-state.",
        # "...post-poll.". Allow a few genuine noun compounds.
        tail_compound = re.search(r"\b([a-z]{2,}-[a-z]{2,})\.$", text)
        if tail_compound and tail_compound.group(1) not in {
            "runner-up", "follow-up", "build-up", "set-up", "start-up", "stand-off", "cease-fire",
            "year-old", "year-olds", "years-old",
        }:
            return True
        # A relative clause cut off after one short token, e.g. "...that UN.",
        # "...which RBI." (the clause's verb/object was dropped).
        if re.search(r"\b(?:that|which|where|whose|whom|when|who)\s+[A-Za-z.&]{1,5}\.$", text):
            return True
        # A trailing appositive fragment, e.g. "...bus stands, a move." — the
        # describing clause ("a move aimed at...") was cut off.
        if re.search(r",\s+(?:a|an|the)\s+(?:move|step|decision|measure|gesture|sign|shift|development|bid|push|ploy|effort)\.$", text):
            return True
        if re.search(
            r"\b(?:in|on|at|to|for|from|with|by|of|as|into|over|under|about|between|through|across|and|or|but|so|yet)\s+"
            r"(?:the|a|an|his|her|their|its|this|that|they|he|she|we|you)\.$",
            text,
        ):
            return True
        if re.search(
            r"\b(?:comes|came|falls|fell|rises|rose|surges|surged|jumps|jumped|opens|opened|follows|followed|starts|started)"
            r"\s+(?:as|after|before|because|since|while|when|if)\s+(?:the|a|an|his|her|their|its|this|that)?\s*"
            r"(?:[a-z0-9.-]+\s*){0,2}\.$",
            text,
        ):
            return True
        return False

    def _trim_dangling_clause(self, sentence: str) -> str:
        """Cut a trailing incomplete clause so the sentence ends complete.

        e.g. "...internal crises and charging Islamabad with cross-border." ->
        "...internal crises." — preserving the rest instead of dropping it all.
        """
        s = (sentence or "").rstrip(" .!?")
        for sep in (", and ", " and ", "; ", ", while ", " while ", ", with ", ", citing ", " that ", " which ", ", "):
            idx = s.rfind(sep)
            if idx > 40:
                candidate = s[:idx].rstrip(" ,;-") + "."
                if not self._has_dangling_tail(candidate):
                    return candidate
        return ""

    def _ensure_complete_body(self, body: str, source_title: str, source_body: str, min_chars: int, max_chars: int) -> str:
        text = self._normalize_body_punctuation(body)
        if not text or not self._has_dangling_tail(text):
            return text

        sentences = self._split_sentences(text)
        # First try to repair the last sentence by trimming only its dangling
        # trailing clause — keeps the lede instead of dropping a whole sentence.
        if sentences:
            repaired_last = self._trim_dangling_clause(sentences[-1])
            if repaired_last:
                candidate = " ".join(sentences[:-1] + [repaired_last]).strip()
                if not self._has_dangling_tail(candidate) and len(candidate) >= min_chars - 30:
                    return self._normalize_body_punctuation(candidate)

        # Otherwise drop the dangling final sentence (sentence-aware so decimals
        # like "7.2%" and scores like "5-1" are not mistaken for boundaries).
        if len(sentences) > 1:
            text = " ".join(sentences[:-1]).strip()
        else:
            text = ""

        if len(text) < min_chars:
            text = self._expand_body(text, source_title, source_body, target_chars=max(min_chars, 320), max_chars=max_chars)
            text = self._normalize_body_punctuation(text)

        if len(text) > max_chars:
            text = self._trim_body_to_band(text, max_chars=max_chars, target_chars=max(min_chars, 320))
            text = self._normalize_body_punctuation(text)

        if self._has_dangling_tail(text):
            text = re.sub(
                r"\b(?:in|on|at|to|for|from|with|by|of|as|into|over|under|about|between|through|across|and|or|but|so|yet)\.?$",
                "",
                text,
            ).rstrip(" ,:-")
            if not text.endswith((".", "!", "?")):
                text = f"{text}."

        return text

    def _split_sentences(self, text: str) -> List[str]:
        clean = " ".join((text or "").split())
        if not clean:
            return []
        parts = re.split(r"(?<=[.!?])\s+", clean)
        return [part.strip() for part in parts if part.strip()]

    def _sentence_signature(self, text: str) -> frozenset[str]:
        return self._tokenize_style_text(text)

    def _title_overlap(self, sentence: str, title: str) -> int:
        return len(self._sentence_signature(sentence) & self._sentence_signature(title))

    def _is_weak_ending_sentence(self, sentence: str, title: str = "") -> bool:
        normalized = self._normalize_body_punctuation(sentence)
        if not normalized:
            return False
        low = normalized.lower()
        if re.search(r"\b\d+\b", normalized):
            return False
        if any(re.search(pattern, low, flags=re.IGNORECASE) for pattern in self._WEAK_ENDING_PATTERNS):
            return True
        if any(low.startswith(prefix) for prefix in self._GENERIC_SENTENCE_STARTS) and len(normalized) <= 150:
            return True
        title_overlap = self._title_overlap(normalized, title)
        consequence_heavy = sum(1 for token in self._CONSEQUENCE_TOKENS if token in low)
        if consequence_heavy >= 2 and title_overlap <= 2 and not any(token in low for token in self._ACTION_VERBS):
            return True
        return False

    def _trim_weak_ending_sentence(self, body: str, source_title: str, source_body: str, min_chars: int, max_chars: int) -> str:
        text = self._normalize_body_punctuation(body)
        if not text:
            return text

        sentences = self._split_sentences(text)
        changed = False
        while len(sentences) > 1 and self._is_weak_ending_sentence(sentences[-1], title=source_title):
            candidate = self._normalize_body_punctuation(" ".join(sentences[:-1]))
            if not candidate:
                break
            sentences.pop()
            text = candidate
            changed = True

        if changed and len(text) < min_chars:
            text = self._expand_body(text, source_title, source_body, target_chars=max(min_chars, 320), max_chars=max_chars)
            text = self._normalize_body_punctuation(text)

        return text

    def _strengthen_body_coverage(
        self,
        body: str,
        source_title: str,
        source_body: str,
        min_chars: int,
        target_chars: int,
        max_chars: int,
    ) -> str:
        out = self._normalize_body_punctuation(body)
        existing = self._split_sentences(out)

        def _near_duplicate_in_base(candidate: str, base: List[str]) -> bool:
            candidate_norm = self._normalize_body_punctuation(candidate).lower()
            for sentence in base:
                existing_norm = self._normalize_body_punctuation(sentence).lower()
                if candidate_norm == existing_norm:
                    return True
                if SequenceMatcher(None, candidate_norm, existing_norm).ratio() >= 0.9:
                    return True
            return False

        ranked: List[tuple[int, int, str]] = []
        for idx, sentence in enumerate(self._split_sentences(source_body)):
            normalized = self._normalize_body_punctuation(sentence)
            if len(normalized) < 35:
                continue
            if _near_duplicate_in_base(normalized, existing):
                continue

            low = normalized.lower()
            score = self._sentence_quality_score(normalized, title=source_title, position=idx)
            if re.search(r"\b\d+\b", normalized):
                score += 3
            if any(token in low for token in self._ACTION_VERBS):
                score += 2
            if any(token in low for token in self._CONSEQUENCE_TOKENS):
                score += 4
            if any(low.startswith(prefix) for prefix in self._GENERIC_SENTENCE_STARTS):
                score -= 2
            ranked.append((score, idx, normalized))

        for _, _, candidate in sorted(ranked, key=lambda item: (-item[0], item[1])):
            proposal = f"{out} {candidate}".strip() if out else candidate
            if len(proposal) > max_chars:
                continue
            out = proposal
            existing.append(candidate)
            if len(existing) >= 3 and len(out) >= min(min_chars, max(220, target_chars - 40)):
                break

        if existing:
            source_positions = {
                self._normalize_body_punctuation(sentence): idx
                for idx, sentence in enumerate(self._split_sentences(source_body))
            }

            title_like = sorted(
                [
                    sentence
                    for sentence in existing
                    if self._title_overlap(sentence, source_title) >= 4
                ],
                key=lambda sentence: -self._title_overlap(sentence, source_title),
            )
            for removable in title_like:
                base = [sentence for sentence in existing if sentence != removable]
                for score, idx, candidate in sorted(ranked, key=lambda item: (-item[0], item[1])):
                    if candidate in base:
                        continue
                    if _near_duplicate_in_base(candidate, base):
                        continue
                    if self._title_overlap(candidate, source_title) >= self._title_overlap(removable, source_title):
                        continue
                    proposal_sentences = sorted(
                        base + [candidate],
                        key=lambda sentence: source_positions.get(sentence, 999),
                    )
                    proposal = " ".join(proposal_sentences)
                    if len(proposal) > max_chars:
                        continue
                    out = proposal
                    existing = proposal_sentences
                    break
                else:
                    continue
                break

            if len(existing) >= 3:
                removable = max(existing, key=lambda sentence: self._title_overlap(sentence, source_title))
                if self._title_overlap(removable, source_title) >= 4:
                    base = [sentence for sentence in existing if sentence != removable]
                    for score, idx, candidate in sorted(ranked, key=lambda item: (-item[0], item[1])):
                        if candidate in base:
                            continue
                        if _near_duplicate_in_base(candidate, base):
                            continue
                        if self._title_overlap(candidate, source_title) > 2:
                            continue
                        proposal_sentences = sorted(
                            base + [candidate],
                            key=lambda sentence: source_positions.get(sentence, 999),
                        )
                        proposal = " ".join(proposal_sentences)
                        if len(proposal) > max_chars:
                            continue
                        out = proposal
                        existing = proposal_sentences
                        break

        return self._normalize_body_punctuation(out)

    def _rebalance_for_consequence_coverage(
        self,
        body: str,
        source_title: str,
        source_body: str,
        min_chars: int,
        max_chars: int,
    ) -> str:
        sentences = self._split_sentences(self._normalize_body_punctuation(body))
        if len(sentences) < 3:
            return body

        removable = max(sentences, key=lambda sentence: self._title_overlap(sentence, source_title))
        if self._title_overlap(removable, source_title) < 3:
            return body

        base = [sentence for sentence in sentences if sentence != removable]
        consequence_candidates: List[tuple[int, int, str]] = []
        for idx, sentence in enumerate(self._split_sentences(source_body)):
            normalized = self._normalize_body_punctuation(sentence)
            low = normalized.lower()
            if len(normalized) < 35:
                continue
            if not any(token in low for token in self._CONSEQUENCE_TOKENS):
                continue
            if self._title_overlap(normalized, source_title) > 2:
                continue
            if any(SequenceMatcher(None, normalized.lower(), existing.lower()).ratio() >= 0.9 for existing in base):
                continue
            score = self._sentence_quality_score(normalized, title=source_title, position=idx) + 4
            consequence_candidates.append((score, idx, normalized))

        if not consequence_candidates:
            return body

        source_positions = {
            self._normalize_body_punctuation(sentence): idx
            for idx, sentence in enumerate(self._split_sentences(source_body))
        }
        for _, _, candidate in sorted(consequence_candidates, key=lambda item: (-item[0], item[1])):
            proposal_sentences = sorted(
                base + [candidate],
                key=lambda sentence: source_positions.get(sentence, 999),
            )
            proposal = " ".join(proposal_sentences)
            if len(proposal) > max_chars:
                continue
            if len(proposal) < max(220, min_chars - 60):
                continue
            return self._normalize_body_punctuation(proposal)
        return body

    def _sentence_quality_score(self, sentence: str, title: str = "", position: int = 0) -> int:
        normalized = self._normalize_body_punctuation(sentence)
        signature = self._sentence_signature(normalized)
        if not signature:
            return -999

        low = normalized.lower()
        score = len(signature)
        if re.search(r"\b\d+\b", normalized):
            score += 4
        if any(token in low for token in self._ONGOING_TOKENS):
            score += 3
        if any(token in low for token in self._ACTION_VERBS):
            score += 2
        if any(token in low for token in self._CONSEQUENCE_TOKENS):
            score += 2
        if position <= 1:
            score += 2
        if len(normalized) > 190:
            score -= 2
        if any(low.startswith(prefix) for prefix in self._GENERIC_SENTENCE_STARTS):
            score -= 4
        if re.match(
            r"^(?:this (?:development|move|scrutiny|situation)|the move)\s+(?:could|may|might)\s+(?:impact|affect|signal)\b",
            low,
        ):
            score -= 6
        if self._is_weak_ending_sentence(normalized, title=title):
            score -= 8

        overlap = self._title_overlap(normalized, title)
        if overlap >= max(4, min(len(signature), 6)):
            score -= 8
        if overlap >= max(6, min(len(signature), 8)):
            score -= 4
        return score

    def _is_duplicate_sentence(self, candidate: str, existing: List[str]) -> bool:
        candidate_norm = self._normalize_body_punctuation(candidate)
        candidate_sig = self._sentence_signature(candidate_norm)
        if not candidate_sig:
            return True
        for sentence in existing:
            existing_norm = self._normalize_body_punctuation(sentence)
            if candidate_norm.lower() == existing_norm.lower():
                return True

            shorter, longer = sorted((candidate_norm.lower(), existing_norm.lower()), key=len)
            if len(shorter) >= 40 and shorter in longer:
                return True

            if SequenceMatcher(None, candidate_norm.lower(), existing_norm.lower()).ratio() >= 0.86:
                return True

            overlap = len(candidate_sig & self._sentence_signature(existing_norm))
            if overlap >= max(4, min(len(candidate_sig), 6)):
                return True
        return False

    def _source_sentence_candidates(self, title: str, body: str, existing: List[str]) -> List[str]:
        scored = []
        seen = list(existing)
        for idx, sentence in enumerate(self._split_sentences(body)):
            normalized = self._normalize_body_punctuation(sentence)
            if len(normalized) < 45:
                continue
            if self._is_duplicate_sentence(normalized, seen):
                continue
            score = self._sentence_quality_score(normalized, title=title, position=idx)
            if any(word[:1].isupper() for word in normalized.split()[1:4]):
                score += 1
            scored.append((score, normalized))
            seen.append(normalized)

        title_sentence = self._normalize_body_punctuation(title)
        if not existing and len(scored) < 2 and len(title_sentence) >= 35 and not self._is_duplicate_sentence(title_sentence, seen):
            scored.append((5, title_sentence))

        scored.sort(key=lambda item: -item[0])
        return [item[1] for item in scored]

    def _pick_body_sentences(self, source_title: str, source_body: str, target_chars: int, max_chars: int) -> List[str]:
        sentences = [
            self._normalize_body_punctuation(sentence)
            for sentence in self._split_sentences(source_body)
        ]
        sentences = [sentence for sentence in sentences if len(sentence) >= 35]
        if not sentences:
            return []

        chosen: List[str] = []
        sentence_positions = {sentence: idx for idx, sentence in enumerate(sentences)}

        def _select_best(pool: List[tuple[int, int, str]]) -> Optional[str]:
            for _, _, candidate in sorted(pool, key=lambda item: (-item[0], item[1])):
                if self._is_duplicate_sentence(candidate, chosen):
                    continue
                proposal = f"{' '.join(chosen)} {candidate}".strip() if chosen else candidate
                if len(proposal) > max_chars:
                    continue
                chosen.append(candidate)
                return candidate
            return None

        lead_pool = []
        context_pool = []
        consequence_pool = []
        for idx, sentence in enumerate(sentences):
            base_score = self._sentence_quality_score(sentence, title=source_title, position=idx)
            low = sentence.lower()
            lead_pool.append((base_score + (3 if any(token in low for token in self._ACTION_VERBS) else 0), idx, sentence))
            context_pool.append((base_score + (3 if re.search(r"\b\d+\b", sentence) else 0), idx, sentence))
            consequence_pool.append((base_score + (4 if any(token in low for token in self._CONSEQUENCE_TOKENS) else 0), idx, sentence))

        _select_best(lead_pool)
        _select_best(context_pool)
        _select_best(consequence_pool)

        if len(chosen) >= 3 and self._title_overlap(chosen[0], source_title) >= 4:
            base = chosen[1:]
            replacement_options: List[tuple[int, int, List[str]]] = []
            for idx, sentence in enumerate(sentences):
                if sentence == chosen[0]:
                    continue
                if self._is_duplicate_sentence(sentence, base):
                    continue
                proposal_sentences = sorted(base + [sentence], key=lambda item: sentence_positions.get(item, 999))
                proposal = " ".join(proposal_sentences)
                if len(proposal) > max_chars:
                    continue
                score = self._sentence_quality_score(sentence, title=source_title, position=idx)
                score -= self._title_overlap(sentence, source_title) * 2
                if any(token in sentence.lower() for token in self._CONSEQUENCE_TOKENS):
                    score += 3
                replacement_options.append((score, idx, proposal_sentences))

            if replacement_options:
                chosen = sorted(replacement_options, key=lambda item: (-item[0], item[1]))[0][2]

        for candidate in self._source_sentence_candidates(source_title, source_body, chosen):
            if self._is_duplicate_sentence(candidate, chosen):
                continue
            proposal = f"{' '.join(chosen)} {candidate}".strip() if chosen else candidate
            if len(proposal) > max_chars:
                continue
            chosen.append(candidate)
            if len(proposal) >= target_chars - 10 or len(chosen) >= 4:
                break

        body = " ".join(chosen)
        if body and len(body) > target_chars:
            body = self._trim_body_to_band(body, max_chars=max_chars, target_chars=target_chars)
            body = self._trim_weak_ending_sentence(body, source_title, source_body, min_chars=220, max_chars=max_chars)
            return self._split_sentences(body)
        return chosen

    def _trim_body_to_band(self, body: str, max_chars: int, target_chars: int) -> str:
        sentences = self._split_sentences(body)
        if not sentences:
            return body[:max_chars].rstrip(" ,.-")

        built = []
        current = ""
        for sentence in sentences:
            proposal = f"{current} {sentence}".strip() if current else sentence
            if len(proposal) <= max_chars:
                built.append(sentence)
                current = proposal
            else:
                break

        # Always prefer complete sentences over a mid-sentence cut. A slightly
        # short but complete paragraph beats a truncated fragment ("...while US.").
        if built:
            return " ".join(built)

        # No whole sentence fits (one very long sentence) — cut at the last
        # sentence terminator if any, otherwise at a word boundary.
        clipped = body[:max_chars]
        stop = max(clipped.rfind("."), clipped.rfind("!"), clipped.rfind("?"))
        if stop > 40:
            return clipped[: stop + 1].rstrip()
        cut = clipped.rfind(" ")
        return (clipped[:cut] if cut > 0 else clipped).rstrip(" ,.-")

    def _expand_body(self, text: str, source_title: str, source_body: str, target_chars: int = 330, max_chars: int = 350) -> str:
        out = self._normalize_body_punctuation(text)
        existing = self._split_sentences(out)
        candidates = self._source_sentence_candidates(source_title, source_body, existing)

        for candidate in candidates:
            proposal = f"{out} {candidate}".strip()
            if len(proposal) <= max_chars:
                out = proposal
                existing.append(candidate)
            if len(out) >= target_chars - 5:
                break

        if len(out) < target_chars - 5:
            clause_seed = out
            clause_options = []
            for source in self._split_sentences(source_body):
                if self._is_duplicate_sentence(source, existing):
                    continue
                if self._title_overlap(source, source_title) >= 4:
                    continue
                for clause in re.split(r",|;", source):
                    clause = clause.strip(" ,.-")
                    if len(clause) >= 20:
                        clause_options.append(clause)

            for clause in clause_options:
                candidate_sentence = f"{clause.rstrip(' .!?')}."
                if self._looks_broken_sentence(candidate_sentence):
                    continue
                if clause in out:
                    continue
                if self._is_duplicate_sentence(clause, self._split_sentences(out)):
                    continue
                proposal = f"{out} {clause}.".strip()
                if len(proposal) <= max_chars:
                    out = proposal
                if len(out) >= target_chars - 5:
                    break

        if len(out) < target_chars - 5 and out == clause_seed:
            fragment_sources = list(reversed(candidates))
            for source in fragment_sources:
                if self._title_overlap(source, source_title) >= 4:
                    continue
                tokens = source.rstrip('.!?').split()
                start = max(6, min(10, len(tokens) // 2))
                for size in range(start, len(tokens) + 1):
                    fragment = " ".join(tokens[:size]).rstrip(" ,.-")
                    if not fragment:
                        continue
                    if self._looks_broken_sentence(f"{fragment}."):
                        continue
                    if fragment in out:
                        continue
                    if self._is_duplicate_sentence(fragment, self._split_sentences(out)):
                        continue
                    
                    # 60% word overlap check to prevent hallucination snippets
                    fragment_words = set(fragment.lower().split())
                    out_words = set(out.lower().split())
                    if fragment_words and out_words:
                        if len(fragment_words & out_words) / len(fragment_words) >= 0.6:
                            continue

                    proposal = f"{out} {fragment}.".strip()
                    if len(proposal) <= max_chars:
                        out = proposal
                    if len(out) >= target_chars - 5:
                        break
                if len(out) >= target_chars - 5:
                    break

        if len(out) < target_chars - 5:
            tail_options = []
            for sentence in self._split_sentences(source_body):
                if self._title_overlap(sentence, source_title) >= 4:
                    continue
                for clause in re.split(r",|;", sentence):
                    clause = clause.strip(" ,.-")
                    if len(clause) >= 8:
                        tail_options.append(clause)

            for tail in tail_options:
                if self._looks_broken_sentence(f"{tail.rstrip(' .!?')}."):
                    continue
                proposal = f"{out} {tail}.".strip()
                if len(proposal) <= max_chars:
                    out = proposal
                if len(out) >= target_chars - 5:
                    break

        return self._normalize_body_punctuation(out)

    def _expand_body_via_model(self, text: str, source_title: str, source_body: str, min_chars: int, max_chars: int) -> str:
        """Expand a too-short body using the model (own words), never source paste."""
        if not self.client or not getattr(self.client, "available", False):
            return text
        prompt = (
            f"Expand this news paragraph to between {min_chars} and {max_chars} characters by adding "
            f"one or two more concrete facts taken FROM THE SOURCE, written in your own words. "
            f"Keep it as one cohesive, professional paragraph. Do NOT copy sentences from the source, "
            f"do NOT repeat a fact already stated, and end on a complete sentence. "
            f"Return only the paragraph text.\n\n"
            f"SOURCE: {source_body[:2500]}\n\nPARAGRAPH: {text}"
        )
        try:
            out = self.client.generate_text(
                prompt,
                system_instruction="You are a precise, professional news copy editor.",
                max_output_tokens=600,
            )
            out = " ".join((out or "").split()).strip().strip('"').strip("'").strip()
            if out and len(out) >= len(text) and not self._body_too_close_to_source(out, source_body):
                return out
        except Exception:
            pass
        return text

    def _expand_body_smart(self, text: str, source_title: str, source_body: str, target_chars: int, min_chars: int, max_chars: int) -> str:
        if self.client and getattr(self.client, "available", False):
            expanded = self._expand_body_via_model(text, source_title, source_body, min_chars=min_chars, max_chars=max_chars)
            if expanded and len(expanded) > len(text):
                return expanded
        # No model (or it didn't help) — fall back to the conservative source-based pad.
        return self._expand_body(text, source_title, source_body, target_chars=target_chars, max_chars=max_chars)

    def _fit_body_length(self, body: str, source_title: str, source_body: str, target_chars: int, min_chars: int, max_chars: int) -> str:
        out = self._normalize_body_punctuation(body)
        if len(out) < min_chars:
            out = self._expand_body_smart(out, source_title, source_body, target_chars=target_chars, min_chars=min_chars, max_chars=max_chars)
        if len(out) > max_chars:
            out = self._trim_body_to_band(out, max_chars=max_chars, target_chars=target_chars)
        out = self._normalize_body_punctuation(out)
        out = self._trim_weak_ending_sentence(out, source_title, source_body, min_chars=min_chars, max_chars=max_chars)
        out = self._ensure_complete_body(out, source_title, source_body, min_chars=min_chars, max_chars=max_chars)
        if len(out) < min_chars:
            out = self._expand_body_smart(out, source_title, source_body, target_chars=max(target_chars, min_chars), min_chars=min_chars, max_chars=max_chars)
            if len(out) > max_chars:
                out = self._trim_body_to_band(out, max_chars=max_chars, target_chars=target_chars)
            out = self._normalize_body_punctuation(out)
            out = self._trim_weak_ending_sentence(out, source_title, source_body, min_chars=min_chars, max_chars=max_chars)
            out = self._ensure_complete_body(out, source_title, source_body, min_chars=min_chars, max_chars=max_chars)
        return out
    def _boost_title_punch(self, title: str, source_title: str, source_body: str = "", max_title: int = 68) -> str:
        clean = self._remove_title_commas(
            self._normalize_title_punctuation(self._clean_title_copy(self._clean_title_noise(title), source_title, source_body))
        )
        if not clean:
            return clean

        out = clean
        replacements = {
            r"\bwhat it means\b": "stakes rise",
            r"\bhere'?s what\b": "pressure builds as",
            r"\bwhy it matters\b": "stakes grow",
            r"\bamid concerns\b": "as pressure mounts",
            r"\bafter concerns\b": "after shockwaves spread",
        }
        for pattern, repl in replacements.items():
            out = re.sub(pattern, repl, out, flags=re.IGNORECASE)

        out = re.sub(r"\s+", " ", out).strip(" ,.-")
        out = self._remove_title_commas(out)
        out = self._restore_designations(
            self._restore_proper_nouns(self._to_sentence_case_headline(out), source_title, source_body),
            source_title,
            source_body,
        )
        out = self._enforce_cautious_title(out, source_title, source_body, max_title)
        return self._smart_truncate_title(out, max_title)

    def _title_from_source(self, source_title: str, max_title: int, source_body: str = "") -> str:
        title = self._clean_title_noise(source_title)
        title = self._normalize_acronyms(title)
        title = self._normalize_title_punctuation(title)
        title = self._remove_title_commas(title)
        title = self._clean_title_copy(title, source_title, source_body)
        title = self._restore_designations(
            self._restore_proper_nouns(self._to_sentence_case_headline(title), source_title, source_body),
            source_title,
            source_body,
        )
        title = self._enforce_cautious_title(title, source_title, source_body, max_title)
        if len(title) > max_title:
            title = self._smart_truncate_title(title, max_title)
        return title

    def _fallback_body(self, source_title: str, source_body: str, target_chars: int, min_chars: int, max_chars: int) -> str:
        chosen = self._pick_body_sentences(source_title, source_body, target_chars=target_chars, max_chars=max_chars)
        if not chosen:
            raw = self._clean_body_copy(source_body[:max_chars], source_title, source_body)
            return self._ensure_complete_body(raw, source_title, source_body, min_chars=min_chars, max_chars=max_chars)

        body = " ".join(chosen)
        body = self._clean_body_copy(body, source_title, source_body)
        body = self._trim_body_to_band(body, max_chars=max_chars, target_chars=target_chars)
        body = self._ensure_complete_body(body, source_title, source_body, min_chars=min_chars, max_chars=max_chars)
        if len(body) < min_chars:
            body = self._expand_body(body, source_title, source_body, target_chars=target_chars, max_chars=max_chars)
            body = self._clean_body_copy(body, source_title, source_body)
            body = self._ensure_complete_body(body, source_title, source_body, min_chars=min_chars, max_chars=max_chars)
        if len(body) < min_chars:
            body = self._expand_body(source_body, source_title, source_body, target_chars=target_chars, max_chars=max_chars)
            body = self._clean_body_copy(body, source_title, source_body)
            body = self._trim_body_to_band(body, max_chars=max_chars, target_chars=target_chars)
            body = self._ensure_complete_body(body, source_title, source_body, min_chars=min_chars, max_chars=max_chars)
        body = self._strengthen_body_coverage(
            body,
            source_title,
            source_body,
            min_chars=min_chars,
            target_chars=target_chars,
            max_chars=max_chars,
        )
        body = self._rebalance_for_consequence_coverage(
            body,
            source_title,
            source_body,
            min_chars=min_chars,
            max_chars=max_chars,
        )
        if len(body) < min_chars:
            tail = self._source_context_tail(source_title, source_body)
            proposal = self._normalize_body_punctuation(f"{body} {tail}".strip())
            if len(proposal) <= max_chars:
                body = proposal
        body = self._clean_body_copy(body, source_title, source_body)
        body = self._ensure_complete_body(body, source_title, source_body, min_chars=min_chars, max_chars=max_chars)
        return body

    def _fallback_summary(
        self,
        source_title: str,
        source_body: str,
        min_title: int,
        max_title: int,
        target_body: int,
        min_body: int,
        max_body: int,
    ) -> Optional[Dict[str, str]]:
        title = self._title_from_source(source_title, max_title=max_title, source_body=source_body)
        if self._title_too_close_to_source(title, source_title):
            title = self._retitle_from_source(source_title, source_body, max_title=max_title)
        if len(title) < min_title:
            title = self._boost_title_punch(title or source_title, source_title, source_body, max_title=max_title)
        body = self._fallback_body(source_title, source_body, target_chars=target_body, min_chars=min_body, max_chars=max_body)
        title = self._enforce_cautious_title(
            self._restore_designations(title, source_title, source_body),
            source_title,
            source_body,
            max_title=max_title,
        )
        body = self._clean_body_copy(body, source_title, source_body)
        body = self._fit_body_length(
            body,
            source_title,
            source_body,
            target_chars=target_body,
            min_chars=min_body,
            max_chars=max_body,
        )
        body = self._clean_body_copy(body, source_title, source_body)
        if not self._passes_credibility_checks(title, body, source_title, source_body):
            title = self._retitle_from_source(source_title, source_body, max_title=max_title)
            body = self._fallback_body(source_title, source_body, target_chars=target_body, min_chars=min_body, max_chars=max_body)
            body = self._clean_body_copy(body, source_title, source_body)
        if len(title) < min_title or len(body) < min_body:
            return None
        if not self._passes_credibility_checks(title, body, source_title, source_body):
            return None
        return {"title": title, "body": body}

    def summarize(self, title: str, body: str, max_retries: int = 2) -> Optional[Dict[str, str]]:
        min_title = 16
        max_title = 80
        target_body = 320
        # Lowered floor so a clean model rewrite is never padded with verbatim
        # source text or invented filler just to hit a count (validator allows >= 50).
        min_body = 230
        max_body = 360

        article_title = " ".join((title or "").split())
        article_body_raw = " ".join((body or "").split())
        # ── Strip common Indian news site boilerplate before summarization ──────────
        # These patterns appear at the end of scraped bodies and pollute the AI output
        _BOILERPLATE_PATTERNS = [
            r"\s*-\s*Ends\b.*$",                            # "- Ends Published By: ..."
            r"\bPublished\s+By\s*:\s*.{0,60}$",             # "Published By: Armaan Agarwal"
            r"\bPublished\s+On\s*:\s*.{0,40}$",             # "Published On: Apr 21, 2026 11:27 IST"
            r"\bAlso\s+Read\s*[|:].+$",                     # "Also Read | Who is new Apple CEO..."
            r"\bStory\s+continues\s+below\s+this\s+ad\b.*$",
            r"\bAdvertisement\b.*$",
            r"\bRead\s+more\s+at\s+TOI\b.*$",
            r"\bFORTHCOMING\s+STORIES\b.*$",
            r"\bSubscribe\s+to\s+India\s+Today\b.*$",
            r"\bGet\s+latest\s+news\s+on\b.*$",
            r"(?:Watch|Follow)\s+(?:live|us\s+on)\b.*$",
        ]
        import re as _re
        _cleaned = article_body_raw
        for _pat in _BOILERPLATE_PATTERNS:
            _cleaned = _re.sub(_pat, "", _cleaned, flags=_re.IGNORECASE | _re.DOTALL).strip()

        # Strip scraped JavaScript / ad-embed junk (e.g. vdo.ai player scripts
        # that some sources inline into the article body).
        _cleaned = _re.sub(r"<script[^>]*>.*?</script>", " ", _cleaned, flags=_re.IGNORECASE | _re.DOTALL)
        _cleaned = _re.sub(r"<[^>]+>", " ", _cleaned)  # any stray HTML tags
        # IIFE ad blocks like (function(v,d,o,ai){...})(window, document, "//a.vdo.ai/...js");
        _cleaned = _re.sub(r"\(function\s*\([^)]*\)\s*\{.*?\}\s*\)\s*\([^;]*\)\s*;?", " ", _cleaned, flags=_re.DOTALL)
        # Residual JS fragments / script srcs
        _cleaned = _re.sub(r"\b\w+\.(?:createElement|appendChild|getElementById|setAttribute)\b[^.;]*[.;]?", " ", _cleaned)
        _cleaned = _re.sub(r"(?:window|document|d\.head|v\.location)\.\w+[^.;]*[.;]?", " ", _cleaned)
        _cleaned = _re.sub(r"[\"']?//[\w.\-]+\.(?:ai|com|net|io)/[\w./\-]*\.js[\"']?", " ", _cleaned)
        _cleaned = _re.sub(r"\s+", " ", _cleaned).strip()
        article_body = _cleaned[:5200]

        if not article_title or not article_body:
            return None

        # If cleaning left almost no real prose (the page was mostly ad/script or
        # the scraper failed to extract the article), skip rather than publish junk.
        if len(article_body) < 150 or len(re.findall(r"[A-Za-z]{3,}", article_body)) < 20:
            self.logger.warning(
                f"Body too thin after cleaning ({len(article_body)} chars) — skipping article"
            )
            return None
        if not self.client or not self.client.available:
            return self._fallback_summary(
                article_title,
                article_body,
                min_title=min_title,
                max_title=max_title,
                target_body=target_body,
                min_body=min_body,
                max_body=max_body,
            )

        style_examples = self._build_dynamic_style_examples(article_title, article_body, limit=2)
        credibility_note = self._credibility_prompt_note(article_title, article_body)

        system_msg = (
            "You are the chief copy editor of a fast, premium mobile news app. You take one raw "
            "source article and rewrite it into a crisp, professional news card: one sharp headline "
            "and one tight, well-framed paragraph. "
            "You write in your OWN words — you never paste or lightly trim the source. "
            "Voice: confident, neutral, factual, modern. Clean nouns, strong verbs, specific numbers, "
            "no fluff, no marketing, no opinion. Use only facts present in the source; never invent or "
            "speculate. The headline and the paragraph must tell the SAME story — the headline names "
            "the single most important fact, and the paragraph opens on that exact fact."
        )

        house_style = self._house_style_block()

        prompt = f"""Rewrite the source below into one professional news card.

SOURCE HEADLINE: {article_title}
SOURCE TEXT: {article_body}

{house_style}
{style_examples}
{credibility_note}

OUTPUT FORMAT
Return JSON only: {{"title":"...","body":"..."}}

TITLE — 16 to 80 characters, sentence case
- Name the single most newsworthy fact: subject + strong active verb + the key stake/number/place. Be specific — name the result, opponent, figure, or place. Never a vague summary like "delivers a dominant performance" or "makes a big move"; say what actually happened ("India crush Afghanistan by an innings and 300 runs").
- Strong verbs: raises, halts, files, signs, approves, rejects, warns, slashes, blocks, cuts, sues, opens, names, drops, denies, accuses, clears, arrests, strikes, rescues, exits.
- It MUST match the body: the title's main fact is the same fact the body's first sentence states. Never headline a detail the body buries or omits.
- Reword the source headline completely — do not echo or lightly trim it.
- Sentence case (only proper nouns and standard acronyms capitalised). No clickbait, questions, rhetorical hooks, or exclamations.
- NO COMMAS in the title — rephrase to avoid lists (write "ED raids six sites in three states", not "ED raids Punjab, UP, Delhi"). A single clean colon separator is fine (e.g. "Navy alert: 24 sailors rescued"); no dramatic colons or em-dashes.
- Never cut a word to fit; if too long, rewrite tighter.

BODY — one cohesive paragraph, 2 to 4 complete sentences, aim for {min_body}-{max_body} characters
- REWRITE in your own words. Do NOT copy sentences from the source. Never reuse 5 or more consecutive words from the source text (proper names, figures, and fixed designations are the only exceptions). If a sentence reads like the source, rebuild it from scratch.
- USE ONLY SOURCE FACTS. Never invent a fact, a quote, a reaction, or a generic closing line to reach the length. Do NOT add commentary the source did not state ("market participants said...", "officials said the event aimed to...", "the outlook remains fragile"). If the source only supports two strong sentences, write exactly two — a tight short card is far better than a padded one.
- Keep Indian-style figures as the source gives them: write "22 lakh" and "5 lakh", not "2.2 million" / "500,000"; keep "crore" as crore.
- STRONG OPENING: the first sentence is the lede — open on the single most consequential fact (the biggest number, the official action/decision), even if the source lists it last. Never lead with a minor or personal detail (e.g. "brings his own lunch") when a bigger action exists (e.g. "ordered 717 outlets shut"). Name the subject — never start the body with a bare pronoun ("He", "She", "They"). No throat-clearing or wind-up; the first six words should carry the real news.
- The first sentence must deliver the SAME fact as the title, expanded with one or two concrete specifics.
- Each later sentence adds NEW connected information — scale, mechanism, named context, then consequence, next step, or named reaction. Carry the thread forward; never restate the lede.
- Prefer two or three clear sentences over one long run-on. Do not cram every fact into a single sentence — break it so each sentence is complete and easy to read.
- It must read as one smooth, professional paragraph a newsreader would say aloud — connected, not a list of stacked facts, and not choppy.
- The closing sentence must be complete and land a real fact or consequence. Never end on a fragment ("and pledged.", "officials said.", "risks from global.").
- Keep exact figures, dates, technical designations, and named systems. Expand an acronym once on first use if the source uses it (e.g. Enforcement Directorate (ED)).
- Use specific names over generic labels ("Modi", not "the prime minister", when the source names him).
- Preserve any source qualifier ("reportedly", "alleged", "appears to") — never upgrade a claim to confirmed, and never add verification the source did not state.

NEVER WRITE
- Verbatim or near-verbatim source sentences.
- Source/publisher names ("according to Reuters", "Times of India reported").
- Filler ("this development", "this comes amid", "in a major move", "meanwhile", "notably", "it is worth noting").
- Generic closers ("officials said", "the situation is being watched", "more details awaited").
- Opinion words ("shocking", "stunning", "unprecedented") unless the source itself uses them.

QUALITY BAR — before you answer, self-check:
1. Does the title state the SAME core fact the first body sentence states? If not, fix the title.
2. Is any sentence copied or barely changed from the source? If yes, rewrite it from scratch.
3. Does the first sentence hit real news in its first few words, with no wind-up?
4. Does the paragraph flow as one connected account and end on a complete fact?
Only return the JSON once all four pass.
"""

        last_content = ""
        retry_feedback = ""
        for attempt in range(max_retries):
            try:
                messages = [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ]

                if attempt > 0 and last_content:
                    messages.append({"role": "assistant", "content": last_content})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Rewrite to be cleaner and more concrete. Make the body one cohesive, flowing paragraph "
                                "where each sentence connects to the last — not stacked, disconnected facts. "
                                "The final sentence must be complete and land a real fact; never end on a fragment like 'and pledged.' "
                                "Use only source facts, keep the title direct. "
                                "Title 16-80 chars, body 299-360 chars, no filler, no publisher names, no opinions; JSON only. "
                                f"{retry_feedback}".strip()
                            ),
                        }
                    )

                combined_prompt = "\n\n".join(
                    f"{message['role'].upper()}:\n{message['content']}" for message in messages
                )
                last_content = self.client.generate_json(
                    combined_prompt,
                    system_instruction=system_msg,
                    temperature=0.15,
                    max_output_tokens=420,
                    schema=self._TITLE_BODY_JSON_SCHEMA,
                )
                raw = last_content
                if "```" in raw:
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]

                parsed = json.loads(raw)
                if "title" not in parsed or "body" not in parsed:
                    self.logger.error("Missing title/body in summary response")
                    break

                title_out = self._restore_proper_nouns(
                    self._to_sentence_case_headline(
                        self._remove_title_commas(
                            self._normalize_title_punctuation(
                                self._clean_title_copy(
                                    self._clean_title_noise(" ".join(str(parsed["title"]).split())),
                                    article_title,
                                    article_body,
                                )
                            )
                        )
                    ),
                    article_title,
                    article_body,
                )
                title_out = self._restore_designations(title_out, article_title, article_body)
                title_out = self._enforce_cautious_title(title_out, article_title, article_body, max_title)
                body_out = self._fit_body_length(
                    " ".join(str(parsed["body"]).split()),
                    article_title,
                    article_body,
                    target_chars=target_body,
                    min_chars=min_body,
                    max_chars=max_body,
                )
                body_out = self._clean_body_copy(body_out, article_title, article_body)
                body_out = self._fit_body_length(
                    body_out,
                    article_title,
                    article_body,
                    target_chars=target_body,
                    min_chars=min_body,
                    max_chars=max_body,
                )
                body_out = self._clean_body_copy(body_out, article_title, article_body)

                if len(title_out) > max_title:
                    title_out = self._smart_truncate_title(title_out, max_title)
                if len(title_out) < min_title:
                    retry_feedback = (
                        "Title is too short. Expand it to 16-80 characters while staying factual and direct."
                    )
                    if attempt < (max_retries - 1):
                        continue
                    title_out = self._retitle_from_source(article_title, article_body, max_title=max_title)
                if "," in title_out:
                    self.logger.warning("Title contains comma, retrying...")
                    if attempt < (max_retries - 1):
                        retry_feedback = "Remove commas from the title and rewrite it in a tighter headline style."
                        continue
                    title_out = self._remove_title_commas(title_out)
                if self._title_too_close_to_source(title_out, article_title):
                    self.logger.warning("Title too close to source wording, retrying...")
                    if attempt < (max_retries - 1):
                        retry_feedback = (
                            "Headline is too close to the source wording. Rebuild it with different phrasing while keeping it factual and restrained."
                        )
                        continue
                    title_out = self._retitle_from_source(article_title, article_body, max_title=max_title)

                if self._is_vague_title(title_out):
                    self.logger.warning(f"Title too vague: {title_out!r}, retrying...")
                    if attempt < (max_retries - 1):
                        retry_feedback = (
                            "The headline is too vague. Name the specific result, subject, number, or place — "
                            "say exactly what happened (e.g. 'India crush Afghanistan by an innings and 300 runs'), "
                            "not a generic phrase like 'delivers a dominant performance'."
                        )
                        continue
                    rebuilt = self._title_from_body_lede(body_out, max_title, source_title=article_title)
                    if rebuilt and not self._is_vague_title(rebuilt):
                        title_out = rebuilt

                if self._is_bad_title(title_out, body_out):
                    self.logger.warning(f"Title broken/off-topic: {title_out!r}, retrying...")
                    if attempt < (max_retries - 1):
                        retry_feedback = (
                            "The headline is wrong: it must state the MAIN news in the first sentence of the body, "
                            "naming the subject (not a pronoun) and the key action. Do not start with 'And/But/He/She/"
                            "It/They' and do not headline a minor side detail. Rebuild it to match the lede."
                        )
                        continue
                    rebuilt = self._title_from_body_lede(body_out, max_title, source_title=article_title)
                    if self._title_well_formed(rebuilt):
                        title_out = rebuilt

                body_is_complete = body_out.endswith((".", "!", "?")) and not self._has_dangling_tail(body_out)
                if len(body_out) < min_body and body_is_complete:
                    expanded_body = self._expand_body(
                        body_out,
                        article_title,
                        article_body,
                        target_chars=target_body,
                        max_chars=max_body,
                    )
                    expanded_body = self._fit_body_length(
                        expanded_body,
                        article_title,
                        article_body,
                        target_chars=target_body,
                        min_chars=min_body,
                        max_chars=max_body,
                    )
                    if len(expanded_body) > len(body_out):
                        body_out = expanded_body
                        body_is_complete = body_out.endswith((".", "!", "?")) and not self._has_dangling_tail(body_out)

                if len(body_out) > max_body or len(body_out) < min_body:
                    self.logger.warning(
                        f"Summary length out of range (title={len(title_out)}, body={len(body_out)}), retrying..."
                    )
                    if attempt < (max_retries - 1):
                        retry_feedback = (
                            "Keep the body in the 280-380 character band and keep the title in the 16-80 character band."
                        )
                        continue
                    break

                if self._looks_template_body(body_out):
                    self.logger.warning("Summary body sounds template-like, retrying...")
                    if attempt < (max_retries - 1):
                        retry_feedback = "Keep the copy concrete and sentence-led. Remove template-like filler."
                        continue

                if self._has_source_boilerplate(body_out):
                    self.logger.warning("Summary body contains source boilerplate, retrying...")
                    if attempt < (max_retries - 1):
                        retry_feedback = "Remove publisher/source boilerplate and keep only article facts."
                        continue
                    break

                if self._body_too_close_to_source(body_out, article_body):
                    self.logger.warning("Summary body too close to source copy, retrying...")
                    if attempt < (max_retries - 1):
                        retry_feedback = "Rephrase the body more aggressively. Do not echo source sentences."
                        continue
                    body_out = self._fallback_body(
                        article_title,
                        article_body,
                        target_chars=target_body,
                        min_chars=min_body,
                        max_chars=max_body,
                    )
                    body_out = self._clean_body_copy(body_out, article_title, article_body)
                    body_out = self._fit_body_length(
                        body_out,
                        article_title,
                        article_body,
                        target_chars=target_body,
                        min_chars=min_body,
                        max_chars=max_body,
                    )
                    body_out = self._clean_body_copy(body_out, article_title, article_body)

                title_out = self._restore_designations(title_out, article_title, article_body)
                title_out = self._enforce_cautious_title(title_out, article_title, article_body, max_title)
                body_out = self._clean_body_copy(body_out, article_title, article_body)
                body_out = self._fit_body_length(
                    body_out,
                    article_title,
                    article_body,
                    target_chars=target_body,
                    min_chars=min_body,
                    max_chars=max_body,
                )
                body_out = self._clean_body_copy(body_out, article_title, article_body)

                if not self._passes_credibility_checks(title_out, body_out, article_title, article_body):
                    self.logger.warning("Summary failed credibility checks, retrying...")
                    if attempt < (max_retries - 1):
                        retry_feedback = (
                            "Preserve tentative or unverified framing and exact technical designations from the source. "
                            "Do not turn image-based or social-media claims into confirmed fact."
                        )
                        continue
                    fallback = self._fallback_summary(
                        article_title,
                        article_body,
                        min_title=min_title,
                        max_title=max_title,
                        target_body=target_body,
                        min_body=min_body,
                        max_body=max_body,
                    )
                    if fallback and self._passes_credibility_checks(
                        fallback["title"],
                        fallback["body"],
                        article_title,
                        article_body,
                    ):
                        self.logger.warning("Using credibility-safe deterministic fallback summary")
                        return fallback
                    break

                if not self._has_body_hook(body_out):
                    self.logger.warning("Summary body lacks a clear factual lead, keeping best fitted version")

                # FINAL coherence pass: body may have been re-picked after the
                # earlier title check (e.g. via _fallback_body), so the title can
                # now describe a different fact than the body's lede. Rebuild the
                # title from the final lede if it no longer matches.
                if self._is_bad_title(title_out, body_out):
                    rebuilt = self._title_from_body_lede(body_out, max_title, source_title=article_title)
                    if self._title_well_formed(rebuilt):
                        self.logger.info(f"Title realigned to lede: {title_out!r} -> {rebuilt!r}")
                        title_out = self._restore_designations(rebuilt, article_title, article_body)

                self.logger.info(f"Summary ready: title={len(title_out)} chars, body={len(body_out)} chars")
                return {"title": title_out, "body": body_out}
            except Exception as exc:
                self.logger.error(f"Summarization failed: {exc}")
                break

        fallback = self._fallback_summary(
            article_title,
            article_body,
            min_title=min_title,
            max_title=max_title,
            target_body=target_body,
            min_body=min_body,
            max_body=max_body,
        )
        if fallback:
            self.logger.warning(
                f"Using deterministic summary fallback: title={len(fallback['title'])} body={len(fallback['body'])}"
            )
            return fallback
        return None
