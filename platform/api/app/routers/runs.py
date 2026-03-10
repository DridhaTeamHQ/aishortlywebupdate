from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import AuthContext, get_auth_context, require_operator_or_admin
from ..models import RunView, StopRunResponse
from ..repositories import Repository

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("/{run_id}", response_model=RunView)
def get_run(run_id: str, ctx: AuthContext = Depends(get_auth_context), repo: Repository = Depends(Repository)):
    row = repo.get_run(run_id, ctx.org_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return row


@router.post("/{run_id}/stop", response_model=StopRunResponse)
def stop_run(run_id: str, ctx: AuthContext = Depends(require_operator_or_admin), repo: Repository = Depends(Repository)):
    row = repo.mark_run_stopping(run_id, ctx.org_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Run not stoppable")
    return StopRunResponse(run_id=run_id, status="stopping")
