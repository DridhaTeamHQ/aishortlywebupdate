import asyncio
import importlib
import sys
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

        return importlib.import_module("core.orchestrator")

    def _resolve_agent_repo_root(self) -> Path:
        if self.repo_path:
            return Path(self.repo_path)

        current_repo_root = Path(__file__).resolve().parents[2]
        external_repo_root = current_repo_root / "external" / "ai-agent-browser"
        if external_repo_root.exists():
            return external_repo_root
        return current_repo_root

    async def _safe_emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            result = self.event_sink(event_type, payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass
