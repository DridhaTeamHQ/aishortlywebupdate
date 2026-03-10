"""English summarizer with newsroom-style hook writing."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from openai import OpenAI

from utils.logger import get_logger


class Summarizer:
    """Creates concise, professional English title/body for CMS."""

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
    _ONGOING_TOKENS = frozenset(
        {
            "war", "conflict", "talks", "clashes", "clash", "operation", "probe", "investigation",
            "search", "rescue", "ceasefire", "negotiation", "standoff", "strike", "bombing", "protest",
            "hearing", "case", "crisis", "exchange", "campaign", "offensive",
        }
    )
    _STYLE_STOPWORDS = frozenset(
        {
            "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "in", "into",
            "is", "it", "its", "of", "on", "or", "that", "the", "their", "this", "to", "was", "were", "will", "with",
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
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
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
        out = re.sub(r"\bUS-([a-z])", lambda m: f"US-{m.group(1).upper()}", out)
        return out

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

    def _normalize_body_punctuation(self, body: str) -> str:
        text = " ".join((body or "").split())
        if not text:
            return text
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        text = re.sub(r"\s+'", " '", text)
        text = re.sub(r"'\s+", "' ", text)
        if text[-1] not in ".!?":
            text = f"{text}."
        text = re.sub(r"\.\.+", ".", text)
        return text

    def _has_dangling_tail(self, body: str) -> bool:
        text = " ".join((body or "").split()).lower()
        if not text:
            return False
        if re.search(r"\b(?:in|on|at|to|for|from|with|by|of|as|into|over|under|about|between|through|across)\.$", text):
            return True
        if re.search(
            r"\b(?:in|on|at|to|for|from|with|by|of|as|into|over|under|about|between|through|across)\s+"
            r"(?:the|a|an|his|her|their|its|this|that)\.$",
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
            text = self._expand_body(text, source_title, source_body, target_chars=max(min_chars, 355), max_chars=max_chars)
            text = self._normalize_body_punctuation(text)

        if len(text) > max_chars:
            text = self._trim_body_to_band(text, max_chars=max_chars, target_chars=max(min_chars, 355))
            text = self._normalize_body_punctuation(text)

        if self._has_dangling_tail(text):
            closure = " Analysts say pressure is continuing to build."
            if len(text) + len(closure) <= max_chars:
                text = self._normalize_body_punctuation(text + closure)
            else:
                text = text.rstrip(" ,:-")
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

    def _is_duplicate_sentence(self, candidate: str, existing: List[str]) -> bool:
        candidate_sig = self._sentence_signature(candidate)
        if not candidate_sig:
            return True
        for sentence in existing:
            overlap = len(candidate_sig & self._sentence_signature(sentence))
            if overlap >= max(4, min(len(candidate_sig), 6)):
                return True
        return False

    def _source_sentence_candidates(self, title: str, body: str, existing: List[str]) -> List[str]:
        scored = []
        seen = list(existing)
        for sentence in self._split_sentences(body):
            normalized = self._normalize_body_punctuation(sentence)
            if len(normalized) < 45:
                continue
            if self._is_duplicate_sentence(normalized, seen):
                continue
            score = 0
            low = normalized.lower()
            score += len(self._sentence_signature(normalized))
            if re.search(r"\b\d+\b", normalized):
                score += 4
            if any(token in low for token in self._ONGOING_TOKENS):
                score += 3
            if any(token in low for token in self._ACTION_VERBS):
                score += 2
            if any(word[:1].isupper() for word in normalized.split()[1:4]):
                score += 1
            scored.append((score, normalized))
            seen.append(normalized)

        title_sentence = self._normalize_body_punctuation(title)
        if len(title_sentence) >= 35 and not self._is_duplicate_sentence(title_sentence, seen):
            scored.append((5, title_sentence))

        scored.sort(key=lambda item: -item[0])
        return [item[1] for item in scored]

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

    def _expand_body(self, text: str, source_title: str, source_body: str, target_chars: int = 360, max_chars: int = 365) -> str:
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
            fragment_sources = list(reversed(existing + candidates))
            used_fragments: set = set()
            for source in fragment_sources:
                tokens = source.rstrip('.!?').split()
                start = max(6, min(10, len(tokens) // 2))
                for size in range(start, len(tokens) + 1):
                    fragment = " ".join(tokens[:size]).rstrip(" ,.-")
                    if not fragment:
                        continue
                    # Dedup: skip if fragment overlaps >60% with existing text
                    frag_words = set(fragment.lower().split())
                    out_words = set(out.lower().split())
                    if frag_words and out_words:
                        overlap = len(frag_words & out_words) / len(frag_words)
                        if overlap >= 0.6:
                            continue
                    frag_key = " ".join(sorted(frag_words))
                    if frag_key in used_fragments:
                        continue
                    used_fragments.add(frag_key)
                    proposal = f"{out} {fragment}.".strip()
                    if len(proposal) <= max_chars:
                        out = proposal
                    if len(out) >= target_chars - 5:
                        break
                if len(out) >= target_chars - 5:
                    break

        if len(out) < target_chars - 5:
            tail_options = []
            title_words = source_title.rstrip('.!?').split()
            for size in range(2, min(5, len(title_words)) + 1):
                tail_options.append(" ".join(title_words[-size:]))
            for sentence in self._split_sentences(source_body):
                for clause in re.split(r",|;", sentence):
                    clause = clause.strip(" ,.-")
                    if len(clause) >= 8:
                        tail_options.append(clause)

            for tail in tail_options:
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
        return self._ensure_complete_body(out, source_title, source_body, min_chars=min_chars, max_chars=max_chars)

    def _has_present_continuous(self, body: str) -> bool:
        low = (body or "").lower()
        return bool(re.search(r"\b(is|are)\s+[a-z]{3,}ing\b", low))

    def _requires_present_continuous(self, title: str, body: str) -> bool:
        low = f"{title} {body}".lower()
        return any(tok in low for tok in self._ONGOING_TOKENS)

    def _inject_present_continuous_flow(self, body: str, max_chars: int = 365) -> str:
        text = self._normalize_body_punctuation(body)
        if self._has_present_continuous(text):
            return text

        bridge = " Authorities are monitoring the situation as updates are coming in."
        if len(text) + len(bridge) <= max_chars:
            return self._normalize_body_punctuation(text + bridge)

        return text

    def _boost_title_punch(self, title: str, source_title: str) -> str:
        clean = self._normalize_title_punctuation(self._normalize_acronyms(self._clean_title_noise(title)))
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
        out = self._restore_proper_nouns(self._to_sentence_case_headline(out), source_title)
        return out[:62].rstrip(" ,.-")
    def summarize(self, title: str, body: str) -> Optional[Dict[str, str]]:
        min_title = 36
        max_title = 62
        target_body = 350
        min_body = 299
        max_body = 360

        article_title = " ".join((title or "").split())
        article_body = " ".join((body or "").split())[:2600]
        if not article_title or not article_body:
            return None

        dynamic_examples = self._build_dynamic_style_examples(article_title, article_body)

        system_msg = (
            "You are a senior newsroom editor. Write high-clarity, hook-led copy that is factual, specific, and engaging. "
            "Use active voice and concrete facts. No clickbait and no sensational speculation. "
            "Headlines must be notification-ready: actor + action + consequence."
        )

        prompt = f"""Write a clean news package in JSON.

Source Title: {article_title}
Source Text: {article_body}

{dynamic_examples}

{self._STYLE_BANK}

Requirements:
- Output JSON only: {{"title":"...","body":"..."}}
- Title: 36-62 characters, sentence case (not Title Case), active verb, clear angle.
- Title should open with the key development, not generic filler.
- Title must be clickable but factual: include who did what and the immediate stake or consequence.
- Keep proper nouns capitalized (people, parties, countries, institutions).
- Prefer sharper newsroom verbs such as hits, jolts, warns, scrambles, squeezes, blocks, races, surges, slams.
- Prefer one of these title devices: number, quote fragment, direct contrast, or high-stakes consequence.
- If reference examples are relevant, match their directness and compression without copying phrasing.
- Body: 299-360 characters, ideally near 350, in 4-5 sentences.
- Sentence 1 must be a factual hook with the most important development.
- For ongoing developments, prefer present continuous in body (is/are + verb-ing).
- Add quick context by sentence 2 and immediate consequence by sentence 3.
- Use clean punctuation in every sentence.
- Avoid generic connective lines like "This development..." or repeated "This..." sentence starts.
- Tone: professional and engaging, never sensational.
"""

        last_content = ""
        for attempt in range(3):
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
                                "Rewrite for stronger hook and engagement while staying factual. "
                                "Title 36-62 chars with active verb, clear consequence; "
                                "body 299-360 chars, concrete and tight, with clean punctuation; JSON only."
                            ),
                        }
                    )

                response = self.client.chat.completions.create(
                    model="gpt-4o",
                    messages=messages,
                    temperature=0.2,
                    max_tokens=520,
                )

                last_content = (response.choices[0].message.content or "").strip()
                raw = last_content
                if "```" in raw:
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]

                parsed = json.loads(raw)
                if "title" not in parsed or "body" not in parsed:
                    self.logger.error("Missing title/body in summary response")
                    return None

                title_out = self._restore_proper_nouns(self._to_sentence_case_headline(self._normalize_title_punctuation(self._normalize_acronyms(self._clean_title_noise(" ".join(str(parsed["title"]).split()))))), article_title, article_body)
                body_out = self._fit_body_length(
                    " ".join(str(parsed["body"]).split()),
                    article_title,
                    article_body,
                    target_chars=target_body,
                    min_chars=min_body,
                    max_chars=max_body,
                )

                if len(title_out) > max_title:
                    title_out = title_out[:max_title].rstrip(" ,.-")
                if len(title_out) < min_title:
                    if attempt == 0:
                        continue
                    return None

                if len(body_out) < min_body or len(body_out) > max_body:
                    self.logger.warning(
                        f"Summary length out of range (title={len(title_out)}, body={len(body_out)}), retrying..."
                    )
                    if attempt == 0:
                        continue
                    return None

                if not self._has_title_hook(title_out):
                    title_out = self._boost_title_punch(title_out, article_title)

                if (not self._has_title_hook(title_out)) or (not self._has_body_hook(body_out)):
                    self.logger.warning("Summary lacks hook/punch quality, retrying...")
                    if attempt < 2:
                        continue

                if re.search(r",\s+(escalates|intensifies|deepens|widens|triggers|raises|sparks)\b", title_out, flags=re.IGNORECASE):
                    self.logger.warning("Title punctuation feels awkward, retrying...")
                    if attempt == 0:
                        continue

                if self._looks_template_body(body_out):
                    self.logger.warning("Summary body sounds template-like, retrying...")
                    if attempt == 0:
                        continue

                if self._requires_present_continuous(article_title, article_body) and not self._has_present_continuous(body_out):
                    body_out = self._inject_present_continuous_flow(body_out, max_chars=max_body)
                    if not self._has_present_continuous(body_out):
                        self.logger.warning("Summary body missing present-continuous flow, retrying...")
                        if attempt == 0:
                            continue
                    body_out = self._fit_body_length(
                        body_out,
                        article_title,
                        article_body,
                        target_chars=target_body,
                        min_chars=min_body,
                        max_chars=max_body,
                    )
                    if len(body_out) < min_body or len(body_out) > max_body:
                        if attempt == 0:
                            continue
                        return None
                self.logger.info(f"Summary ready: title={len(title_out)} chars, body={len(body_out)} chars")
                return {"title": title_out, "body": body_out}
            except Exception as exc:
                self.logger.error(f"Summarization failed: {exc}")
                return None

        return None














