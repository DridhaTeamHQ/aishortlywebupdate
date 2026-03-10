"""Tests for body truncation and dangling-tail detection."""

import os
import unittest

from core.intelligence.summarize import Summarizer


class DanglingTailTests(unittest.TestCase):
    def setUp(self):
        self.summarizer = Summarizer()

    def test_detects_trailing_preposition(self):
        self.assertTrue(self.summarizer._has_dangling_tail("The government responded to."))

    def test_detects_trailing_possessive(self):
        self.assertTrue(self.summarizer._has_dangling_tail("The initiative is set to expand its."))

    def test_detects_trailing_article(self):
        self.assertTrue(self.summarizer._has_dangling_tail("Passengers should monitor updates for the."))

    def test_detects_trailing_conjunction(self):
        self.assertTrue(self.summarizer._has_dangling_tail("Officials warned residents and."))

    def test_detects_trailing_auxiliary(self):
        self.assertTrue(self.summarizer._has_dangling_tail("The company said it will."))

    def test_detects_preposition_article_combo(self):
        self.assertTrue(self.summarizer._has_dangling_tail("They are looking for the."))

    def test_complete_sentence_not_dangling(self):
        self.assertFalse(self.summarizer._has_dangling_tail("The economy grew at 7.2% in Q3."))

    def test_complete_sentence_with_number(self):
        self.assertFalse(self.summarizer._has_dangling_tail("Over 100 flights were cancelled across India."))


class TrimBodyTests(unittest.TestCase):
    def setUp(self):
        self.summarizer = Summarizer()

    def test_trim_ends_on_sentence_boundary(self):
        body = "First sentence here. Second sentence follows. Third sentence is longer and exceeds the limit set for display."
        result = self.summarizer._trim_body_to_band(body, max_chars=50, target_chars=15)
        self.assertTrue(
            result.endswith(".") or result.endswith("!") or result.endswith("?"),
            f"Expected sentence-ending punctuation, got: {result!r}",
        )

    def test_trim_keeps_complete_sentences(self):
        body = "Economy grew 7% last quarter. Markets rallied on the news. Analysts predict continued growth ahead."
        result = self.summarizer._trim_body_to_band(body, max_chars=70, target_chars=25)
        self.assertTrue(result.endswith("."), f"Expected period, got: {result!r}")
        # Should not contain partial next sentence
        self.assertNotIn("Analysts predict continued", result)


if __name__ == "__main__":
    unittest.main()
