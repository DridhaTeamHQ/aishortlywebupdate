import asyncio
import importlib
import importlib.util
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
        return importlib.import_module("core.orchestrator")

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
        """
        utils_dir = repo_root / "utils"
        if not utils_dir.exists():
            return

        utils_pkg = types.ModuleType("utils")
        utils_pkg.__path__ = [str(utils_dir.resolve())]
        sys.modules["utils"] = utils_pkg

        # Pre-load gemini_client so downstream imports find it immediately
        gemini_path = utils_dir / "gemini_client.py"
        if gemini_path.exists():
            try:
                spec = importlib.util.spec_from_file_location("utils.gemini_client", str(gemini_path))
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    sys.modules["utils.gemini_client"] = module
                    # Also set on the bare utils package so `from utils import GeminiClient` works
                    if hasattr(module, "GeminiClient"):
                        utils_pkg.GeminiClient = module.GeminiClient
            except Exception as exc:
                print(f"[agent_runner] Warning: could not pre-load utils/gemini_client.py: {exc}")


    async def _safe_emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            result = self.event_sink(event_type, payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass
