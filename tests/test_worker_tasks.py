import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import worker_tasks


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, client, name):
        self.client = client
        self.name = name
        self._select = None
        self._insert = None
        self._update = None

    def select(self, fields):
        self._select = fields
        return self

    def eq(self, _field, _value):
        return self

    def limit(self, _count):
        return self

    def insert(self, payload):
        self._insert = payload
        return self

    def update(self, payload):
        self._update = payload
        return self

    def execute(self):
        if self.name == "agent_runs" and self._select == "org_id":
            return _FakeResult([{"org_id": "org-1"}])
        if self.name == "agent_runs" and self._select == "status":
            return _FakeResult([{"status": self.client.current_status}])
        if self.name == "user_secrets":
            return _FakeResult([])
        if self.name == "agent_runs" and self._update is not None:
            self.client.run_updates.append(self._update)
            if "status" in self._update:
                self.client.current_status = self._update["status"]
            return _FakeResult([self._update])
        if self.name == "agent_run_events" and self._insert is not None:
            self.client.events.append(self._insert)
            return _FakeResult([self._insert])
        return _FakeResult([])


class _FakeSupabase:
    def __init__(self):
        self.current_status = "queued"
        self.run_updates = []
        self.events = []

    def table(self, name):
        return _FakeTable(self, name)


class _SuccessRunner:
    def __init__(self, cancel_check, event_sink):
        self.cancel_check = cancel_check
        self.event_sink = event_sink

    async def run(self):
        self.event_sink("STEP_STARTED", {"step": "summarize", "url": "https://example.com/story"})
        self.event_sink("STEP_DONE", {"step": "summarize", "url": "https://example.com/story", "ok": True})
        return SimpleNamespace(status="succeeded", error="")


class _CancelledRunner:
    def __init__(self, cancel_check, event_sink):
        self.cancel_check = cancel_check
        self.event_sink = event_sink

    async def run(self):
        self.event_sink("STEP_STARTED", {"step": "image", "url": "https://example.com/story"})
        return SimpleNamespace(status="succeeded", error="")


class WorkerTaskTests(unittest.TestCase):
    def setUp(self):
        self._env = {
            "SUPABASE_URL": "https://supabase.test",
            "SUPABASE_SERVICE_ROLE_KEY": "service-key",
            "BROWSER_STREAM_BASE_URL": "https://browser-stream.test",
            "PLATFORM_ENCRYPTION_KEY": "",
        }
        self._patcher = patch.dict(os.environ, self._env, clear=False)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_execute_agent_run_emits_stream_and_final_events(self):
        fake_client = _FakeSupabase()

        with patch("worker_tasks.create_client", return_value=fake_client), patch(
            "worker_tasks.AgentJobRunner", _SuccessRunner
        ):
            worker_tasks.execute_agent_run("run-1", "agent-1", "user-1")

        event_types = [row["event_type"] for row in fake_client.events]
        self.assertEqual(event_types[0], "STREAM_READY")
        self.assertIn("STEP_STARTED", event_types)
        self.assertIn("STEP_DONE", event_types)
        self.assertEqual(event_types[-1], "RUN_FINISHED")
        self.assertEqual(fake_client.run_updates[0]["status"], "running")
        self.assertEqual(fake_client.run_updates[-1]["status"], "succeeded")

    def test_execute_agent_run_marks_cancelled_when_status_flips_to_stopping(self):
        fake_client = _FakeSupabase()

        with patch("worker_tasks.create_client", return_value=fake_client), patch(
            "worker_tasks.AgentJobRunner", _CancelledRunner
        ):
            fake_client.current_status = "stopping"
            worker_tasks.execute_agent_run("run-2", "agent-2", "user-2")

        self.assertEqual(fake_client.run_updates[-1]["status"], "cancelled")
        self.assertEqual(fake_client.events[-1]["payload"]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
