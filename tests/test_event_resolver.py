import json
import os
import unittest

from core.pipeline.event_resolver import EventResolver
from core.pipeline.models import IngestedArticle


class EventResolverTests(unittest.TestCase):
    def test_clusters_duplicate_story(self):
        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "duplicate_stories.json")
        with open(fixture_path, "r", encoding="utf-8-sig") as f:
            rows = json.load(f)

        articles = [IngestedArticle(**row) for row in rows]
        resolver = EventResolver(title_similarity=0.4, content_similarity=0.1, time_window_minutes=90)
        clusters = resolver.cluster(articles)

        self.assertEqual(len(clusters), 2)
        sizes = sorted([len(c.articles) for c in clusters], reverse=True)
        self.assertEqual(sizes, [3, 1])

    def test_story_key_is_stable_for_same_story_variants(self):
        resolver = EventResolver(title_similarity=0.4, content_similarity=0.1, time_window_minutes=180)

        a1 = IngestedArticle(
            category="tech",
            source="Reuters",
            source_url="https://www.reuters.com/technology/",
            url="https://www.reuters.com/tech/apple-1",
            title="Tim Cook to step down as Apple CEO, John Ternus named successor",
            body="Apple said Tim Cook will step down as CEO and John Ternus will take over the top role.",
            published_time="2026-04-21T05:00:00Z",
        )
        a2 = IngestedArticle(
            category="tech",
            source="TOI",
            source_url="https://timesofindia.indiatimes.com/technology",
            url="https://timesofindia.indiatimes.com/technology/apple-2/articleshow/123.cms",
            title="Apple names John Ternus as next CEO as Tim Cook prepares to step down",
            body="John Ternus is set to become Apple's next chief executive after Tim Cook steps down, the company said.",
            published_time="2026-04-21T05:10:00Z",
        )

        key_one = resolver._cluster_story_key([a1])
        key_two = resolver._cluster_story_key([a2])

        self.assertEqual(key_one, key_two)


if __name__ == "__main__":
    unittest.main()
