"""Shared utilities with compatibility shims for production workers."""

from __future__ import annotations

import json
import os
import sys
import types as py_types
from typing import Optional

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


def _install_gemini_client_fallback() -> None:
    """
    Ensure imports like `from utils.gemini_client import GeminiClient` work
    even when that module file is missing in a deployment artifact.
    """

    if "utils.gemini_client" in sys.modules:
        return

    mod = py_types.ModuleType("utils.gemini_client")

    class GeminiClient:  # pylint: disable=too-few-public-methods
        """
        Backward-compatible client name.
        Uses OpenAI by default; Gemini is optional and not required.
        """

        def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
            env_key = os.getenv("OPENAI_API_KEY", "")
            raw_key = env_key if env_key else (api_key or "")
            self.api_key = (raw_key or "").strip().strip('"').strip("'")
            self.model = (model or os.getenv("OPENAI_MODEL", "gpt-4o")).strip() or "gpt-4o"
            self.client = OpenAI(api_key=self.api_key) if (self.api_key and OpenAI is not None) else None

        @property
        def available(self) -> bool:
            return self.client is not None

        def generate_text(
            self,
            contents,
            *,
            system_instruction: str = "",
            temperature: float = 0.2,
            max_output_tokens: int = 800,
            response_mime_type: Optional[str] = None,
            response_schema=None,
        ) -> str:
            if not self.available:
                raise RuntimeError("OpenAI client is not available")

            user_text = contents if isinstance(contents, str) else json.dumps(contents, ensure_ascii=False)
            messages = []
            if system_instruction:
                messages.append({"role": "system", "content": system_instruction})
            messages.append({"role": "user", "content": user_text})

            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_output_tokens,
            }
            if response_mime_type == "application/json":
                kwargs["response_format"] = {"type": "json_object"}

            response = self.client.chat.completions.create(**kwargs)
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
        ) -> str:
            del schema
            return self.generate_text(
                contents,
                system_instruction=system_instruction,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                response_mime_type="application/json",
            )

    mod.GeminiClient = GeminiClient
    sys.modules["utils.gemini_client"] = mod


_install_gemini_client_fallback()

GeminiClient = sys.modules["utils.gemini_client"].GeminiClient
__all__ = ["GeminiClient"]
