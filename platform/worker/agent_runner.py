"""
Agent job runner — loads the orchestrator from the local repo and executes it.

Import strategy:
  1. Resolve the agent repo root (directory containing core/orchestrator.py).
  2. Ensure repo root is first on sys.path.
  3. Flush ALL cached core/config/utils modules from sys.modules.
  4. Explicitly register the `utils` package pointing at <repo>/utils/ so that
     `from utils.gemini_client import GeminiClient` works even in environments
     (like Railway) where Python's default path search would fail.
  5. Import core.orchestrator cleanly.
"""

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

        # ── 1. Ensure repo root is FIRST on sys.path ───────────────────
        # Remove any existing entries so we don't get stale duplicates,
        # then insert at position 0 so our local packages win.
        sys.path = [p for p in sys.path if p != repo_root_str]
        sys.path.insert(0, repo_root_str)

        # ── 2. Flush every cached module that could conflict ───────────
        for mod_name in list(sys.modules):
            if (
                mod_name in ("core", "config", "utils")
                or mod_name.startswith(("core.", "config.", "utils."))
            ):
                sys.modules.pop(mod_name, None)

        # ── 3. Explicitly register the utils package ───────────────────
        # This is essential on Railway and similar platforms where Python's
        # default import machinery can't discover our local utils/ dir.
        self._register_utils_package(repo_root)

        # ── 4. Import the orchestrator ─────────────────────────────────
        return importlib.import_module("core.orchestrator")

    def _register_utils_package(self, repo_root: Path) -> None:
        """Register the local utils/ directory as a proper Python package."""
        utils_dir = repo_root / "utils"
        if not utils_dir.exists():
            return

        # Create and register the utils package module
        utils_pkg = types.ModuleType("utils")
        utils_pkg.__path__ = [str(utils_dir.resolve())]
        utils_pkg.__file__ = str((utils_dir / "__init__.py").resolve())
        sys.modules["utils"] = utils_pkg

        # Pre-load utils.gemini_client so downstream imports find it
        gemini_path = utils_dir / "gemini_client.py"
        if gemini_path.exists():
            try:
                spec = importlib.util.spec_from_file_location(
                    "utils.gemini_client", str(gemini_path.resolve())
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    sys.modules["utils.gemini_client"] = module
                    if hasattr(module, "GeminiClient"):
                        utils_pkg.GeminiClient = module.GeminiClient
            except Exception as exc:
                print(f"[agent_runner] Warning: could not pre-load gemini_client: {exc}")

        # Pre-load utils.logger
        logger_path = utils_dir / "logger.py"
        if logger_path.exists():
            try:
                spec = importlib.util.spec_from_file_location(
                    "utils.logger", str(logger_path.resolve())
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    sys.modules["utils.logger"] = module
            except Exception as exc:
                print(f"[agent_runner] Warning: could not pre-load logger: {exc}")

        # Pre-load utils.image_utils
        image_utils_path = utils_dir / "image_utils.py"
        if image_utils_path.exists():
            try:
                spec = importlib.util.spec_from_file_location(
                    "utils.image_utils", str(image_utils_path.resolve())
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    sys.modules["utils.image_utils"] = module
            except Exception as exc:
                print(f"[agent_runner] Warning: could not pre-load image_utils: {exc}")

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
