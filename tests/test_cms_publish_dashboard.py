import asyncio
import tempfile
import unittest

from core.cms.publish import ArticleData, CMSPublisher


class _FakeLocator:
    def __init__(self):
        self.focused = False
        self.script = ""
        self.value = ""

    async def focus(self):
        self.focused = True

    async def evaluate(self, script, value=None):
        self.script = script
        if value is not None:
            self.value = value
        return None

    async def input_value(self):
        return self.value


class _FakePage:
    def __init__(self):
        self.handlers = {}

    def on(self, event_name, handler):
        self.handlers[event_name] = handler


class CMSPublisherDashboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_fill_react_input_uses_js_injection_strategy(self):
        publisher = CMSPublisher()
        locator = _FakeLocator()

        ok = await publisher._fill_react_input(locator, "Injected title")

        self.assertTrue(ok)
        self.assertTrue(locator.focused)
        self.assertIn("nativeSetter.call", locator.script)
        self.assertIn("dispatchEvent(new Event('input'", locator.script)
        self.assertEqual(locator.value, "Injected title")

    async def test_attach_page_debug_listeners_registers_console_and_pageerror(self):
        publisher = CMSPublisher()
        publisher.page = _FakePage()

        publisher._attach_page_debug_listeners()

        self.assertIn("console", publisher.page.handlers)
        self.assertIn("pageerror", publisher.page.handlers)

    async def test_fill_form_submits_articledata_into_cms_fields(self):
        publisher = CMSPublisher()
        calls = []

        async def _record_fill(locator, value):
            calls.append((locator, value))
            return True

        async def _return_true(*_args, **_kwargs):
            return True

        publisher.ensure_live_page = _return_true
        publisher._is_article_form_open = _return_true
        publisher._find_english_title_field = lambda: asyncio.sleep(0, result="title-locator")
        publisher._find_english_body_field = lambda: asyncio.sleep(0, result="body-locator")
        publisher._fill_react_input = _record_fill
        publisher._scroll_form_to_section = _return_true
        publisher._select_category = _return_true
        publisher._fill_keywords = _return_true
        publisher._upload_image = _return_true
        publisher.image_finder = None

        with tempfile.NamedTemporaryFile(suffix=".jpg") as handle:
            data = ArticleData(
                english_title="Title from dashboard",
                english_body="Body from dashboard",
                category="National",
                hashtag="#news #dashboard",
                image_path=handle.name,
            )

            ok = await publisher.fill_form(data)

        self.assertTrue(ok)
        self.assertEqual(calls, [("title-locator", "Title from dashboard"), ("body-locator", "Body from dashboard")])


if __name__ == "__main__":
    unittest.main()
