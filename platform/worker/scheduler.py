import os
import time
from datetime import datetime, timezone

from croniter import croniter
from redis import Redis
from rq import Queue
from supabase import create_client


def _now() -> datetime:
    return datetime.now(timezone.utc)


def main() -> None:
    sb = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))
    q = Queue(os.getenv("RQ_QUEUE_NAME", "agent-runs"), connection=Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0")))

    while True:
        now_iso = _now().isoformat()
        due = (
            sb.table("agent_schedules")
            .select("id,org_id,agent_id,created_by,cron,timezone,next_run_at")
            .eq("enabled", True)
            .lte("next_run_at", now_iso)
            .execute()
        )

        for row in due.data or []:
            run = (
                sb.table("agent_runs")
                .insert(
                    {
                        "org_id": row["org_id"],
                        "agent_id": row["agent_id"],
                        "created_by": row["created_by"],
                        "status": "queued",
                        "current_step": "queued",
                    }
                )
                .execute()
            )
            run_id = (run.data or [])[0]["id"]
            q.enqueue("worker_tasks.execute_agent_run", run_id, row["agent_id"], row["created_by"])

            itr = croniter(row["cron"], _now())
            nxt = itr.get_next(datetime).astimezone(timezone.utc).isoformat()
            sb.table("agent_schedules").update({"next_run_at": nxt}).eq("id", row["id"]).execute()

        time.sleep(30)


if __name__ == "__main__":
    main()
