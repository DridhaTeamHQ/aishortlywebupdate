from unittest.mock import patch

from fastapi.testclient import TestClient

from app.deps import AuthContext, get_auth_context, require_operator_or_admin
from app.main import app
from app.repositories import Repository


class FakeRepo:
    def list_agents(self, org_id):
        return []

    def create_run(self, org_id, agent_id, created_by):
        return {"id": "run-123", "status": "queued"}


class FakeQueue:
    def enqueue(self, *args, **kwargs):
        return None


def _ctx():
    return AuthContext(user_id="user-1", org_id="org-1", role="operator")


def test_create_run_contract():
    app.dependency_overrides[get_auth_context] = _ctx
    app.dependency_overrides[require_operator_or_admin] = _ctx
    app.dependency_overrides[Repository] = lambda: FakeRepo()

    with patch("app.routers.agents.get_queue", return_value=FakeQueue()):
        client = TestClient(app)
        res = client.post("/api/agents/agent-1/runs", headers={"Authorization": "Bearer token"})

    app.dependency_overrides.clear()

    assert res.status_code == 200
    assert res.json()["run_id"] == "run-123"
    assert res.json()["status"] == "queued"
