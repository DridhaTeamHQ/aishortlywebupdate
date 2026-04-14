import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

from cryptography.fernet import Fernet
from supabase import create_client

from platform.worker.agent_runner import AgentJobRunner


def _settings() -> Dict[str, str]:
    return {
        "SUPABASE_URL": os.getenv("SUPABASE_URL", "").strip(),
        "SUPABASE_SERVICE_ROLE_KEY": os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
        "BROWSER_STREAM_BASE_URL": os.getenv("BROWSER_STREAM_BASE_URL", "http://localhost:8090").strip(),
        "PLATFORM_ENCRYPTION_KEY": os.getenv("PLATFORM_ENCRYPTION_KEY", "").strip(),
        "AI_AGENT_REPO_PATH": os.getenv("AI_AGENT_REPO_PATH", "").strip(),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_user_secrets(sb, user_id: str, org_id: str, enc_key: str) -> Dict[str, str]:
    if not enc_key:
        return {}
    res = sb.table("user_secrets").select("encrypted_payload").eq("user_id", user_id).eq("org_id", org_id).limit(1).execute()
    rows = res.data or []
    if not rows:
        return {}
    f = Fernet(enc_key)
    raw = f.decrypt(rows[0]["encrypted_payload"].encode("utf-8")).decode("utf-8")
    parsed = json.loads(raw)
    return {str(k): str(v) for k, v in parsed.items()}


def execute_agent_run(run_id: str, agent_id: str, user_id: str) -> None:
    cfg = _settings()
    sb = create_client(cfg["SUPABASE_URL"], cfg["SUPABASE_SERVICE_ROLE_KEY"])

    run_row_res = sb.table("agent_runs").select("org_id").eq("id", run_id).limit(1).execute()
    run_rows = run_row_res.data or []
    org_id = run_rows[0]["org_id"] if run_rows else None

    if org_id:
        user_secrets = _load_user_secrets(sb, user_id, org_id, cfg["PLATFORM_ENCRYPTION_KEY"])
        for k, v in user_secrets.items():
            os.environ[k] = v

    def update_run(values: Dict[str, Any]) -> None:
        sb.table("agent_runs").update(values).eq("id", run_id).execute()

    def fetch_status() -> str:
        res = sb.table("agent_runs").select("status").eq("id", run_id).limit(1).execute()
        rows = res.data or []
        return rows[0]["status"] if rows else "failed"

    def emit(event_type: str, payload: Dict[str, Any]) -> None:
        sb.table("agent_run_events").insert(
            {
                "run_id": run_id,
                "agent_id": agent_id,
                "event_type": event_type,
                "payload": payload,
                "created_by": user_id,
            }
        ).execute()

        if event_type in {"STEP_STARTED", "STEP_DONE"}:
            step = str(payload.get("step", "")).strip()
            if step:
                update_run({"current_step": step})

    update_run(
        {
            "status": "running",
            "started_at": _now_iso(),
            "current_step": "starting",
            "stream_url": f"{cfg['BROWSER_STREAM_BASE_URL']}/sessions/{run_id}",
        }
    )
    emit("STREAM_READY", {"stream_url": f"{cfg['BROWSER_STREAM_BASE_URL']}/sessions/{run_id}"})

    def cancel_check() -> bool:
        return fetch_status() == "stopping"

    runner = AgentJobRunner(
        cancel_check=cancel_check,
        event_sink=emit,
        repo_path=cfg["AI_AGENT_REPO_PATH"] or None,
    )
    result = asyncio.run(runner.run())

    final_status = result.status
    if fetch_status() == "stopping":
        final_status = "cancelled"

    update_run({"status": final_status, "finished_at": _now_iso(), "current_step": "finished"})
    emit("RUN_FINISHED", {"status": final_status, "error": result.error})
