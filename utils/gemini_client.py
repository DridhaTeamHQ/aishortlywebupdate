"""Shared model client used across text and vision features."""

from __future__ import annotations

import base64
import json
import os
from typing import Optional

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

try:
    from google import genai
    from google.genai import types
except Exception:  # pragma: no cover
    genai = None  # type: ignore
    types = None  # type: ignore


class GeminiClient:
    """Provider-aware wrapper that supports OpenAI and Gemini backends."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        requested_provider = (os.getenv("AI_PROVIDER", "openai") or "openai").strip().lower()
        self.provider = requested_provider if requested_provider in {"openai", "gemini"} else "openai"

        if self.provider == "openai":
            env_key = os.getenv("OPENAI_API_KEY", "")
            raw_key = env_key if env_key else (api_key or "")
            default_model = (os.getenv("OPENAI_MODEL", "gpt-4o") or "gpt-4o").strip()
            chosen_model = (model or "").strip()
            if not chosen_model or chosen_model.lower().startswith("gemini"):
                chosen_model = default_model
        else:
            env_key = os.getenv("GEMINI_API_KEY", "")
            raw_key = env_key if env_key else (api_key or "")
            default_model = (os.getenv("GEMINI_MODEL", "gemini-2.5-pro") or "gemini-2.5-pro").strip()
            chosen_model = (model or "").strip()
            if not chosen_model or chosen_model.lower().startswith(("gpt-", "chatgpt-")):
                chosen_model = default_model

        self.api_key = (raw_key or "").strip().strip('"').strip("'")
        self.model = chosen_model or default_model
        self.client = None

        if self.provider == "openai" and self.api_key and OpenAI is not None:
            self.client = OpenAI(api_key=self.api_key)
        elif self.provider == "gemini" and self.api_key and genai is not None:
            self.client = genai.Client(api_key=self.api_key)

    @property
    def available(self) -> bool:
        if self.provider == "openai":
            return self.client is not None and OpenAI is not None
        return self.client is not None and types is not None

    def _build_gemini_config(
        self,
        system_instruction: str = "",
        temperature: float = 0.2,
        max_output_tokens: int = 800,
        response_mime_type: Optional[str] = None,
        response_schema=None,
    ):
        if types is None:
            return None

        kwargs = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
            "thinking_config": types.ThinkingConfig(thinking_budget=128),
        }
        if system_instruction:
            kwargs["system_instruction"] = system_instruction
        if response_mime_type:
            kwargs["response_mime_type"] = response_mime_type
        if response_schema is not None:
            kwargs["response_schema"] = response_schema
        return types.GenerateContentConfig(**kwargs)

    def _extract_gemini_response_text(self, response) -> str:
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            if isinstance(parsed, str):
                return parsed.strip()
            return json.dumps(parsed, ensure_ascii=False)

        direct_text = (getattr(response, "text", "") or "").strip()
        if direct_text:
            return direct_text

        chunks = []
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                part_text = getattr(part, "text", None)
                if part_text:
                    chunks.append(part_text)
        return "\n".join(chunk.strip() for chunk in chunks if chunk and chunk.strip()).strip()

    def _normalize_text_contents(self, contents) -> str:
        if isinstance(contents, str):
            return contents
        if isinstance(contents, (list, tuple)):
            parts = []
            for item in contents:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and "content" in item:
                    parts.append(str(item.get("content", "")))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            return "\n".join(part for part in parts if part).strip()
        return str(contents or "").strip()

    def _openai_response_format(self, response_mime_type: Optional[str]) -> Optional[dict]:
        if response_mime_type == "application/json":
            return {"type": "json_object"}
        return None

    def _extract_openai_response_text(self, response) -> str:
        if not getattr(response, "choices", None):
            return ""
        message = response.choices[0].message
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks = []
            for item in content:
                text_value = getattr(item, "text", None)
                if text_value:
                    chunks.append(text_value)
                elif isinstance(item, dict) and item.get("type") == "text":
                    chunks.append(str(item.get("text", "")))
            return "\n".join(chunk.strip() for chunk in chunks if chunk and chunk.strip()).strip()
        return str(content or "").strip()

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
            raise RuntimeError(f"{self.provider} client is not available")

        if self.provider == "openai":
            messages = []
            if system_instruction:
                messages.append({"role": "system", "content": system_instruction})
            messages.append({"role": "user", "content": self._normalize_text_contents(contents)})

            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_output_tokens,
            }
            response_format = self._openai_response_format(response_mime_type)
            if response_format is not None:
                kwargs["response_format"] = response_format

            response = self.client.chat.completions.create(**kwargs)
            return self._extract_openai_response_text(response)

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=self._build_gemini_config(
                system_instruction=system_instruction,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                response_mime_type=response_mime_type,
                response_schema=response_schema,
            ),
        )
        return self._extract_gemini_response_text(response)

    def generate_json(
        self,
        contents,
        *,
        system_instruction: str = "",
        temperature: float = 0.2,
        max_output_tokens: int = 800,
        schema=None,
    ) -> str:
        return self.generate_text(
            contents,
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
            response_schema=schema,
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
    ) -> str:
        if not self.available:
            raise RuntimeError(f"{self.provider} client is not available")

        if self.provider == "openai":
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
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_output_tokens,
            }
            response_format = self._openai_response_format(response_mime_type)
            if response_format is not None:
                kwargs["response_format"] = response_format

            response = self.client.chat.completions.create(**kwargs)
            return self._extract_openai_response_text(response)

        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        return self.generate_text(
            [prompt, image_part],
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type=response_mime_type,
            response_schema=response_schema,
        )
