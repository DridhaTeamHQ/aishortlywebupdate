import json
from typing import Any, Dict, List, Optional

from .config import get_settings
from .security import build_fernet_from_app_key, decrypt_json, encrypt_json
from .supabase_client import get_supabase_admin


class Repository:
    def __init__(self):
        self.sb = get_supabase_admin()
        settings = get_settings()
        self.fernet = build_fernet_from_app_key(settings.encryption_key)

    def list_agents(self, org_id: str) -> List[Dict[str, Any]]:
        res = self.sb.table("agents").select("id,org_id,slug,display_name,enabled,health_status").eq("org_id", org_id).order("display_name").execute()
        return res.data or []

    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        res = self.sb.table("profiles").select("id,org_id,role").eq("id", user_id).limit(1).execute()
        rows = res.data or []
        return rows[0] if rows else None

    def create_run(self, org_id: str, agent_id: str, created_by: str) -> Dict[str, Any]:
        payload = {
            "org_id": org_id,
            "agent_id": agent_id,
            "created_by": created_by,
            "status": "queued",
            "current_step": "queued",
        }
        res = self.sb.table("agent_runs").insert(payload).execute()
        return (res.data or [])[0]

    def get_run(self, run_id: str, org_id: str) -> Optional[Dict[str, Any]]:
        res = self.sb.table("agent_runs").select("id,agent_id,status,current_step,started_at,finished_at,stream_url").eq("id", run_id).eq("org_id", org_id).limit(1).execute()
        rows = res.data or []
        return rows[0] if rows else None

    def mark_run_stopping(self, run_id: str, org_id: str) -> Optional[Dict[str, Any]]:
        res = self.sb.table("agent_runs").update({"status": "stopping"}).eq("id", run_id).eq("org_id", org_id).in_("status", ["queued", "running"]).execute()
        rows = res.data or []
        return rows[0] if rows else None

    def create_schedule(self, org_id: str, agent_id: str, created_by: str, cron: str, timezone: str, enabled: bool) -> Dict[str, Any]:
        payload = {
            "org_id": org_id,
            "agent_id": agent_id,
            "created_by": created_by,
            "cron": cron,
            "timezone": timezone,
            "enabled": enabled,
        }
        res = self.sb.table("agent_schedules").insert(payload).execute()
        return (res.data or [])[0]

    def delete_schedule(self, schedule_id: str, org_id: str) -> bool:
        res = self.sb.table("agent_schedules").delete().eq("id", schedule_id).eq("org_id", org_id).execute()
        return bool(res.data)

    def save_user_secrets(self, user_id: str, org_id: str, secrets: Dict[str, str]) -> Dict[str, Any]:
        encrypted = encrypt_json(self.fernet, json.dumps(secrets))
        payload = {
            "user_id": user_id,
            "org_id": org_id,
            "encrypted_payload": encrypted,
        }
        res = self.sb.table("user_secrets").upsert(payload, on_conflict="user_id").execute()
        return (res.data or [])[0]

    def get_user_secrets(self, user_id: str, org_id: str) -> Optional[Dict[str, str]]:
        res = self.sb.table("user_secrets").select("encrypted_payload").eq("user_id", user_id).eq("org_id", org_id).limit(1).execute()
        rows = res.data or []
        if not rows:
            return None
        raw = decrypt_json(self.fernet, rows[0]["encrypted_payload"])
        return json.loads(raw)
