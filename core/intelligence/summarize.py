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
        out = " ".join((title or "").split())
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
        out = re.sub(r"\s*,\s*", ": ", out)
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

        proper_map: Dict[str, str] = {}
        for token in re.findall(r"\b[A-Za-z][A-Za-z'\u2019-]*\b", src):
            if len(token) < 3:
                continue
            if token.isupper():
                continue
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

    def _normalize_body_punctuation(self, body: str) -> str:
        text = " ".join((body or "").split())
        if not text:
            return text
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        text = re.sub(r"\s*-\s*(?:The\s+Times\s+of\s+India|Times\s+of\s+India|India\s+Today|BBC\s+News|Al\s*Jazeera)\.?", ".", text, flags=re.IGNORECASE)
        text = re.sub(r"(?:\b(?:The\s+Times\s+of\s+India|Times\s+of\s+India|India\s+Today|BBC\s+News|Al\s*Jazeera)\b\.?\s*)+", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+'", " '", text)
        text = re.sub(r"'\s+", "' ", text)
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
        if SequenceMatcher(None, normalized_body, normalized_source).ratio() >= 0.82:
            return True

        copied_sentences = 0
        for sentence in body_sentences:
            normalized_sentence = self._normalize_body_punctuation(sentence).lower()
            for source_sentence in source_sentences:
                normalized_source_sentence = self._normalize_body_punctuation(source_sentence).lower()
                if SequenceMatcher(None, normalized_sentence, normalized_source_sentence).ratio() >= 0.9:
                    copied_sentences += 1
                    break
        return copied_sentences >= 2

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

    def _has_dangling_tail(self, body: str) -> bool:
        text = " ".join((body or "").split()).lower()
        if not text:
            return False
        if re.search(r"\b(?:in|on|at|to|for|from|with|by|of|as|into|over|under|about|between|through|across|and|or|but|so|yet)\.$", text):
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

    def _ensure_complete_body(self, body: str, source_title: str, source_body: str, min_chars: int, max_chars: int) -> str:
        text = self._normalize_body_punctuation(body)
        if not text or not self._has_dangling_tail(text):
            return text

        fallback = text[:-1]
        stop = max(fallback.rfind("."), fallback.rfind("!"), fallback.rfind("?"))
        if stop > 0:
            text = fallback[: stop + 1].strip()
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

        if built:
            current = " ".join(built)
            if len(current) >= target_chars - 8:
                return current

        clipped = body[:max_chars]
        stop = max(clipped.rfind("."), clipped.rfind("!"), clipped.rfind("?"))
        if stop >= target_chars - 35:
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

    def _fit_body_length(self, body: str, source_title: str, source_body: str, target_chars: int, min_chars: int, max_chars: int) -> str:
        out = self._normalize_body_punctuation(body)
        if len(out) < min_chars:
            out = self._expand_body(out, source_title, source_body, target_chars=target_chars, max_chars=max_chars)
        if len(out) > max_chars:
            out = self._trim_body_to_band(out, max_chars=max_chars, target_chars=target_chars)
        out = self._normalize_body_punctuation(out)
        out = self._trim_weak_ending_sentence(out, source_title, source_body, min_chars=min_chars, max_chars=max_chars)
        out = self._ensure_complete_body(out, source_title, source_body, min_chars=min_chars, max_chars=max_chars)
        if len(out) < min_chars:
            out = self._expand_body(out, source_title, source_body, target_chars=max(target_chars, min_chars), max_chars=max_chars)
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
        target_body = 345
        min_body = 299
        max_body = 360

        article_title = " ".join((title or "").split())
        article_body = " ".join((body or "").split())[:5200]
        if not article_title or not article_body:
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
            "You are a disciplined Shortly editor. "
            "Write compact factual copy using only the source. "
            "Do not invent, speculate, add opinion, or add stylistic filler."
        )

        prompt = f"""Create a Shortly-style English article card from the source.

Source Title: {article_title}
Source Text: {article_body}

{style_examples}
{credibility_note}

Requirements:
- Output JSON only: {{"title":"...","body":"..."}}
- Title: 16-80 characters, sentence case, factual and direct.
- Title should clearly state the core update.
- Use sentence case with capitalization only for proper nouns and standard acronyms.
- Avoid filler words when a cleaner headline is possible, but do not make the headline sound forced.
- Never cut off a word to fit the limit. Rewrite shorter instead.
- Headline must be materially reworded from the source headline. Do not echo or lightly trim source wording.
- Body: 299-360 characters, in 3-5 short clean sentences.
- Sentence 1: core development.
- Sentence 2: context or scale.
- Sentence 3: consequence or next stake.
- Keep every sentence useful. Remove any line that does not add value.
- Use only facts present in the source.
- Rewrite the body in fresh wording. Do not copy source sentences verbatim unless a name or figure leaves no natural alternative.
- Prefer specific names when the source gives them. Avoid vague labels like "the US president" if the source names Trump.
- On first mention in the body, expand well-known institutions with acronym in brackets when the source uses the acronym, such as United Nations (UN) or Board of Control for Cricket in India (BCCI).
- Preserve exact technical designations from the source, such as aircraft, ship, missile, or military system identifiers.
- If the source says images, videos, social posts, or reports appear to show something, keep that uncertainty explicit. Do not turn it into confirmed fact.
- Never add a verification claim, investigation claim, or confirmation claim unless the source explicitly gives it.
- No source names, no publisher names, no clickbait phrases, no exclamation marks, no opinions.
- No repeated filler such as "this development", "meanwhile", "in a major move", "here is why".
- Avoid keeping the same sentence order and wording as the source when a clean rewrite is possible.
- Do not repeat the same fact in the final sentence and do not add generic explainer lines like "it is a vital waterway".
- Read the full source text before writing. Use later source details when they add important context, scale, timeline, or consequence.
- End with a complete sentence.
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
                                "Rewrite to be cleaner and more concrete. "
                                "Use only source facts, keep the title direct, and make the body sentence-driven and complete. "
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
