"""CMS publisher with state-aware navigation and resilient selectors."""

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
import httpx

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from config.settings import get_settings
from core.cms.image_finder import GoogleImageFinder
from utils.image_utils import meets_minimum_resolution
from utils.logger import get_logger


@dataclass
class ArticleData:
    english_title: str
    english_body: str
    category: str
    telugu_title: str = ""
    telugu_body: str = ""
    image_search_query: str = ""
    image_alt: str = ""
    hashtag: str = "#news #trending"
    image_path: Optional[str] = None
    needs_image: bool = False
    image_url: Optional[str] = None
    image_metadata: Dict[str, Any] = field(default_factory=dict)


class CMSPublisher:
    # Live CMS categories: Assembly Elections, Technology, Lifestyle, State,
    # International, National, Entertainment, Finance, Sports.
    # Aliases provide graceful fallbacks if a legacy label slips through.
    CATEGORY_ALIASES = {
        "Technology": ["Tech"],
        "Tech": ["Technology"],
        "Finance": ["Business"],
        "Business": ["Finance"],
        "Assembly Elections": ["Politics", "National"],
        "Politics": ["Assembly Elections", "National"],
        "National": ["State"],
        "State": ["National"],
        "Lifestyle": ["Health", "Entertainment"],
        "Entertainment": ["Lifestyle"],
        "Environment": ["Lifestyle", "National"],
    }

    SCREENSHOT_DIR = Path("screenshots")
    DEBUG_DIR = Path("screenshots/debug")
    TIMEOUT = 30000

    def __init__(self):
        self.settings = get_settings()
        self.logger = get_logger("cms")
        self.cms_url = os.getenv("CMS_URL", "")
        self.cms_email = os.getenv("CMS_EMAIL", "")
        self.cms_password = os.getenv("CMS_PASSWORD", "")
        self.cms_role = os.getenv("CMS_ROLE", "Content Writer")

        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.image_finder: Optional[GoogleImageFinder] = None
        self.logged_in = False
        self.last_error = ""

        self.SCREENSHOT_DIR.mkdir(exist_ok=True)
        self.DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    def _fail(self, reason: str) -> bool:
        """Record a precise failure reason (surfaced in Live Activity) and return False."""
        self.last_error = reason
        self.logger.error(f"publish.fail reason={reason}")
        return False

    def _launch_kwargs(self) -> Dict[str, Any]:
        headless = self.settings.headless
        slow_mo = max(0, int(self.settings.slow_mo))
        return {"headless": headless, "slow_mo": slow_mo}

    async def start(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(**self._launch_kwargs())
        self.context = await self.browser.new_context(viewport={"width": 1400, "height": 900})
        self.page = await self.context.new_page()
        self._attach_page_debug_listeners()
        self.image_finder = GoogleImageFinder(self.page)
        self.page.set_default_timeout(self.TIMEOUT)

    async def stop(self):
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception as exc:
            self.logger.warning(f"Error stopping browser: {exc}")

    async def ensure_live_page(self) -> bool:
        try:
            if self.playwright is None:
                self.playwright = await async_playwright().start()
            if self.browser is None or not self.browser.is_connected():
                self.browser = await self.playwright.chromium.launch(**self._launch_kwargs())

            recreate_context = self.context is None or self.page is None or self.page.is_closed()
            if recreate_context:
                try:
                    if self.context:
                        await self.context.close()
                except Exception:
                    pass
                self.context = await self.browser.new_context(viewport={"width": 1400, "height": 900})
                self.page = await self.context.new_page()
                self._attach_page_debug_listeners()
                self.page.set_default_timeout(self.TIMEOUT)
                self.image_finder = GoogleImageFinder(self.page)
                self.logged_in = False
            return True
        except Exception as exc:
            self.logger.error(f"Browser recovery failed: {exc}")
            return False

    def _attach_page_debug_listeners(self) -> None:
        if self.page is None:
            return

        self.page.on(
            "console",
            lambda msg: self.logger.info(f"Playwright console[{msg.type}]: {msg.text}")
        )
        self.page.on(
            "pageerror",
            lambda exc: self.logger.error(f"Playwright pageerror: {exc}")
        )
    async def _wait_stable(self):
        if self.page is None:
            return
        try:
            await self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await asyncio.sleep(0.8)

    async def _dump_debug(self, name: str):
        if self.page is None:
            return
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
        png = self.DEBUG_DIR / f"{safe}.png"
        html = self.DEBUG_DIR / f"{safe}.html"
        try:
            await self.page.screenshot(path=str(png), full_page=True)
            content = await self.page.content()
            html.write_text(content, encoding="utf-8")
            self.logger.warning(f"Debug captured: {png} and {html}")
        except Exception as exc:
            self.logger.warning(f"Debug capture failed ({name}): {exc}")

    async def _is_authenticated_view(self) -> bool:
        if self.page is None:
            return False

        if "/login" not in self.page.url.lower():
            return True

        signals = [
            self.page.locator("text='Dashboard'").first,
            self.page.locator("text='Content'").first,
            self.page.locator("text='Report a Bug'").first,
            self.page.locator("text='Welcome back'").first,
        ]
        count = 0
        for loc in signals:
            try:
                if await loc.is_visible(timeout=1500):
                    count += 1
            except Exception:
                pass
        return count >= 2

    async def _is_article_form_open(self) -> bool:
        if self.page is None:
            return False

        checks = [
            self.page.get_by_text("Create New Article").first,
            self.page.get_by_text("English Title").first,
            self.page.get_by_text("English Content").first,
            self.page.get_by_text("Keywords").first,
            self.page.get_by_text("Media Type").first,
            self.page.locator("#title_en").first,
            self.page.locator("#content_en").first,
            self.page.locator("input[placeholder*='English title' i]").first,
            self.page.locator("textarea[placeholder*='English content' i]").first,
            self.page.locator("input[type='file']").first,
        ]
        for loc in checks:
            try:
                if await loc.count() > 0 and await loc.is_visible(timeout=900):
                    return True
            except Exception:
                pass
        return False

    async def _is_articles_page(self) -> bool:
        if self.page is None:
            return False

        if await self._is_article_form_open():
            return False

        url = self.page.url.lower()
        if "/articles" in url and "create" not in url and "new" not in url:
            return True

        # Strong, single-signal markers from the redesigned newsroom dashboard.
        strong = [
            self.page.get_by_role("button", name=re.compile(r"create article", re.I)).first,
            self.page.locator("text='Article Management'").first,
            self.page.locator("text='Articles Management'").first,
            self.page.locator("text='Editorial Command Center'").first,
        ]
        for loc in strong:
            try:
                if await loc.count() > 0 and await loc.is_visible(timeout=700):
                    return True
            except Exception:
                pass

        # Weak markers — require two together.
        weak = [
            self.page.locator("text='All Articles'").first,
            self.page.locator("text='All Status'").first,
            self.page.locator("table").first,
            self.page.locator("text='Advanced Filters'").first,
        ]
        visible_count = 0
        for loc in weak:
            try:
                if await loc.count() > 0 and await loc.is_visible(timeout=600):
                    visible_count += 1
            except Exception:
                pass
        return visible_count >= 2

    async def _find_first_visible(self, selectors, timeout_ms: int = 1200):
        if self.page is None:
            return None

        for selector in selectors:
            try:
                loc = self.page.locator(selector).first
                if await loc.count() > 0 and await loc.is_visible(timeout=timeout_ms):
                    return loc
            except Exception:
                continue
        return None

    async def _click_first(self, selectors, name: str, timeout_ms: int = 1200) -> bool:
        loc = await self._find_first_visible(selectors, timeout_ms=timeout_ms)
        if loc is None:
            return False
        try:
            await loc.scroll_into_view_if_needed()
        except Exception:
            pass
        try:
            await loc.click(force=True)
            await asyncio.sleep(0.5)
            return True
        except Exception as exc:
            self.logger.warning(f"Failed clicking {name}: {exc}")
            return False

    async def _click_locator(self, locator, name: str) -> bool:
        if locator is None:
            return False
        try:
            if await locator.count() == 0:
                return False
            await locator.scroll_into_view_if_needed()
        except Exception:
            pass
        try:
            await locator.click(force=True)
            await asyncio.sleep(0.5)
            return True
        except Exception as exc:
            self.logger.warning(f"Failed clicking {name}: {exc}")
            return False

    async def _scroll_locator_into_view(self, locator) -> bool:
        if locator is None:
            return False
        try:
            if await locator.count() == 0:
                return False
            await locator.scroll_into_view_if_needed()
            await asyncio.sleep(0.25)
            return True
        except Exception:
            return False

    async def _scroll_modal_by(self, delta: int = 700) -> bool:
        if self.page is None:
            return False

        candidates = [
            self.page.locator("[role='dialog']").first,
            self.page.locator(
                "xpath=(//*[contains(@class,'overflow-y-auto') or contains(@class,'overflow-auto')][.//*[contains(normalize-space(), 'English Title')]])[1]"
            ).first,
            self.page.locator(
                "xpath=(//*[contains(@class,'overflow-y-auto') or contains(@class,'overflow-auto')])[last()]"
            ).first,
        ]

        for candidate in candidates:
            try:
                if await candidate.count() > 0 and await candidate.is_visible(timeout=300):
                    await candidate.evaluate("(el, amount) => { el.scrollTop = el.scrollTop + amount; }", delta)
                    await asyncio.sleep(0.25)
                    return True
            except Exception:
                continue

        try:
            await self.page.mouse.wheel(0, delta)
            await asyncio.sleep(0.25)
            return True
        except Exception:
            return False

    async def _scroll_form_to_section(self, label: str) -> bool:
        if self.page is None:
            return False

        escaped = re.escape(label)
        candidates = [
            self.page.get_by_text(label, exact=True).first,
            self.page.locator(f"xpath=//*[normalize-space()='{label}']").first,
            self.page.locator(f"xpath=//*[contains(normalize-space(), '{label}')]").first,
            self.page.locator(f"text=/{escaped}/i").first,
        ]

        for candidate in candidates:
            if await self._scroll_locator_into_view(candidate):
                return True

        for _ in range(3):
            if await self._scroll_modal_by(500):
                for candidate in candidates:
                    if await self._scroll_locator_into_view(candidate):
                        return True
        return False

    async def _find_clickable_ancestor(self, label: str, scope_selector: str = "aside nav"):
        if self.page is None:
            return None

        scope = self.page.locator(scope_selector).first
        candidates = [
            scope.get_by_text(label, exact=True).first.locator(
                "xpath=ancestor::*[contains(@class,'cursor-pointer')][1]"
            ),
            scope.get_by_text(label, exact=True).first.locator("xpath=ancestor::button[1]"),
            scope.get_by_text(label, exact=True).first.locator("xpath=ancestor::a[1]"),
            scope.get_by_text(label, exact=True).first,
        ]
        for candidate in candidates:
            try:
                if await candidate.count() > 0 and await candidate.is_visible(timeout=1200):
                    return candidate.first
            except Exception:
                continue
        return None

    async def _find_first_visible_in_scope(self, scope, selectors, timeout_ms: int = 1200):
        if scope is None:
            return await self._find_first_visible(selectors, timeout_ms=timeout_ms)

        for selector in selectors:
            try:
                loc = scope.locator(selector).first
                if await loc.count() > 0 and await loc.is_visible(timeout=timeout_ms):
                    return loc
            except Exception:
                continue
        return None

    async def _article_form_scope(self):
        if self.page is None:
            return None

        candidates = [
            self.page.locator("[role='dialog']").filter(has=self.page.get_by_text("Create New Article")).first,
            self.page.locator("[role='dialog']").first,
        ]
        for candidate in candidates:
            try:
                if await candidate.count() > 0 and await candidate.is_visible(timeout=700):
                    return candidate
            except Exception:
                continue
        return self.page

    async def _is_sidebar_item_visible(self, label: str) -> bool:
        if self.page is None:
            return False
        try:
            loc = self.page.locator("aside nav").first.get_by_text(label, exact=True).first
            return await loc.count() > 0 and await loc.is_visible(timeout=900)
        except Exception:
            return False

    async def _open_articles_management(self) -> bool:
        if self.page is None:
            return False

        if await self._is_articles_page():
            return True

        if not await self._is_sidebar_item_visible("Articles"):
            content_item = await self._find_clickable_ancestor("Content")
            if not await self._click_locator(content_item, "Content menu"):
                return False
            await self._wait_stable()

        articles_item = await self._find_clickable_ancestor("Articles")
        if not await self._click_locator(articles_item, "Articles menu"):
            return False

        for _ in range(10):
            await self._wait_stable()
            if await self._is_articles_page():
                return True
            await asyncio.sleep(0.2)
        return False

    async def _open_create_article_modal(self) -> bool:
        if self.page is None:
            return False

        if await self._is_article_form_open():
            return True

        create_candidates = [
            self.page.get_by_role("button", name=re.compile(r"create article", re.I)).first,
            self.page.get_by_role("link", name=re.compile(r"create article", re.I)).first,
            self.page.locator("main").get_by_role("button", name=re.compile(r"create article", re.I)).first,
            self.page.locator("button:has-text('Create Article')").first,
            self.page.locator("a:has-text('Create Article')").first,
            self.page.locator("[data-testid='create-article']").first,
            self.page.locator("text='Create Article'").first.locator(
                "xpath=ancestor-or-self::*[self::button or self::a or @role='button'][1]"
            ),
        ]
        for candidate in create_candidates:
            if await self._click_locator(candidate, "Create Article button"):
                await self._wait_stable()
                if await self._is_article_form_open():
                    return True
        return False

    async def login(self) -> bool:
        if not await self.ensure_live_page():
            return False

        try:
            await self.page.goto(self.cms_url, wait_until="networkidle")
            await self._wait_stable()
            if await self._is_authenticated_view():
                self.logged_in = True
                return True

            dropdown = self.page.locator("button[role='combobox']").first
            await dropdown.click()
            await asyncio.sleep(0.8)
            await self.page.locator(f"[role='option']:has-text('{self.cms_role}')").click()

            await self.page.locator("#email").fill(self.cms_email)
            await self.page.locator("#password").fill(self.cms_password)
            await self.page.locator("button[type='submit']").click()

            await self._wait_stable()
            self.logged_in = await self._is_authenticated_view()
            return self.logged_in
        except Exception as exc:
            self.logger.error(f"Login error: {exc}")
            await self._dump_debug("login_error")
            return False

    async def _open_create_route_from_link(self) -> bool:
        if self.page is None:
            return False
        try:
            href = await self.page.evaluate(
                """() => {
                    const links = [...document.querySelectorAll('a[href]')];
                    const byText = links.find((a) => {
                        const txt = (a.textContent || '').trim();
                        const href = a.getAttribute('href') || '';
                        return /create|new/i.test(txt) && /article/i.test(`${txt} ${href}`);
                    });
                    if (byText) return new URL(byText.getAttribute('href'), window.location.href).toString();

                    const byHref = links.find((a) => {
                        const href = a.getAttribute('href') || '';
                        return /article/i.test(href) && /create|new/i.test(href);
                    });
                    if (byHref) return new URL(byHref.getAttribute('href'), window.location.href).toString();
                    return null;
                }"""
            )
            if href:
                await self.page.goto(href, wait_until="networkidle")
                await self._wait_stable()
                return await self._is_article_form_open()
        except Exception:
            pass
        return False

    async def _open_create_route_direct(self) -> bool:
        if self.page is None:
            return False
        try:
            current = self.page.url or self.cms_url
            from urllib.parse import urlparse

            parsed = urlparse(current)
            base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else self.cms_url.rstrip("/")
            candidates = [
                f"{base}/content/articles/create",
                f"{base}/content/articles/new",
                f"{base}/articles/create",
                f"{base}/articles/new",
            ]
            for target in candidates:
                try:
                    await self.page.goto(target, wait_until="domcontentloaded")
                    await self._wait_stable()
                    if await self._is_article_form_open():
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    async def create_article(self) -> bool:
        if not await self.ensure_live_page():
            return False

        if await self._is_article_form_open():
            return True

        for attempt in range(3):
            await self._wait_stable()

            # The redesigned dashboard exposes "Create Article" globally, so try the
            # modal directly before navigating into Articles Management.
            if await self._open_create_article_modal():
                return True

            if await self._open_articles_management():
                if await self._open_create_article_modal():
                    return True
                if await self._open_create_route_from_link():
                    return True
                if await self._open_create_route_direct():
                    return True

            await self._dump_debug(f"create_article_attempt_{attempt + 1}")
            if self.page and not self.page.is_closed():
                try:
                    await self.page.reload(wait_until="domcontentloaded")
                    await asyncio.sleep(1)
                except Exception as exc:
                    self.logger.warning(f"Create article reload failed: {exc}")
            elif not await self.ensure_live_page():
                return False

        return self._fail("create_article_form_unreachable")

    async def _fill_react_input(self, locator, value: str) -> bool:
        try:
            await locator.focus()
            await locator.evaluate(
                """(input, v) => {
                    const proto = (input instanceof HTMLTextAreaElement)
                        ? window.HTMLTextAreaElement.prototype
                        : window.HTMLInputElement.prototype;
                    const nativeSetter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                    nativeSetter.call(input, v);
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    input.dispatchEvent(new Event('blur', { bubbles: true }));
                }""",
                value,
            )
            await asyncio.sleep(0.3)
            return (await locator.input_value()).strip() == value.strip()
        except Exception:
            return False

    async def _find_english_title_field(self):
        if self.page is None:
            return None
        scope = await self._article_form_scope()
        selectors = [
            "#title_en",
            "input[placeholder*='English title' i]",
            "xpath=.//label[contains(normalize-space(), 'English Title')]/following::input[1]",
            "input[data-testid='rt-input-component']",
        ]
        return await self._find_first_visible_in_scope(scope, selectors, timeout_ms=900)

    async def _find_english_body_field(self):
        if self.page is None:
            return None
        scope = await self._article_form_scope()
        selectors = [
            "#content_en",
            "textarea[placeholder*='English content' i]",
            "xpath=.//label[contains(normalize-space(), 'English Content')]/following::textarea[1]",
            "textarea[data-testid='rt-input-component']",
        ]
        return await self._find_first_visible_in_scope(scope, selectors, timeout_ms=900)

    async def _select_category(self, category: str) -> bool:
        if self.page is None:
            return False
        scope = await self._article_form_scope()

        async def _open_dropdown():
            candidates = [
                scope.locator("xpath=.//label[contains(normalize-space(), 'Category')]/following::button[@role='combobox'][1]").first,
                scope.locator("xpath=.//label[contains(normalize-space(), 'Category')]/following::*[@role='combobox'][1]").first,
                scope.locator("button:has-text('Select category')").first,
                scope.locator("xpath=.//*[contains(normalize-space(), 'Select category')]").first,
                scope.locator("button:has-text('Select')").last,
                scope.locator("[role='combobox']").last,
            ]
            for c in candidates:
                try:
                    if await c.count() > 0 and await c.is_visible(timeout=1200):
                        await c.click(force=True)
                        await asyncio.sleep(0.4)
                        return c
                except Exception:
                    continue
            return None

        async def _find_category_option(name: str):
            escaped = re.escape(name)
            candidates = [
                self.page.get_by_role("option", name=re.compile(rf"^\s*{escaped}\s*$", re.I)).first,
                self.page.locator(
                    f"xpath=(//*[@role='listbox']//*[normalize-space()='{name}'])[1]"
                ).first,
                # Radix/headless popper content (new CMS uses a floating panel).
                self.page.locator(
                    f"xpath=(//*[@data-radix-popper-content-wrapper or @role='menu' or contains(@class,'popover') or contains(@class,'dropdown')]//*[normalize-space()='{name}'])[1]"
                ).first,
                self.page.locator(
                    f"xpath=((//*[contains(@class,'overflow-y-auto') or contains(@class,'overflow-auto')])[last()]//*[normalize-space()='{name}'])[1]"
                ).first,
                # Any visible element whose entire text is exactly the category name.
                self.page.locator(f"xpath=(//*[normalize-space(text())='{name}'])[last()]").first,
                self.page.locator(f"text=/^\\s*{escaped}\\s*$/i").last,
                self.page.locator(f"text=/{escaped}/i").last,
            ]
            for candidate in candidates:
                try:
                    if await candidate.count() > 0 and await candidate.is_visible(timeout=400):
                        return candidate
                except Exception:
                    continue
            return None

        try:
            target = (category or "").strip()
            if not target:
                return False

            desired = [target]
            desired.extend(self.CATEGORY_ALIASES.get(target, []))

            for _attempt in range(3):
                dropdown = await _open_dropdown()
                if dropdown is None:
                    return False

                chosen = None

                for name in desired:
                    exact = await _find_category_option(name)
                    if exact is not None:
                        await self._scroll_locator_into_view(exact)
                        try:
                            await exact.click(force=True)
                            chosen = name
                            break
                        except Exception:
                            continue

                if chosen is None:
                    for name in desired:
                        try:
                            await self.page.keyboard.press("Control+A")
                            await self.page.keyboard.type(name)
                            await asyncio.sleep(0.25)
                            await self.page.keyboard.press("Enter")
                            chosen = name
                            break
                        except Exception:
                            continue

                await asyncio.sleep(0.6)

                # Primary verification: the trigger now shows the chosen label.
                try:
                    selected_text = (await dropdown.inner_text()).strip().lower()
                    if chosen and chosen.lower() in selected_text:
                        self.logger.info(f"Category selected: {chosen}")
                        return True
                except Exception:
                    pass

                # Lenient verification: we clicked an exact option and the option
                # list has since closed — treat as committed selection.
                if chosen:
                    try:
                        still_open = await _find_category_option(chosen)
                        if still_open is None:
                            self.logger.info(f"Category selected (list closed): {chosen}")
                            return True
                    except Exception:
                        pass

            self.logger.error(f"Category verify failed. target={target} selected=unknown")
            return False
        except Exception:
            return False

    async def _upload_image(self, image_path: str) -> bool:
        if self.page is None:
            return False
        scope = await self._article_form_scope()

        try:
            await self._scroll_form_to_section("Media")
            await self._ensure_media_type_image()
            chooser = scope.locator("input[type='file']").first
            if await chooser.count() == 0:
                await self._click_first(
                    [
                        "text='Browse Files'", "button:has-text('Browse Files')",
                        "text='Choose File'", "button:has-text('Choose File')",
                    ],
                    "Browse Files",
                )
                await asyncio.sleep(0.8)
                chooser = scope.locator("input[type='file']").first

            if await chooser.count() == 0:
                self.logger.error("File input not found in CMS form")
                return False

            await chooser.set_input_files(image_path)
            await asyncio.sleep(1.0)

            # The redesigned CMS pops a "Crop Image" dialog the moment the file is
            # accepted, and clears the <input> as it reads it — so input.files.length
            # is unreliable (reads 0 even on success). Confirm via the crop dialog.
            if await self._confirm_image_crop():
                return True

            # No crop dialog appeared — fall back to direct-attach signals.
            try:
                files_count = await chooser.evaluate("el => (el.files && el.files.length) ? el.files.length : 0")
            except Exception:
                files_count = 0
            if isinstance(files_count, int) and files_count >= 1:
                return True
            if await self._image_preview_present():
                return True

            self.logger.error("Image upload did not attach a file (no crop dialog, no preview)")
            await self._dump_debug("image_attach_unconfirmed")
            return False
        except Exception as exc:
            self.logger.error(f"Image upload failed: {exc}")
            await self._dump_debug("image_upload_error")
            return False

    async def _confirm_image_crop(self) -> bool:
        """Wait for the Crop Image dialog and click its Crop button to confirm the upload."""
        if self.page is None:
            return False

        crop_dialog = (
            self.page.locator("[role='dialog']")
            .filter(has=self.page.get_by_text(re.compile(r"crop image", re.I)))
            .first
        )
        crop_button = self.page.get_by_role("button", name=re.compile(r"^\s*crop\s*$", re.I)).first

        appeared = False
        for _ in range(12):
            for signal in (crop_dialog, crop_button):
                try:
                    if await signal.count() > 0 and await signal.is_visible(timeout=300):
                        appeared = True
                        break
                except Exception:
                    pass
            if appeared:
                break
            await asyncio.sleep(0.3)

        if not appeared:
            return False

        crop_candidates = [
            crop_button,
            self.page.locator("[role='dialog'] button:has-text('Crop')").last,
            self.page.locator("button:has-text('Crop')").last,
        ]
        for candidate in crop_candidates:
            try:
                if await candidate.count() == 0 or not await candidate.is_visible(timeout=500):
                    continue
                await candidate.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            try:
                await candidate.click(force=True)
                await asyncio.sleep(1.0)
            except Exception:
                continue
            # Success when the crop dialog dismisses.
            for _ in range(12):
                try:
                    if await crop_dialog.count() == 0 or not await crop_dialog.is_visible(timeout=300):
                        self.logger.info("Image crop confirmed")
                        return True
                except Exception:
                    self.logger.info("Image crop confirmed (dialog gone)")
                    return True
                await asyncio.sleep(0.3)
            # Clicked but dialog lingered; treat as confirmed to avoid a false negative.
            self.logger.warning("Crop clicked but dialog still visible — proceeding")
            return True

        self.logger.error("Crop dialog present but Crop button not clickable")
        await self._dump_debug("crop_button_unclickable")
        return False

    async def _image_preview_present(self) -> bool:
        """Detect an attached-image preview/thumbnail inside the article form."""
        if self.page is None:
            return False
        scope = await self._article_form_scope()
        candidates = [
            scope.locator("img[src^='blob:']").first,
            scope.locator("img[src^='data:']").first,
            scope.get_by_text(re.compile(r"remove image|change image|image selected|uploaded", re.I)).first,
        ]
        for candidate in candidates:
            try:
                if await candidate.count() > 0 and await candidate.is_visible(timeout=400):
                    return True
            except Exception:
                continue
        return False

    async def _ensure_media_type_image(self) -> bool:
        if self.page is None:
            return False
        scope = await self._article_form_scope()

        try:
            await self._scroll_form_to_section("Media")
            trigger = await self._find_first_visible_in_scope(
                scope,
                [
                    "xpath=.//label[contains(normalize-space(), 'Media Type')]/following::*[@role='combobox'][1]",
                    "xpath=.//label[contains(normalize-space(), 'Media Type')]/following::button[1]",
                    "xpath=.//label[contains(normalize-space(), 'Media Type')]/following::div[contains(., 'Image')][1]",
                ],
                timeout_ms=700,
            )
            if trigger is None:
                return False

            try:
                current = (await trigger.inner_text()).strip().lower()
                if "image" in current:
                    return True
            except Exception:
                pass

            await trigger.click(force=True)
            await asyncio.sleep(0.4)
            option = self.page.get_by_role("option", name=re.compile(r"^\s*Image\s*$", re.I)).first
            if await option.count() > 0:
                await option.click(force=True)
                await asyncio.sleep(0.4)
                return True
        except Exception:
            return False
        return False


    async def _download_article_image(self, image_url: str, title: str) -> Optional[str]:
        """Fallback download using the article-selected image URL only."""
        if not image_url:
            return None

        low = image_url.lower().strip()
        # Reject known branded/watermarked URL patterns in fallback path.
        if "ichef.bbci.co.uk/news/" in low and "/branded_news/" in low:
            self.logger.warning("Rejected fallback image URL: BBC branded_news watermark pattern")
            return None
        if "static.files.bbci.co.uk" in low:
            self.logger.warning("Rejected fallback image URL: BBC static branded asset")
            return None
        if low.startswith("data:image") or low.endswith(".svg"):
            return None

        try:
            safe_name = re.sub(r"[^\w\s-]", "", (title or "article_image"))[:40].strip().replace(" ", "_")
            out_path = Path("downloads/images") / f"{safe_name}.jpg"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                resp = await client.get(image_url)
                if resp.status_code != 200:
                    return None

                final_url = str(resp.url).lower()
                if "ichef.bbci.co.uk/news/" in final_url and "/branded_news/" in final_url:
                    self.logger.warning("Rejected fallback image URL after redirect: BBC branded_news watermark pattern")
                    return None
                if "static.files.bbci.co.uk" in final_url:
                    self.logger.warning("Rejected fallback image URL after redirect: BBC static branded asset")
                    return None
                if any(tok in final_url for tok in ("watermark", "channelbug", "branding", "overlay-toi_sw")):
                    self.logger.warning("Rejected fallback image URL after redirect: branded/overlay token detected")
                    return None

                content_type = (resp.headers.get("content-type") or "").lower()
                if content_type and "image" not in content_type:
                    return None

                data = resp.content
                if not meets_minimum_resolution(data):
                    self.logger.warning("Rejected fallback image after download: below minimum resolution")
                    return None

                out_path.write_bytes(data)
                return str(out_path.resolve())
        except Exception:
            return None
    async def _find_keywords_field(self):
        if self.page is None:
            return None
        scope = await self._article_form_scope()

        selectors = [
            "xpath=.//label[contains(normalize-space(), 'Keywords')]/following::input[1]",
            "xpath=.//label[contains(normalize-space(), 'Keywords')]/following::textarea[1]",
            "input[placeholder*='keyword' i]",
        ]
        return await self._find_first_visible_in_scope(scope, selectors, timeout_ms=900)

    async def _fill_keywords(self, hashtag: str) -> bool:
        await self._scroll_form_to_section("Keywords")
        field = await self._find_keywords_field()
        if field is None:
            return False

        try:
            raw_tokens = re.split(r"[\s,]+", (hashtag or "").strip())
            tokens = []
            seen = set()
            for token in raw_tokens:
                clean = token.strip().lstrip("#").strip()
                if not clean:
                    continue
                lowered = clean.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                tokens.append(clean)
                if len(tokens) >= 10:
                    break

            if not tokens:
                tokens = ["news"]

            for token in tokens:
                await self._scroll_locator_into_view(field)
                await field.click(force=True)
                await asyncio.sleep(0.1)
                await field.fill("")
                await asyncio.sleep(0.05)
                await field.type(token, delay=20)
                await asyncio.sleep(0.15)
                await field.press("Enter")
                await asyncio.sleep(0.2)

            self.logger.info(f"Keywords set: {tokens}")
            return True
        except Exception:
            return False

    async def fill_form(self, data: ArticleData) -> bool:
        if not await self.ensure_live_page():
            return self._fail("browser_unavailable")

        try:
            self.logger.info(
                "Submitting ArticleData title_len=%s body_len=%s category=%s image_search=%s",
                len(data.english_title or ""),
                len(data.english_body or ""),
                data.category,
                bool(data.image_search_query),
            )
            if not await self._is_article_form_open():
                return self._fail("form_not_open")

            title_field = await self._find_english_title_field()
            body_field = await self._find_english_body_field()
            if title_field is None:
                return self._fail("title_field_not_found")
            if body_field is None:
                return self._fail("body_field_not_found")

            if not await self._fill_react_input(title_field, data.english_title):
                return self._fail("title_fill_failed")
            if not await self._fill_react_input(body_field, data.english_body):
                return self._fail("body_fill_failed")
            self.logger.info("ArticleData English fields injected into CMS form")

            await self._scroll_form_to_section("Category")
            if not await self._select_category(data.category):
                return self._fail(f"category_select_failed:{data.category}")

            await self._scroll_form_to_section("Keywords")
            if not await self._fill_keywords(data.hashtag):
                self.logger.warning("Keywords fill failed — continuing without keywords")
                # Keywords are optional metadata; a fill failure must NOT abort the publish

            image_path = data.image_path if data.image_path and os.path.exists(data.image_path) else None
            if not image_path and data.image_url:
                self.logger.info("Image path missing, trying article image URL fallback")
                image_path = await self._download_article_image(data.image_url, data.english_title)

            if (
                (not image_path or not os.path.exists(image_path))
                and data.image_search_query
                and self.image_finder is not None
            ):
                self.logger.info(f"Image path missing, trying search fallback: {data.image_search_query}")
                image_path = await self.image_finder.find_and_download(data.image_search_query)

            if not image_path or not os.path.exists(image_path):
                return self._fail("image_missing")

            data.image_path = image_path
            await self._scroll_form_to_section("Media")
            if not await self._upload_image(image_path):
                return self._fail("image_upload_failed")

            self.last_error = ""
            return True
        except Exception as exc:
            self.logger.error(f"Form error: {exc}")
            await self._dump_debug("form_fill_error")
            return self._fail(f"form_exception:{type(exc).__name__}")

    @staticmethod
    def _publish_candidate_rank(meta: Dict[str, Any]) -> int:
        text = re.sub(r"\s+", " ", str(meta.get("text", ""))).strip().lower()
        if not text or not re.search(r"publish|submit|save|review", text):
            return -1

        role = str(meta.get("role", "")).strip().lower()
        control_type = str(meta.get("type", "")).strip().lower()
        aria_has_popup = str(meta.get("aria_has_popup", "")).strip().lower()
        nearby_text = re.sub(r"\s+", " ", str(meta.get("ancestor_text", ""))).strip().lower()

        if meta.get("is_disabled") or meta.get("is_listbox_item"):
            return -1
        if role == "combobox" or aria_has_popup == "listbox":
            return -1

        score = 0
        if "submit for review" in text:
            score += 150
        elif "publish article" in text:
            score += 120
        elif text == "publish":
            score += 50
        elif "publish" in text:
            score += 40
        elif "review" in text:
            score += 35
        elif "submit" in text:
            score += 25
        elif "save" in text:
            score += 10

        if control_type == "submit":
            score += 30
        if meta.get("in_form"):
            score += 15
        if meta.get("within_dialog"):
            score += 10
        if "approval status" in nearby_text and text == "publish":
            score -= 60

        top = float(meta.get("top") or 0)
        left = float(meta.get("left") or 0)
        bottom = float(meta.get("bottom") or 0)
        viewport_height = float(meta.get("viewport_height") or 0)
        viewport_width = float(meta.get("viewport_width") or 0)

        if viewport_height and top > viewport_height * 0.55:
            score += 25
        if viewport_height and bottom > viewport_height * 0.85:
            score += 10
        if viewport_width and left > viewport_width * 0.45:
            score += 10

        return score

    async def _dismiss_transient_overlays(self):
        if self.page is None:
            return

        overlay_selectors = [
            "[role='listbox']",
            "[role='menu']",
            "[data-radix-popper-content-wrapper]",
        ]

        for _ in range(2):
            overlay_visible = False
            for selector in overlay_selectors:
                try:
                    loc = self.page.locator(selector).first
                    if await loc.count() > 0 and await loc.is_visible(timeout=150):
                        overlay_visible = True
                        break
                except Exception:
                    continue

            if not overlay_visible:
                return

            try:
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.25)
            except Exception:
                return

    async def _inspect_publish_candidate(self, locator) -> Optional[Dict[str, Any]]:
        try:
            if await locator.count() == 0 or not await locator.is_visible(timeout=300):
                return None
            meta = await locator.evaluate(
                """(el) => {
                    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const rect = el.getBoundingClientRect();
                    const ancestorText = [];
                    let node = el.parentElement;
                    for (let depth = 0; depth < 2 && node; depth += 1, node = node.parentElement) {
                        ancestorText.push(normalize(node.innerText || '').slice(0, 140));
                    }
                    return {
                        text: normalize(el.innerText || el.textContent || el.value || ''),
                        role: normalize(el.getAttribute('role') || ''),
                        type: normalize(el.getAttribute('type') || ''),
                        aria_has_popup: normalize(el.getAttribute('aria-haspopup') || ''),
                        ancestor_text: ancestorText.join(' '),
                        within_dialog: !!el.closest('[role="dialog"]'),
                        in_form: !!el.closest('form'),
                        is_disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
                        is_listbox_item: !!el.closest('[role="listbox"], [role="option"], [role="menu"], [data-radix-popper-content-wrapper]'),
                        top: rect.top,
                        left: rect.left,
                        bottom: rect.bottom,
                        viewport_height: window.innerHeight || 0,
                        viewport_width: window.innerWidth || 0,
                    };
                }"""
            )
            if not isinstance(meta, dict):
                return None
            return meta
        except Exception:
            return None

    async def _pick_best_publish_candidate(self, selector: str, limit: int = 8):
        if self.page is None:
            return None

        try:
            locator = self.page.locator(selector)
            count = min(await locator.count(), limit)
        except Exception:
            return None

        best = None
        best_meta = None
        best_score = -1

        for index in range(count):
            candidate = locator.nth(index)
            meta = await self._inspect_publish_candidate(candidate)
            if meta is None:
                continue
            score = self._publish_candidate_rank(meta)
            if score > best_score:
                best = candidate
                best_meta = meta
                best_score = score

        if best is None or best_score < 0:
            return None
        return best, best_meta, best_score

    async def _find_publish_button(self):
        if self.page is None:
            return None

        await self._dismiss_transient_overlays()

        selectors = [
            "button:has-text('Submit for Review')",
            "[role='dialog'] button:has-text('Submit for Review')",
            "button[type='submit']:has-text('Submit for Review')",
            "button:has-text('Publish Article')",
            "[role='dialog'] button:has-text('Publish Article')",
            "button[type='submit']:has-text('Publish Article')",
            "[data-testid='publish-button']",
            "[role='dialog'] button[type='submit']",
            "button[type='submit']:has-text('Publish')",
            "button:has-text('Submit')",
            "button:has-text('Save')",
            "button:has-text('Publish')",
        ]

        best = None
        best_meta = None
        best_score = -1

        for sel in selectors:
            result = await self._pick_best_publish_candidate(sel)
            if result is None:
                continue
            candidate, meta, score = result
            if score > best_score:
                best = candidate
                best_meta = meta
                best_score = score

        generic = await self._pick_best_publish_candidate("button, [role='button'], input[type='submit']", limit=40)
        if generic is not None:
            candidate, meta, score = generic
            if score > best_score:
                best = candidate
                best_meta = meta
                best_score = score

        if best is not None and best_meta is not None:
            self.logger.info(
                "Publish target chosen: text=%r score=%s",
                best_meta.get("text", ""),
                best_score,
            )
            return best

        return None

    async def _wait_publish_success(self) -> bool:
        if self.page is None:
            return False

        for _ in range(16):
            await asyncio.sleep(0.7)
            try:
                if await self._is_articles_page() and not await self._is_article_form_open():
                    return True

                if await self.page.locator("text=/published|success|created|review/i").first.is_visible(timeout=200):
                    return True
            except Exception:
                pass

        return False

    async def publish(self) -> bool:
        if not await self.ensure_live_page():
            return self._fail("browser_unavailable")

        try:
            if await self._is_articles_page() and not await self._is_article_form_open():
                self.last_error = ""
                return True

            btn = None
            for _ in range(8):
                await self._scroll_form_to_section("Submit for Review")
                btn = await self._find_publish_button()
                if btn is not None:
                    break
                await self._scroll_modal_by(900)
                await asyncio.sleep(0.4)

            if btn is None:
                await self._dump_debug("publish_button_missing")
                return self._fail("submit_button_not_found")

            try:
                await btn.scroll_into_view_if_needed(timeout=5000)
            except Exception:
                pass
            await asyncio.sleep(0.3)
            await btn.click(force=True)

            if await self._wait_publish_success():
                # Let CMS React state fully settle before the next article opens the modal
                await asyncio.sleep(1.5)
                self.last_error = ""
                return True

            await self._dump_debug("publish_no_success_signal")
            return self._fail("no_success_signal_after_submit")
        except Exception as exc:
            self.logger.error(f"Publish failed: {exc}")
            await self._dump_debug("publish_exception")
            return self._fail(f"publish_exception:{type(exc).__name__}")

    async def verify_publish(self) -> bool:
        return await self._wait_publish_success()




























