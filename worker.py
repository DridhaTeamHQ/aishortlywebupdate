"""
Shortly AI local worker.

Behavior:
- polls Supabase for queued runs
- claims one run at a time
- executes the claimed run in a child process
- kills the child process when the run status becomes "stopping"
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from supabase import create_client

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
POLL_INTERVAL = int(os.getenv("WORKER_POLL_INTERVAL", "5"))

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")
    sys.exit(1)

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
_shutdown = False


def _handle_signal(sig, frame):
    del sig, frame
    global _shutdown
    print("\nShutdown signal received, finishing current job...")
    _shutdown = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_agent_job_runner():
    runner_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "platform",
        "worker",
        "agent_runner.py",
    )
    spec = importlib.util.spec_from_file_location("shortly_agent_runner", runner_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load agent runner from {runner_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.AgentJobRunner


def _claim_run() -> Optional[Dict[str, Any]]:
    try:
        result = (
            sb.table("agent_runs")
            .select("id, agent_id, created_by")
            .eq("status", "queued")
            .order("created_at", desc=False)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        print(f"Poll error: {exc}")
        return None

    rows = result.data or []
    if not rows:
        return None

    run = rows[0]
    run_short = run["id"][:8]
    print(f"Found queued run {run_short}... claiming")

    try:
        (
            sb.table("agent_runs")
            .update({"status": "running", "started_at": _now_iso(), "current_step": "starting"})
            .eq("id", run["id"])
            .eq("status", "queued")
            .execute()
        )
        verify = sb.table("agent_runs").select("status").eq("id", run["id"]).limit(1).execute()
        actual_status = (verify.data or [{}])[0].get("status", "")
        if actual_status != "running":
            print(f"Run {run_short} already claimed by another worker (status={actual_status})")
            return None
    except Exception as exc:
        print(f"Claim update failed: {exc}")
        return None

    print(f"Claimed run {run_short}")
    return run


def _fetch_status(run_id: str) -> str:
    try:
        res = sb.table("agent_runs").select("status").eq("id", run_id).limit(1).execute()
        rows = res.data or []
        return rows[0]["status"] if rows else "failed"
    except Exception:
        return "running"


def _update_run(run_id: str, values: Dict[str, Any]) -> None:
    try:
        sb.table("agent_runs").update(values).eq("id", run_id).execute()
    except Exception as exc:
        print(f"Update run failed: {exc}")


def _emit_event(run_id: str, agent_id: str, user_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    try:
        sb.table("agent_run_events").insert(
            {
                "run_id": run_id,
                "agent_id": agent_id,
                "event_type": event_type,
                "payload": payload,
                "created_by": user_id,
            }
        ).execute()
    except Exception as exc:
        print(f"Event emit failed: {exc}")

    if event_type in {"STEP_STARTED", "STEP_DONE"}:
        step = str(payload.get("step", "")).strip()
        if step:
            _update_run(run_id, {"current_step": step})


def _finalize_if_needed(run_id: str, agent_id: str, user_id: str, final_status: str, error: str = "") -> None:
    current_status = _fetch_status(run_id)
    if current_status in {"succeeded", "failed", "cancelled"}:
        return

    _update_run(
        run_id,
        {
            "status": final_status,
            "finished_at": _now_iso(),
            "current_step": "cancelled" if final_status == "cancelled" else "finished",
        },
    )
    _emit_event(
        run_id,
        agent_id,
        user_id,
        "RUN_FINISHED",
        {"status": final_status, "error": error},
    )


def _execute_run_inline(run: Dict[str, Any]) -> None:
    run_id = run["id"]
    agent_id = run["agent_id"]
    user_id = run["created_by"]
    run_short = run_id[:8]

    print(f"\nStarting run {run_short}...")
    print(f"  Agent: {agent_id[:8]}")
    print(f"  User:  {user_id[:8]}")

    def cancel_check() -> bool:
        return _fetch_status(run_id) == "stopping"

    def event_sink(event_type: str, payload: Dict[str, Any]) -> None:
        _emit_event(run_id, agent_id, user_id, event_type, payload)
        step = payload.get("step", "")
        if event_type == "STEP_STARTED" and step:
            print(f"  -> {step}")
        elif event_type == "STEP_DONE" and step:
            ok = payload.get("ok", True)
            print(f"  {'OK' if ok else 'FAIL'} {step}")
        elif event_type == "ERROR":
            print(f"  ERROR {payload.get('message', 'error')}")
        elif event_type == "LOG":
            msg = payload.get("message", "")
            if msg:
                print(f"  LOG {msg}")

    try:
        AgentJobRunner = _load_agent_job_runner()
        repo_path = os.getenv("AI_AGENT_REPO_PATH", ".")
        runner = AgentJobRunner(repo_path=repo_path, cancel_check=cancel_check, event_sink=event_sink)
        result = asyncio.run(runner.run())
        final_status = result.status
        error = result.error
    except Exception as exc:
        tb = traceback.format_exc()
        print(f"Agent crashed: {exc}")
        print(tb)
        _emit_event(run_id, agent_id, user_id, "ERROR", {"message": str(exc)})
        final_status = "failed"
        error = str(exc)

    if _fetch_status(run_id) == "stopping":
        final_status = "cancelled"

    _update_run(
        run_id,
        {
            "status": final_status,
            "finished_at": _now_iso(),
            "current_step": "finished" if final_status != "cancelled" else "cancelled",
        },
    )
    _emit_event(
        run_id,
        agent_id,
        user_id,
        "RUN_FINISHED",
        {"status": final_status, "error": error or ""},
    )

    print(f"\nRun {run_short} finished: {final_status}")
    print("-" * 40)


def _execute_run_supervised(run: Dict[str, Any]) -> None:
    run_id = run["id"]
    agent_id = run["agent_id"]
    user_id = run["created_by"]
    run_short = run_id[:8]

    proc = subprocess.Popen(
        [sys.executable, __file__, "--run-claimed", run_id, agent_id, user_id],
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )

    while proc.poll() is None:
        status = _fetch_status(run_id)
        if _shutdown or status == "stopping":
            print(f"Stop requested for run {run_short}; terminating active job...")
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            except Exception as exc:
                print(f"Failed to terminate child process cleanly: {exc}")

            _finalize_if_needed(run_id, agent_id, user_id, "cancelled")
            print(f"\nRun {run_short} cancelled")
            print("-" * 40)
            return

        time.sleep(1)

    current_status = _fetch_status(run_id)
    if current_status in {"succeeded", "failed", "cancelled"}:
        return

    if proc.returncode == 0:
        _finalize_if_needed(run_id, agent_id, user_id, "succeeded")
    else:
        _finalize_if_needed(run_id, agent_id, user_id, "failed", error=f"worker_child_exit_{proc.returncode}")


def main() -> None:
    print()
    print("=" * 42)
    print("Shortly AI Worker")
    print(f"Supabase: {SUPABASE_URL}")
    print(f"Poll interval: {POLL_INTERVAL}s")
    print("Press Ctrl+C to stop")
    print("=" * 42)
    print()

    try:
        test = sb.table("agents").select("id").limit(1).execute()
        agents = test.data or []
        print(f"Connected - {len(agents)} agent(s) found")
    except Exception as exc:
        print(f"Connection test failed: {exc}")
        sys.exit(1)

    print("Waiting for jobs...\n")

    while not _shutdown:
        try:
            run = _claim_run()
            if run:
                _execute_run_supervised(run)
                print("Waiting for jobs...\n")
            else:
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            print(f"Worker error: {exc}")
            traceback.print_exc()
            time.sleep(POLL_INTERVAL)

    print("\nWorker stopped.")


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--run-claimed":
        _execute_run_inline(
            {
                "id": sys.argv[2],
                "agent_id": sys.argv[3],
                "created_by": sys.argv[4],
            }
        )
        sys.exit(0)
    main()
