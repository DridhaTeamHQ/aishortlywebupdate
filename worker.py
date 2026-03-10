"""
Shortly AI Worker — polls Supabase for queued runs and executes locally.

Usage:  python worker.py
"""

import asyncio
import logging
import os
import sys
import time
import signal
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# ── Suppress noisy httpx logs ───────────────────
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from dotenv import load_dotenv

load_dotenv()

# ── Required env vars ────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
POLL_INTERVAL = int(os.getenv("WORKER_POLL_INTERVAL", "5"))

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")
    sys.exit(1)

from supabase import create_client

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# ── Graceful shutdown ────────────────────────────
_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    print("\n🛑 Shutdown signal received, finishing current job...")
    _shutdown = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════
#  DATABASE HELPERS
# ═══════════════════════════════════════════════════

def _claim_run() -> Optional[Dict[str, Any]]:
    """Find and atomically claim the oldest queued run."""
    try:
        result = sb.table("agent_runs") \
            .select("id, agent_id, created_by") \
            .eq("status", "queued") \
            .order("created_at", desc=False) \
            .limit(1) \
            .execute()
    except Exception as exc:
        print(f"  ⚠️  Poll error: {exc}")
        return None

    rows = result.data or []
    if not rows:
        return None

    run = rows[0]
    run_short = run["id"][:8]
    print(f"  📥 Found queued run {run_short}... claiming")

    try:
        # Update the run to 'running' — conditional on still being 'queued'
        sb.table("agent_runs") \
            .update({"status": "running", "started_at": _now_iso(), "current_step": "starting"}) \
            .eq("id", run["id"]) \
            .eq("status", "queued") \
            .execute()

        # Verify we actually claimed it by re-reading the status
        verify = sb.table("agent_runs") \
            .select("status") \
            .eq("id", run["id"]) \
            .limit(1) \
            .execute()
        actual_status = (verify.data or [{}])[0].get("status", "")

        if actual_status != "running":
            print(f"  ⚠️  Run {run_short} already claimed by another worker (status={actual_status})")
            return None
    except Exception as exc:
        print(f"  ❌ Claim update failed: {exc}")
        return None

    print(f"  ✅ Claimed run {run_short}")
    return run


def _fetch_status(run_id: str) -> str:
    try:
        res = sb.table("agent_runs").select("status").eq("id", run_id).limit(1).execute()
        rows = res.data or []
        return rows[0]["status"] if rows else "failed"
    except Exception:
        return "running"  # assume still running on error


def _update_run(run_id: str, values: Dict[str, Any]) -> None:
    try:
        sb.table("agent_runs").update(values).eq("id", run_id).execute()
    except Exception as exc:
        print(f"  ⚠️  Update run failed: {exc}")


def _emit_event(run_id: str, agent_id: str, user_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    try:
        sb.table("agent_run_events").insert({
            "run_id": run_id,
            "agent_id": agent_id,
            "event_type": event_type,
            "payload": payload,
            "created_by": user_id,
        }).execute()
    except Exception as exc:
        print(f"  ⚠️  Event emit failed: {exc}")

    if event_type in {"STEP_STARTED", "STEP_DONE"}:
        step = str(payload.get("step", "")).strip()
        if step:
            _update_run(run_id, {"current_step": step})


# ═══════════════════════════════════════════════════
#  RUN EXECUTION
# ═══════════════════════════════════════════════════

def _execute_run(run: Dict[str, Any]) -> None:
    """Execute a claimed run using the orchestrator."""
    run_id = run["id"]
    agent_id = run["agent_id"]
    user_id = run["created_by"]
    run_short = run_id[:8]

    print(f"\n  🔄 Starting run {run_short}...")
    print(f"     Agent: {agent_id[:8]}")
    print(f"     User:  {user_id[:8]}")

    def cancel_check() -> bool:
        return _fetch_status(run_id) == "stopping"

    def event_sink(event_type: str, payload: Dict[str, Any]) -> None:
        _emit_event(run_id, agent_id, user_id, event_type, payload)

        # Terminal output for local visibility
        step = payload.get("step", "")
        if event_type == "STEP_STARTED" and step:
            print(f"    ▶ {step}")
        elif event_type == "STEP_DONE" and step:
            ok = payload.get("ok", True)
            extras = []
            if payload.get("categories"):
                extras.append(f"{payload['categories']} cats")
            if payload.get("clusters"):
                extras.append(f"{payload['clusters']} clusters")
            if payload.get("breaking") is not None:
                extras.append(f"{payload['breaking']} breaking")
            detail = f" ({', '.join(extras)})" if extras else ""
            print(f"    {'✅' if ok else '⚠️'} {step}{detail}")
        elif event_type == "ERROR":
            print(f"    ❌ {payload.get('message', 'error')}")
        elif event_type == "LOG":
            msg = payload.get("message", "")
            if msg:
                print(f"    📋 {msg}")

    # ── Import and run the agent ──
    try:
        from core.runtime import AgentJobRunner

        runner = AgentJobRunner(cancel_check=cancel_check, event_sink=event_sink)
        result = asyncio.run(runner.run())
        final_status = result.status
    except Exception as exc:
        tb = traceback.format_exc()
        print(f"    💥 Agent crashed: {exc}")
        print(f"    {tb}")
        # Emit error event
        _emit_event(run_id, agent_id, user_id, "ERROR", {"message": str(exc)})
        final_status = "failed"

    # Check if stop was requested during execution
    if _fetch_status(run_id) == "stopping":
        final_status = "cancelled"

    _update_run(run_id, {
        "status": final_status,
        "finished_at": _now_iso(),
        "current_step": "finished",
    })
    _emit_event(run_id, agent_id, user_id, "RUN_FINISHED", {
        "status": final_status,
        "error": getattr(locals().get("result"), "error", "") or "",
    })

    icon = "🎉" if final_status == "succeeded" else "🛑" if final_status == "cancelled" else "💥"
    print(f"\n  {icon} Run {run_short} finished: {final_status}")
    print(f"  {'─' * 40}")


# ═══════════════════════════════════════════════════
#  MAIN LOOP
# ═══════════════════════════════════════════════════

def main() -> None:
    print()
    print("  ═══════════════════════════════════════════")
    print("  ⚡ Shortly AI Worker")
    print(f"  📡 Supabase: {SUPABASE_URL}")
    print(f"  ⏱️  Poll interval: {POLL_INTERVAL}s")
    print("  🛑 Press Ctrl+C to stop")
    print("  ═══════════════════════════════════════════")
    print()

    # Quick connectivity test
    try:
        test = sb.table("agents").select("id").limit(1).execute()
        agents = test.data or []
        print(f"  ✅ Connected — {len(agents)} agent(s) found")
    except Exception as exc:
        print(f"  ❌ Connection test failed: {exc}")
        sys.exit(1)

    print(f"  👀 Waiting for jobs...\n")

    while not _shutdown:
        try:
            run = _claim_run()
            if run:
                _execute_run(run)
                print(f"  👀 Waiting for jobs...\n")
            else:
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            print(f"  ❌ Worker error: {exc}")
            traceback.print_exc()
            time.sleep(POLL_INTERVAL)

    print("\n  👋 Worker stopped.")


if __name__ == "__main__":
    main()
