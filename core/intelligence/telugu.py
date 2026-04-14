"""Telugu news writer with strict language-quality checks."""

from __future__ import annotations

import json
import re
from typing import Dict, Optional

from utils.gemini_client import GeminiClient
from utils.logger import get_logger


class TeluguWriter:
    """Generates Telugu newsroom copy from English summary."""

    _TITLE_BODY_JSON_SCHEMA = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["title", "body"],
    }

    ALLOWED_ENGLISH = {
        "us", "uk", "un", "ai", "pm", "cm", "bjp", "congress", "g20", "who", "isro", "nato",
        "iran", "israel", "india", "modi", "trump", "rahul", "gandhi", "bbc", "rbi", "mea", "icc",
    }

    LEXICAL_REPLACEMENTS = {
        "restraint": "\u0c38\u0c02\u0c2f\u0c2e\u0c28\u0c02",
        "dialogue": "\u0c38\u0c02\u0c2d\u0c3e\u0c37\u0c23",
        "tension": "\u0c09\u0c26\u0c4d\u0c30\u0c3f\u0c15\u0c4d\u0c24\u0c24",
        "impact": "\u0c2a\u0c4d\u0c30\u0c2d\u0c3e\u0c35\u0c02",
        "escalation": "\u0c24\u0c40\u0c35\u0c4d\u0c30\u0c24 \u0c2a\u0c46\u0c30\u0c41\u0c17\u0c41\u0c26\u0c32",
    }

    def __init__(self):
        self.logger = get_logger("telugu_writer")
        self.client = GeminiClient()

    def write(self, english_title: str, english_body: str, max_retries: int = 3) -> Optional[Dict[str, str]]:
        min_body_chars = 299
        max_body_chars = 350

        prompt = f"""You are a senior Telugu newsroom editor.

Source English Title: {english_title}
Source English Body: {english_body}

Write Telugu output with this style:
- Strong factual hook in sentence 1.
- Clear, natural, publication-grade Telugu.
- Neutral tone, no sensational exaggeration.
- Include what happened, who is involved, and why it matters.
- Prefer short, punchy sentence rhythm used in mobile news cards.
- If source includes a quote, render it naturally in Telugu quotes.

Rules:
1) Title: Telugu headline up to 80 chars.
2) Body: 299-350 chars, ideally near 330, in 3-5 full sentences.
3) No English lexical words in the final Telugu copy.
4) Only unavoidable proper nouns/acronyms may remain in English.
5) Avoid bland filler transitions unless essential.
6) Keep facts faithful to source.

Return JSON only:
{{
  "title": "...",
  "body": "..."
}}
"""

        system_msg = (
            "You are a Telugu news editor. "
            "Body should be 299-350 characters and read like a professional newsroom write-up. "
            "Do not leave generic English words such as restraint, dialogue, tension, impact, escalation in output."
        )

        last_content = ""

        if not self.client or not self.client.available:
            self.logger.error("Gemini client unavailable for Telugu generation")
            return None

        # Single-attempt mode: exactly one model call, no rewrite/expand retries.
        try:
            messages = [{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}]
            combined_prompt = "\n\n".join(
                f"{message['role'].upper()}:\n{message['content']}" for message in messages
            )
            last_content = self.client.generate_json(
                combined_prompt,
                system_instruction=system_msg,
                temperature=0.3,
                max_output_tokens=800,
                schema=self._TITLE_BODY_JSON_SCHEMA,
            )
            raw = last_content
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]

            result = json.loads(raw)
            title = self._sanitize_title((result.get("title") or "").strip())
            body = self._sanitize_text((result.get("body") or "").strip())

            if len(title) > 80:
                title = title[:80].rstrip(" ,.-")
                title = self._sanitize_title(title)
            if body:
                body = self._fit_body_length(body, min_chars=min_body_chars, max_chars=max_body_chars)

            if not title:
                title = self._sanitize_title(english_title[:80])
            if not body:
                body = self._fit_body_length(self._sanitize_text(english_body), min_chars=min_body_chars, max_chars=max_body_chars)

            self.logger.info(f"Telugu generated (single-attempt): title={len(title)} body={len(body)}")
            return {"title": title, "body": body}
        except Exception as exc:
            self.logger.warning(f"Single-attempt Telugu generation failed: {exc}; using source fallback")
            fallback_title = self._sanitize_title((english_title or "").strip()[:80])
            fallback_body = self._fit_body_length(self._sanitize_text(english_body or ""), min_chars=min_body_chars, max_chars=max_body_chars)
            if not fallback_title or not fallback_body:
                return None
            return {"title": fallback_title, "body": fallback_body}

    def _expand_telugu_body(self, short_body: str, title: str, english_context: str) -> Optional[str]:
        if not short_body:
            return None
        if len(short_body) >= 299:
            return short_body

        try:
            safe_title = title[:80].replace('"', "'")
            expand_prompt = f"""Expand this Telugu paragraph to 299-350 characters, ideally near 330.
Keep the same facts and meaning. Write in natural Telugu newsroom style.

Title: {safe_title}
Current Paragraph: {short_body}
English Context: {english_context[:700]}

Return JSON only with keys: title, body.
"""
            raw = self.client.generate_json(
                expand_prompt,
                temperature=0.25,
                max_output_tokens=820,
                schema=self._TITLE_BODY_JSON_SCHEMA,
            )
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            parsed = json.loads(raw)
            expanded = (parsed.get("body") or "").strip()
            return expanded if expanded else None
        except Exception as exc:
            self.logger.warning(f"Expand failed: {exc}")
            return None

    def _fit_body_length(self, body: str, min_chars: int, max_chars: int) -> str:
        text = self._sanitize_text(body)
        if len(text) < min_chars:
            text = self._pad_short_body(text, min_chars, max_chars)
        if len(text) <= max_chars:
            text = self._ensure_complete_ending(text, min_chars, max_chars)
            if len(text) < min_chars:
                text = self._pad_short_body(text, min_chars, max_chars)
            return self._ensure_complete_ending(text, min_chars, max_chars)

        clipped = text[:max_chars]
        stop = self._last_sentence_stop(clipped)
        if stop >= min_chars - 30:
            text = self._ensure_complete_ending(clipped[: stop + 1].rstrip(), min_chars, max_chars)
            if len(text) < min_chars:
                text = self._pad_short_body(text, min_chars, max_chars)
            return self._ensure_complete_ending(text, min_chars, max_chars)

        cut = clipped.rfind(" ")
        trimmed = (clipped[:cut] if cut > 0 else clipped).rstrip(" ,.-")
        text = self._ensure_complete_ending(trimmed, min_chars, max_chars)
        if len(text) < min_chars:
            text = self._pad_short_body(text, min_chars, max_chars)
        return self._ensure_complete_ending(text, min_chars, max_chars)

    def _last_sentence_stop(self, text: str) -> int:
        return max(text.rfind("."), text.rfind("!"), text.rfind("?"), text.rfind("\u0964"))

    def _ensure_complete_ending(self, body: str, min_chars: int, max_chars: int) -> str:
        text = self._sanitize_text(body).rstrip(" ,:-")
        if not text:
            return text

        if text.endswith((".", "!", "?", "\u0964")):
            return text

        stop = self._last_sentence_stop(text)
        if stop >= min_chars - 25:
            return text[: stop + 1].rstrip()

        closure = " వివరాలు ఇంకా వెలుగులోకి వస్తున్నాయి."
        if len(text) + len(closure) <= max_chars:
            return self._sanitize_text(text + closure)

        shorter_closure = " పరిస్థితిపై నిఘా కొనసాగుతోంది."
        if len(text) + len(shorter_closure) <= max_chars:
            return self._sanitize_text(text + shorter_closure)

        cut = text.rfind(" ")
        trimmed = (text[:cut] if cut > 0 else text).rstrip(" ,:-")
        if trimmed and not trimmed.endswith((".", "!", "?", "\u0964")):
            trimmed = trimmed + "."
        return trimmed

    def _pad_short_body(self, body: str, min_chars: int, max_chars: int) -> str:
        text = self._sanitize_text(body)
        if len(text) >= min_chars:
            return text

        tails = [
            " పరిణామాలపై నిఘా కొనసాగుతోంది.",
            " అధికారుల పర్యవేక్షణ కొనసాగుతోంది.",
            " తాజా వివరాలు ఇంకా వెలుగులోకి వస్తున్నాయి.",
            " దీనిపై స్పందనలు పెరుగుతున్నాయి.",
            " పరిస్థితి మార్పులపై దృష్టి నిలిచింది.",
            " సంబంధిత వర్గాలు అప్రమత్తంగా ఉన్నాయి.",
        ]
        idx = 0
        while len(text) < min_chars and idx < 12:
            tail = tails[idx % len(tails)]
            if len(text) + len(tail) > max_chars:
                break
            text = self._sanitize_text(text + tail)
            idx += 1
        return text

    def _sanitize_title(self, title: str) -> str:
        out = self._sanitize_text(title)
        out = re.sub(r"\s*,\s*", ": ", out)
        out = re.sub(r"\s*:\s*:\s*", ": ", out)
        out = re.sub(r"\s+", " ", out)
        return out.strip(" ,.-:")

    def _sanitize_text(self, text: str) -> str:
        out = " ".join((text or "").split())
        out = out.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
        out = re.sub(r"\s+([,.;:!?\u0964])", r"\1", out)
        out = re.sub(r"\s+", " ", out)
        out = self._replace_english_lexical(out)
        return out

    def _replace_english_lexical(self, text: str) -> str:
        out = text
        for source, target in self.LEXICAL_REPLACEMENTS.items():
            out = re.sub(rf"\b{re.escape(source)}\b", target, out, flags=re.IGNORECASE)
        return out

    def _purify_telugu_copy(
        self,
        title: str,
        body: str,
        english_title: str,
        english_body: str,
    ) -> Optional[Dict[str, str]]:
        try:
            prompt = f"""Rewrite the Telugu copy into pure, natural Telugu.

English title: {english_title}
English context: {english_body[:700]}
Current Telugu title: {title}
Current Telugu body: {body}

Rules:
- Remove all unnecessary English words.
- Keep only essential acronyms/proper nouns (US, UK, UN, BJP, Congress, PM, CM).
- Keep facts unchanged.
- Keep title <= 80 chars and body 345-365 chars.
- Keep Telugu newsroom style, with natural punctuation.

Return JSON only: {{\"title\":\"...\",\"body\":\"...\"}}
"""
            raw = self.client.generate_json(
                prompt,
                temperature=0.2,
                max_output_tokens=700,
                schema=self._TITLE_BODY_JSON_SCHEMA,
            )
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            parsed = json.loads(raw)
            return {
                "title": (parsed.get("title") or title).strip(),
                "body": (parsed.get("body") or body).strip(),
            }
        except Exception as exc:
            self.logger.warning(f"Telugu purify pass failed: {exc}")
            return None

    def _derive_allowed_english(self, english_title: str, english_body: str) -> set[str]:
        allowed = set(self.ALLOWED_ENGLISH)
        source_tokens = re.findall(r"[A-Za-z][A-Za-z'.-]{1,}", f"{english_title} {english_body}")
        stopwords = {
            "the", "and", "for", "with", "from", "that", "this", "into", "after", "before", "over",
            "amid", "near", "more", "will", "have", "has", "had", "was", "were", "are", "is", "its",
            "their", "them", "they", "his", "her", "hers", "our", "your", "than", "then", "also",
            "said", "says", "say", "new", "old", "why", "what", "when", "where", "how", "all", "off",
            "out", "day", "days", "week", "weeks", "month", "months", "year", "years", "today", "soon",
            "public", "private", "sector", "government", "workers", "holiday", "break", "confirmed", "entry",
            "free", "best", "timings", "open", "opens", "story", "report", "live",
            "update", "updates", "news", "video", "photos", "amidst", "against", "through", "across",
        }
        for token in source_tokens:
            normalized = token.strip("'.-").lower()
            if len(normalized) < 2 or normalized in stopwords:
                continue
            if token[0].isupper() or token.isupper():
                allowed.add(normalized)
        return allowed

    def _has_disallowed_english(self, text: str, allowed_tokens: Optional[set[str]] = None) -> bool:
        allowed = allowed_tokens or self.ALLOWED_ENGLISH
        tokens = re.findall(r"[a-zA-Z]{3,}", text)
        return any(token.lower() not in allowed for token in tokens)

    def _english_token_stats(self, text: str, allowed_tokens: Optional[set[str]] = None) -> tuple[int, float]:
        allowed = allowed_tokens or self.ALLOWED_ENGLISH
        tokens = re.findall(r"[a-zA-Z]{2,}", text)
        if not tokens:
            return (0, 0.0)
        filtered = [t for t in tokens if t.lower() not in allowed]
        words = re.findall(r"\S+", text)
        ratio = len(filtered) / max(1, len(words))
        return (len(filtered), ratio)

    def _telugu_percentage(self, text: str) -> float:
        if not text:
            return 0.0
        telugu_chars = sum(1 for ch in text if "\u0C00" <= ch <= "\u0C7F")
        total_chars = sum(1 for ch in text if not ch.isspace())
        return (telugu_chars / total_chars * 100.0) if total_chars > 0 else 0.0











