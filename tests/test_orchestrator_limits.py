import unittest
from types import SimpleNamespace

from core.orchestrator import HardenedOrchestrator


class OrchestratorLimitTests(unittest.TestCase):
    def test_max_articles_per_run_never_drops_below_publish_plan_total(self):
        orchestrator = object.__new__(HardenedOrchestrator)
        orchestrator.settings = SimpleNamespace(max_articles=5)
        orchestrator.publish_plan = [
            {"category": "international", "total": 5, "breaking_target": 3},
            {"category": "national", "total": 5, "breaking_target": 3},
            {"category": "business", "total": 5, "breaking_target": 3},
        ]

        self.assertEqual(HardenedOrchestrator._planned_articles_per_run(orchestrator), 15)
        self.assertEqual(HardenedOrchestrator._max_articles_per_run(orchestrator), 15)

    def test_single_active_category_uses_remaining_slots(self):
        orchestrator = object.__new__(HardenedOrchestrator)
        orchestrator.settings = SimpleNamespace(max_articles=250)
        orchestrator.publish_plan = []

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
        orchestrator.publish_plan = []

        total, breaking = HardenedOrchestrator._effective_publish_targets(
            orchestrator,
            total_target=5,
            breaking_target=3,
            active_category_count=4,
            remaining_slots=2,
        )

        self.assertEqual(total, 2)
        self.assertEqual(breaking, 2)

    def test_build_image_ref_prefers_url(self):
        orchestrator = object.__new__(HardenedOrchestrator)
        self.assertEqual(
            HardenedOrchestrator._build_image_ref(orchestrator, None, "https://example.com/image.jpg"),
            "https://example.com/image.jpg",
        )


if __name__ == "__main__":
    unittest.main()
