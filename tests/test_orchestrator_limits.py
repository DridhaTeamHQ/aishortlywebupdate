import unittest
from types import SimpleNamespace

from core.orchestrator import HardenedOrchestrator


class OrchestratorLimitTests(unittest.TestCase):
    def test_single_active_category_uses_remaining_slots(self):
        orchestrator = object.__new__(HardenedOrchestrator)
        orchestrator.settings = SimpleNamespace(max_articles=250)

        total, breaking = HardenedOrchestrator._effective_publish_targets(
            orchestrator,
            total_target=5,
            breaking_target=3,
            active_category_count=1,
            remaining_slots=23,
        )

        self.assertEqual(total, 23)
        self.assertEqual(breaking, 3)

    def test_multi_category_respects_remaining_slots(self):
        orchestrator = object.__new__(HardenedOrchestrator)
        orchestrator.settings = SimpleNamespace(max_articles=250)

        total, breaking = HardenedOrchestrator._effective_publish_targets(
            orchestrator,
            total_target=5,
            breaking_target=3,
            active_category_count=4,
            remaining_slots=2,
        )

        self.assertEqual(total, 2)
        self.assertEqual(breaking, 2)


if __name__ == "__main__":
    unittest.main()
