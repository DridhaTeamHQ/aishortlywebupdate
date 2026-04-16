import importlib.util
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


def _load_agent_runner_module():
    runner_path = Path(__file__).resolve().parents[1] / "platform" / "worker" / "agent_runner.py"
    spec = importlib.util.spec_from_file_location("shortly_agent_runner_test", str(runner_path))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class AgentRunnerImportTests(unittest.TestCase):
    def setUp(self):
        self.runner_module = _load_agent_runner_module()
        self._original_modules = {
            name: sys.modules[name]
            for name in list(sys.modules)
            if name in {"core", "config", "utils"} or name.startswith(("core.", "config.", "utils."))
        }
        for name in list(sys.modules):
            if name in {"core", "config", "utils"} or name.startswith(("core.", "config.", "utils.")):
                sys.modules.pop(name, None)

    def tearDown(self):
        for name in list(sys.modules):
            if name in {"core", "config", "utils"} or name.startswith(("core.", "config.", "utils.")):
                sys.modules.pop(name, None)
        sys.modules.update(self._original_modules)
        os.environ.pop("RAILWAY_PROJECT_ID", None)

    def test_loader_recovers_when_repo_gemini_client_is_broken(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "core").mkdir()
            (root / "utils").mkdir()

            (root / "core" / "__init__.py").write_text("", encoding="utf-8")
            (root / "utils" / "__init__.py").write_text("", encoding="utf-8")
            (root / "utils" / "gemini_client.py").write_text(
                "raise RuntimeError('broken gemini client import')\n",
                encoding="utf-8",
            )
            (root / "core" / "orchestrator.py").write_text(
                textwrap.dedent(
                    """
                    from utils.gemini_client import GeminiClient

                    class HardenedOrchestrator:
                        def __init__(self):
                            self.client = GeminiClient()
                    """
                ),
                encoding="utf-8",
            )

            runner = self.runner_module.AgentJobRunner(
                cancel_check=lambda: False,
                event_sink=lambda *_args, **_kwargs: None,
                repo_path=str(root),
            )

            orchestrator_module = runner._load_orchestrator_module()
            self.assertTrue(hasattr(orchestrator_module, "HardenedOrchestrator"))
            self.assertIn("utils.gemini_client", sys.modules)
            self.assertTrue(hasattr(sys.modules["utils.gemini_client"], "GeminiClient"))

    def test_fallback_replaces_half_initialized_gemini_module(self):
        broken_module = type(sys)("utils.gemini_client")
        sys.modules["utils.gemini_client"] = broken_module
        sys.modules["utils"] = type(sys)("utils")
        sys.modules["utils"].__path__ = []

        runner = self.runner_module.AgentJobRunner(
            cancel_check=lambda: False,
            event_sink=lambda *_args, **_kwargs: None,
            repo_path=".",
        )

        runner._install_gemini_client_fallback()

        repaired = sys.modules["utils.gemini_client"]
        self.assertIsNot(repaired, broken_module)
        self.assertTrue(hasattr(repaired, "GeminiClient"))
        self.assertTrue(hasattr(sys.modules["utils"], "GeminiClient"))

    def test_fallback_populates_model_and_legacy_aliases(self):
        sys.modules["utils"] = type(sys)("utils")
        sys.modules["utils"].__path__ = []

        runner = self.runner_module.AgentJobRunner(
            cancel_check=lambda: False,
            event_sink=lambda *_args, **_kwargs: None,
            repo_path=".",
        )

        runner._install_gemini_client_fallback()

        self.assertIn("utils.model_client", sys.modules)
        self.assertIn("utils.gemini_client", sys.modules)
        self.assertIs(sys.modules["utils.model_client"], sys.modules["utils.gemini_client"])
        self.assertTrue(hasattr(sys.modules["utils.model_client"], "GeminiClient"))

    def test_hosted_runner_ignores_legacy_external_repo_path(self):
        os.environ["RAILWAY_PROJECT_ID"] = "proj_123"
        runner = self.runner_module.AgentJobRunner(
            cancel_check=lambda: False,
            event_sink=lambda *_args, **_kwargs: None,
            repo_path="external/ai-agent-browser",
        )

        resolved = runner._resolve_agent_repo_root()
        expected = Path(__file__).resolve().parents[1]
        self.assertEqual(resolved.resolve(), expected.resolve())


if __name__ == "__main__":
    unittest.main()
