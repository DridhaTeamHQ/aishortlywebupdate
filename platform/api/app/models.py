from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field

RunStatus = Literal["queued", "running", "stopping", "succeeded", "failed", "cancelled"]
RoleName = Literal["admin", "operator", "viewer"]


class AgentRow(BaseModel):
    id: str
    org_id: str
    slug: str
    display_name: str
    enabled: bool = True
    health_status: str = "unknown"


class CreateRunResponse(BaseModel):
    run_id: str
    status: RunStatus


class StopRunResponse(BaseModel):
    run_id: str
    status: RunStatus


class RunView(BaseModel):
    id: str
    agent_id: str
    status: RunStatus
    current_step: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    stream_url: Optional[str] = None


class CreateScheduleRequest(BaseModel):
    cron: str = Field(min_length=9, max_length=120)
    timezone: str = Field(default="Asia/Kolkata", min_length=2, max_length=64)
    enabled: bool = True


class CreateScheduleResponse(BaseModel):
    schedule_id: str


class SaveSecretsRequest(BaseModel):
    cms_url: str
    cms_email: str
    cms_password: str
    openai_api_key: str


class EventPayload(BaseModel):
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
