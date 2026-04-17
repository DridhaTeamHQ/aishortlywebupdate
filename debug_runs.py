"""Quick debug: check recent runs and verify local worker claims."""
import os
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

runs = (
    sb.table("agent_runs")
    .select("id, status, started_at, finished_at, current_step")
    .order("created_at", desc=True)
    .limit(5)
    .execute()
)

print("=== Recent Runs ===")
for r in runs.data:
    rid = r["id"][:8]
    status = r.get("status", "?")
    step = r.get("current_step", "")
    start = r.get("started_at", "")
    end = r.get("finished_at", "")
    print(f"  {rid} | {status:10s} | step={step} | start={start}")

# Check events for the latest failed run
failed = [r for r in runs.data if r["status"] == "failed"]
if failed:
    run_id = failed[0]["id"]
    events = (
        sb.table("agent_run_events")
        .select("event_type, payload, created_at")
        .eq("run_id", run_id)
        .order("created_at", desc=True)
        .limit(5)
        .execute()
    )
    print(f"\n=== Events for failed run {run_id[:8]} ===")
    for e in events.data:
        print(f"  {e['event_type']} | {e.get('created_at','')} | {e.get('payload','')}")
