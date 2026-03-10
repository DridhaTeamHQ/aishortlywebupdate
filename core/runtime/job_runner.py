import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict

from core.orchestrator import HardenedOrchestrator


@dataclass
class RunResult:
    status: str
    error: str = ""


class AgentJobRunner:
    def __init__(self, cancel_check: Callable[[], bool], event_sink: Callable[[str, Dict[str, Any]], Any]):
        self.cancel_check = cancel_check
        self.event_sink = event_sink

    async def run(self) -> RunResult:
        orchestrator = HardenedOrchestrator()
        # Note: settings.scheduler_enabled defaults to False (env: SCHEDULER_ENABLED)
        # so the orchestrator already runs in single-shot mode
        orchestrator.set_runtime_controls(cancel_check=self.cancel_check, event_sink=self.event_sink)

        try:
            await orchestrator.run()
            if self.cancel_check():
                return RunResult(status="cancelled")
            return RunResult(status="succeeded")
        except Exception as exc:
            await self._safe_emit("ERROR", {"message": str(exc)})
            return RunResult(status="failed", error=str(exc))

    async def _safe_emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            result = self.event_sink(event_type, payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass
