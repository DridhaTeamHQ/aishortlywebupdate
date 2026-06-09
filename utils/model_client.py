"""Shared model client, provider-agnostic module path.

Supports both the GPT-4o family (temperature + max_tokens) and the GPT-5 /
o-series reasoning family (max_completion_tokens + reasoning_effort, no custom
temperature). The class name is kept as GeminiClient for backward compatibility
with existing imports.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Optional

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


# Reasoning-family model prefixes (GPT-5 + o-series). These reject `max_tokens`
# and non-default `temperature`, and accept `reasoning_effort` / `verbosity`.
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")

# Reasoning tokens count against the completion budget, so give the model enough
# headroom that the visible output is never starved/truncated.
_REASONING_MIN_BUDGET = 3000


def _is_reasoning_model(model: str) -> bool:
    m = (model or "").lower().strip()
    return m.startswith(_REASONING_PREFIXES)


class GeminiClient:
    """OpenAI-powered text/vision client (class name kept for backward compat)."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        env_key = os.getenv("OPENAI_API_KEY", "")
        raw_key = env_key if env_key else (api_key or "")
        self.api_key = (raw_key or "").strip().strip('"').strip("'")
        self.model = (model or os.getenv("OPENAI_MODEL", "gpt-5-mini") or "gpt-5-mini").strip()
        self.reasoning_effort = (os.getenv("OPENAI_REASONING_EFFORT", "low") or "low").strip()
        self.verbosity = (os.getenv("OPENAI_VERBOSITY", "") or "").strip()
        self.client = OpenAI(api_key=self.api_key) if (self.api_key and OpenAI is not None) else None

    @property
    def available(self) -> bool:
        return self.client is not None

    @property
    def is_reasoning(self) -> bool:
        return _is_reasoning_model(self.model)

    def _build_kwargs(
        self,
        messages: list,
        *,
        temperature: float,
        max_output_tokens: int,
        response_mime_type: Optional[str],
        reasoning_effort: Optional[str],
        verbosity: Optional[str],
    ) -> dict:
        kwargs: dict = {"model": self.model, "messages": messages}

        if self.is_reasoning:
            # GPT-5 / o-series: budget must cover reasoning + visible output.
            kwargs["max_completion_tokens"] = max(int(max_output_tokens), _REASONING_MIN_BUDGET)
            effort = (reasoning_effort or self.reasoning_effort or "low").strip()
            if effort:
                kwargs["reasoning_effort"] = effort
            verb = (verbosity if verbosity is not None else self.verbosity).strip()
            if verb:
                kwargs["verbosity"] = verb
            # temperature intentionally omitted — only the default is supported.
        else:
            kwargs["max_tokens"] = int(max_output_tokens)
            kwargs["temperature"] = temperature

        if response_mime_type == "application/json":
            kwargs["response_format"] = {"type": "json_object"}

        return kwargs

    def _create_with_fallback(self, kwargs: dict):
        """Call the API, gracefully dropping params an older endpoint rejects."""
        attempt = dict(kwargs)
        for _ in range(4):
            try:
                return self.client.chat.completions.create(**attempt)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                dropped = False
                for param in ("verbosity", "reasoning_effort"):
                    if param in attempt and (param in msg and ("unsupported" in msg or "unknown" in msg or "not supported" in msg)):
                        attempt.pop(param, None)
                        dropped = True
                        break
                if not dropped and "max_tokens" in msg and "max_completion_tokens" in msg and "max_tokens" in attempt:
                    attempt["max_completion_tokens"] = attempt.pop("max_tokens")
                    dropped = True
                if not dropped and "temperature" in msg and "temperature" in attempt:
                    attempt.pop("temperature", None)
                    dropped = True
                if not dropped:
                    raise
        # Final attempt without optional knobs.
        return self.client.chat.completions.create(**attempt)

    def generate_text(
        self,
        contents,
        *,
        system_instruction: str = "",
        temperature: float = 0.2,
        max_output_tokens: int = 800,
        response_mime_type: Optional[str] = None,
        response_schema=None,
        reasoning_effort: Optional[str] = None,
        verbosity: Optional[str] = None,
    ) -> str:
        if not self.available:
            raise RuntimeError("OpenAI client is not available")

        user_text = contents if isinstance(contents, str) else json.dumps(contents, ensure_ascii=False)
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": user_text})

        kwargs = self._build_kwargs(
            messages,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type=response_mime_type,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
        )
        response = self._create_with_fallback(kwargs)
        content = response.choices[0].message.content
        if isinstance(content, str):
            return content.strip()
        return str(content or "").strip()

    def generate_json(
        self,
        contents,
        *,
        system_instruction: str = "",
        temperature: float = 0.2,
        max_output_tokens: int = 800,
        schema=None,
        reasoning_effort: Optional[str] = None,
    ) -> str:
        del schema
        return self.generate_text(
            contents,
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
            reasoning_effort=reasoning_effort,
        )

    def generate_text_with_image(
        self,
        prompt: str,
        image_bytes: bytes,
        *,
        mime_type: str = "image/jpeg",
        system_instruction: str = "",
        temperature: float = 0.0,
        max_output_tokens: int = 200,
        response_mime_type: Optional[str] = None,
        response_schema=None,
        reasoning_effort: Optional[str] = None,
    ) -> str:
        if not self.available:
            raise RuntimeError("OpenAI client is not available")

        data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        )
        kwargs = self._build_kwargs(
            messages,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type=response_mime_type,
            reasoning_effort=reasoning_effort,
            verbosity=None,
        )
        response = self._create_with_fallback(kwargs)
        content = response.choices[0].message.content
        if isinstance(content, str):
            return content.strip()
        return str(content or "").strip()
