import unittest

from core.intelligence.category import CategoryDecider


class CategoryDeciderTests(unittest.TestCase):
    def setUp(self):
        self.decider = CategoryDecider()
        # Keep tests deterministic even when API key exists in environment.
        self.decider.client = None

    def test_uk_transport_is_not_national(self):
        category = self.decider.decide(
            title="Easter travel hit: London-Milton Keynes trains halted",
            body="Mainline train services between London Euston and Milton Keynes are suspended in England.",
            source="Guardian",
            pipeline_hint="international",
        )
        self.assertEqual(category, "International")

    def test_wildlife_restoration_prefers_environment(self):
        category = self.decider.decide(
            title="Wildlife Trusts restore Norfolk land for ecology",
            body="Conservation groups are transforming Norfolk farmland into woodland habitat to boost biodiversity.",
            source="Guardian",
            pipeline_hint="environment",
        )
        self.assertEqual(category, "Environment")

    def test_ai_policy_story_prefers_technology(self):
        category = self.decider.decide(
            title="UK peers demand AI halt to protect creative rights",
            body="Lawmakers debate regulation for AI model training and copyright protections for artists.",
            source="Guardian",
            pipeline_hint="tech",
        )
        self.assertEqual(category, "Technology")


    def test_toi_dubai_charity_is_international(self):
        """TOI source should NOT force National for non-India articles."""
        category = self.decider.decide(
            title="Dubai charity auction nets AED 91.4M for kids",
            body="Dubai's Most Noble Number auction has successfully netted AED 91.4 million, fueling a global campaign against childhood hunger.",
            source="TOI",
            pipeline_hint="business",
        )
        self.assertNotEqual(category, "National")

    def test_toi_qatar_airways_is_not_national(self):
        """Qatar Airways article from TOI should not be National."""
        category = self.decider.decide(
            title="Qatar Airways cuts flights, travelers face delays",
            body="Qatar Airways is cutting flights from March 9 to March 11 due to an airspace closure, impacting international travel plans.",
            source="TOI",
            pipeline_hint="business",
        )
        self.assertNotEqual(category, "National")

    def test_toi_india_article_stays_national(self):
        """TOI article about India should still be National."""
        category = self.decider.decide(
            title="India GDP growth hits 7.2% in Q3, surpasses estimates",
            body="India's economy grew at 7.2% in the third quarter, beating analyst expectations and bolstering Modi's economic agenda.",
            source="TOI",
            pipeline_hint="national",
        )
        self.assertIn(category, {"National", "Business"})


if __name__ == "__main__":
    unittest.main()
