from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import AuthContext, get_auth_context, require_operator_or_admin
from ..models import AgentRow, CreateRunResponse, CreateScheduleRequest
from ..queue import get_queue
from ..repositories import Repository

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("", response_model=list[AgentRow])
def list_agents(ctx: AuthContext = Depends(get_auth_context), repo: Repository = Depends(Repository)):
    return repo.list_agents(ctx.org_id)


@router.post("/{agent_id}/runs", response_model=CreateRunResponse)
def create_run(agent_id: str, ctx: AuthContext = Depends(require_operator_or_admin), repo: Repository = Depends(Repository)):
    row = repo.create_run(ctx.org_id, agent_id, ctx.user_id)

    try:
        q = get_queue()
        q.enqueue("worker_tasks.execute_agent_run", row["id"], agent_id, ctx.user_id)
    except Exception:
        # Local development can run with the polling worker instead of Redis/RQ.
        pass

    return CreateRunResponse(run_id=row["id"], status=row["status"])


@router.post("/{agent_id}/schedules")
def create_schedule(
    agent_id: str,
    payload: CreateScheduleRequest,
    ctx: AuthContext = Depends(require_operator_or_admin),
    repo: Repository = Depends(Repository),
):
    cron = payload.cron.strip()
    timezone = payload.timezone.strip()
    enabled = payload.enabled

    if not cron:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="cron is required")

    row = repo.create_schedule(ctx.org_id, agent_id, ctx.user_id, cron, timezone, enabled)
    return {"schedule_id": row["id"]}


@router.delete("/{agent_id}/schedules/{schedule_id}")
def delete_schedule(
    agent_id: str,
    schedule_id: str,
    ctx: AuthContext = Depends(require_operator_or_admin),
    repo: Repository = Depends(Repository),
):
    _ = agent_id
    ok = repo.delete_schedule(schedule_id, ctx.org_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return {"deleted": True}


