"""
Agent job runner for Railway/worker execution.

This loader is defensive against import-path issues in hosted environments.
It guarantees that legacy ``utils.gemini_client`` and newer ``utils.model_client``
both resolve, even when only one file exists, by installing an OpenAI-backed
compatibility shim.
"""

import asyncio
import importlib
import importlib.util
import json
import os
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict


@dataclass
class RunResult:
    status: str
    error: str = ""


class AgentJobRunner:
    def __init__(
        self,
        cancel_check: Callable[[], bool],
        event_sink: Callable[[str, Dict[str, Any]], Any],
        repo_path: str | None = None,
    ):
        self.cancel_check = cancel_check
        self.event_sink = event_sink
        self.repo_path = repo_path

    async def run(self) -> RunResult:
        try:
            orchestrator = self._build_orchestrator()
            if hasattr(orchestrator, "set_runtime_controls"):
                orchestrator.set_runtime_controls(
                    cancel_check=self.cancel_check,
                    event_sink=self.event_sink,
                )

            await orchestrator.run()
            if self.cancel_check():
                return RunResult(status="cancelled")
            return RunResult(status="succeeded")
        except Exception as exc:
            await self._safe_emit("ERROR", {"message": str(exc)})
            return RunResult(status="failed", error=str(exc))

    def _build_orchestrator(self):
        orchestrator_module = self._load_orchestrator_module()
        orchestrator_cls = getattr(
            orchestrator_module,
            "HardenedOrchestrator",
            getattr(orchestrator_module, "Orchestrator", None),
        )
        if orchestrator_cls is None:
            raise RuntimeError("Unable to locate orchestrator class in agent repo")
        return orchestrator_cls()

    def _load_orchestrator_module(self):
        repo_root = self._resolve_agent_repo_root()
        if not repo_root.exists():
            raise RuntimeError(f"Agent repo path not found: {repo_root}")

        repo_root_str = str(repo_root.resolve())
        sys.path = [p for p in sys.path if p != repo_root_str]
        sys.path.insert(0, repo_root_str)

        for mod_name in list(sys.modules):
            if mod_name in ("core", "config", "utils") or mod_name.startswith(("core.", "config.", "utils.")):
                sys.modules.pop(mod_name, None)

        self._register_utils_package(repo_root)

        try:
            return importlib.import_module("core.orchestrator")
        except (ImportError, ModuleNotFoundError) as exc:
            err = str(exc)
            name = getattr(exc, "name", "")
            if (
                "utils.gemini_client" in err
                or "utils.model_client" in err
                or "GeminiClient" in err
                or name in {"utils", "utils.gemini_client", "utils.model_client"}
            ):
                self._install_gemini_client_fallback()
                if (
                    "utils" in sys.modules
                    and any(key in sys.modules for key in ("utils.model_client", "utils.gemini_client"))
                ):
                    model_module = sys.modules.get("utils.model_client") or sys.modules.get("utils.gemini_client")
                    if model_module is not None and hasattr(model_module, "GeminiClient"):
                        sys.modules["utils"].GeminiClient = model_module.GeminiClient

                for mod_name in list(sys.modules):
                    if mod_name == "core" or mod_name.startswith("core."):
                        sys.modules.pop(mod_name, None)

                return importlib.import_module("core.orchestrator")
            raise

    def _register_utils_package(self, repo_root: Path) -> None:
        utils_dir = repo_root / "utils"

        utils_pkg = types.ModuleType("utils")
        utils_pkg.__path__ = [str(utils_dir.resolve())] if utils_dir.exists() else []
        if (utils_dir / "__init__.py").exists():
            utils_pkg.__file__ = str((utils_dir / "__init__.py").resolve())
        sys.modules["utils"] = utils_pkg

        model_module = self._preload_utils_module(utils_dir / "model_client.py", "utils.model_client")
        gemini_module = self._preload_utils_module(utils_dir / "gemini_client.py", "utils.gemini_client")

        if model_module is None and gemini_module is None:
            self._install_gemini_client_fallback()
        elif model_module is not None and gemini_module is None:
            sys.modules["utils.gemini_client"] = model_module
        elif gemini_module is not None and model_module is None:
            sys.modules["utils.model_client"] = gemini_module

        resolved_model_module = sys.modules.get("utils.model_client") or sys.modules.get("utils.gemini_client")
        if resolved_model_module is not None and hasattr(resolved_model_module, "GeminiClient"):
            utils_pkg.GeminiClient = resolved_model_module.GeminiClient

        self._preload_utils_module(utils_dir / "logger.py", "utils.logger")
        self._preload_utils_module(utils_dir / "image_utils.py", "utils.image_utils")

    def _preload_utils_module(self, path: Path, module_name: str):
        if not path.exists():
            return None
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(path.resolve()))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                return module
        except Exception as exc:
            sys.modules.pop(module_name, None)
            print(f"[agent_runner] Warning: could not pre-load {module_name}: {exc}")
        return None

    def _install_gemini_client_fallback(self) -> None:
        existing = sys.modules.get("utils.model_client") or sys.modules.get("utils.gemini_client")
        if existing is not None and hasattr(existing, "GeminiClient"):
            sys.modules["utils.model_client"] = existing
            sys.modules["utils.gemini_client"] = existing
            return
        sys.modules.pop("utils.model_client", None)
        sys.modules.pop("utils.gemini_client", None)

        if "utils" not in sys.modules:
            parent = types.ModuleType("utils")
            parent.__path__ = []
            sys.modules["utils"] = parent

        try:
            from openai import OpenAI
        except Exception:
            OpenAI = None  # type: ignore

        module = types.ModuleType("utils.model_client")

        class GeminiClient:  # pylint: disable=too-few-public-methods
            def __init__(self, api_key: str | None = None, model: str | None = None):
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
                response_mime_type: str | None = None,
                response_schema=None,
            ) -> str:
                del response_schema
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

        module.GeminiClient = GeminiClient
        sys.modules["utils.model_client"] = module
        sys.modules["utils.gemini_client"] = module
        sys.modules["utils"].GeminiClient = module.GeminiClient

    def _resolve_agent_repo_root(self) -> Path:
        current_repo_root = Path(__file__).resolve().parents[2]
        local_repo_valid = self._is_valid_repo_root(current_repo_root)

        if self.repo_path:
            configured = Path(self.repo_path)
            resolved = configured if configured.is_absolute() else (current_repo_root / configured)
            if self._is_valid_repo_root(resolved):
                if local_repo_valid and resolved.resolve() != current_repo_root.resolve():
                    return current_repo_root
                return resolved
            return current_repo_root

        if local_repo_valid:
            return current_repo_root
        return current_repo_root

    def _is_valid_repo_root(self, root: Path) -> bool:
        return root.exists() and (root / "core" / "orchestrator.py").exists()

    async def _safe_emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            result = self.event_sink(event_type, payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass
