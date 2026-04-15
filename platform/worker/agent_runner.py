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
        if repo_root_str not in sys.path:
            sys.path.insert(0, repo_root_str)

        # Clear conflicting local modules so imports come from the real agent repo.
        for module_name in list(sys.modules):
            if module_name == "core" or module_name.startswith(("core.", "config.", "utils.")):
                sys.modules.pop(module_name, None)

        self._prepare_repo_utils_imports(repo_root)
        try:
            return importlib.import_module("core.orchestrator")
        except (ImportError, ModuleNotFoundError) as exc:
            # Some environments resolve a third-party `utils` package first.
            # If that happens, force-install a compatibility `utils.gemini_client` shim and retry once.
            err_msg = str(exc)
            if "utils.gemini_client" in err_msg or "GeminiClient" in err_msg or "utils" in err_msg:
                self._install_gemini_client_fallback()
                # Also patch the bare utils module so `from utils import GeminiClient` works
                if "utils" in sys.modules and "utils.gemini_client" in sys.modules:
                    sys.modules["utils"].GeminiClient = sys.modules["utils.gemini_client"].GeminiClient
                # Clear partially-loaded modules before retry
                for mod_name in list(sys.modules):
                    if mod_name == "core" or mod_name.startswith("core."):
                        sys.modules.pop(mod_name, None)
                return importlib.import_module("core.orchestrator")
            raise

    def _resolve_agent_repo_root(self) -> Path:
        current_repo_root = Path(__file__).resolve().parents[2]
        local_repo_valid = self._is_valid_repo_root(current_repo_root)

        if self.repo_path:
            configured = Path(self.repo_path)
            resolved = configured if configured.is_absolute() else (current_repo_root / configured)
            if self._is_valid_repo_root(resolved):
                # Prefer local project code by default so missing/stale submodules never break runtime.
                if local_repo_valid and resolved.resolve() != current_repo_root.resolve():
                    return current_repo_root
                return resolved
            return current_repo_root

        if local_repo_valid:
            return current_repo_root
        return current_repo_root

    def _is_valid_repo_root(self, root: Path) -> bool:
        return root.exists() and (root / "core" / "orchestrator.py").exists()

    def _prepare_repo_utils_imports(self, repo_root: Path) -> None:
        """
        Ensure `utils.*` imports resolve against the selected agent repo.
        This keeps compatibility with repos that don't define utils/__init__.py.
        """
        utils_dir = repo_root / "utils"
        if not utils_dir.exists():
            return

        utils_pkg = types.ModuleType("utils")
        utils_pkg.__path__ = [str(utils_dir.resolve())]
        sys.modules["utils"] = utils_pkg

        gemini_path = utils_dir / "gemini_client.py"
        if gemini_path.exists():
            try:
                spec = importlib.util.spec_from_file_location("utils.gemini_client", str(gemini_path))
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    sys.modules["utils.gemini_client"] = module
            except Exception as exc:
                print(f"[agent_runner] Failed to load utils/gemini_client.py: {exc}")
                print("[agent_runner] Installing fallback GeminiClient shim...")
                self._install_gemini_client_fallback()

        # Ensure the module is always registered even if gemini_client.py doesn't exist
        if "utils.gemini_client" not in sys.modules:
            self._install_gemini_client_fallback()

        # Patch the bare utils module with GeminiClient so `from utils import GeminiClient` works
        if "utils.gemini_client" in sys.modules:
            gc_mod = sys.modules["utils.gemini_client"]
            if hasattr(gc_mod, "GeminiClient"):
                sys.modules["utils"].GeminiClient = gc_mod.GeminiClient

    def _install_gemini_client_fallback(self) -> None:
        if "utils.gemini_client" in sys.modules:
            return

        try:
            from openai import OpenAI
        except Exception:
            OpenAI = None  # type: ignore

        module = types.ModuleType("utils.gemini_client")

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
        sys.modules["utils.gemini_client"] = module

    async def _safe_emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            result = self.event_sink(event_type, payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass
