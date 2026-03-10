from fastapi.testclient import TestClient

from app.deps import AuthContext, require_operator_or_admin
from app.main import app
from app.repositories import Repository


class FakeRepo:
    def mark_run_stopping(self, run_id, org_id):
        return {"id": run_id, "status": "stopping"}


def _ctx():
    return AuthContext(user_id="user-1", org_id="org-1", role="operator")


def test_stop_run_contract():
    app.dependency_overrides[require_operator_or_admin] = _ctx
    app.dependency_overrides[Repository] = lambda: FakeRepo()

    client = TestClient(app)
    res = client.post("/api/runs/run-7/stop", headers={"Authorization": "Bearer token"})

    app.dependency_overrides.clear()

    assert res.status_code == 200
    assert res.json()["run_id"] == "run-7"
    assert res.json()["status"] == "stopping"
