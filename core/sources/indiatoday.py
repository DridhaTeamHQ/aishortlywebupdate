"""India Today scraper using pure HTTP requests."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import httpx

from utils.logger import get_logger


@dataclass
class Article:
    url: str
    title: str
    body: str
    og_image: Optional[str]
    main_image: Optional[str]
    published_time: Optional[str]
    source: str = "India Today"


class IndiaTodayScraper:
    NEWS_URL = "https://www.indiatoday.in/india"

    def __init__(self):
        self.logger = get_logger("indiatoday_scraper")
        self.client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )

    def _base_url(self) -> str:
        parsed = urlparse(self.NEWS_URL)
        return f"{parsed.scheme}://{parsed.netloc}"

    def get_article_links(self, limit: int = 20) -> List[str]:
        try:
            response = self.client.get(self.NEWS_URL)
            response.raise_for_status()
            html_text = response.text

            hrefs = re.findall(r'href=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
            found: List[str] = []
            seen = set()
            for href in hrefs:
                url = urljoin(self._base_url(), html.unescape(href.strip()))
                parsed = urlparse(url)
                if parsed.netloc.lower() != "www.indiatoday.in":
                    continue
                path = parsed.path.lower()
                if "/story/" not in path:
                    continue
                if not re.search(r"-\d{5,}", path):
                    continue
                if any(skip in path for skip in ("/video", "/videos", "/photos", "/visualstories", "/fact-check")):
                    continue
                if url in seen:
                    continue
                seen.add(url)
                found.append(url)
                if len(found) >= limit:
                    break

            self.logger.info(f"Found {len(found)} article links (India Today)")
            return found
        except Exception as exc:
            self.logger.error(f"Failed to get article links: {exc}")
            return []

    def scrape_article(self, url: str) -> Optional[Article]:
        try:
            response = self.client.get(url)
            response.raise_for_status()
            html_text = response.text

            title = self._extract_title(html_text)
            if not title or len(title) < 18:
                self.logger.warning(f"No valid title found: {url}")
                return None

            body = self._extract_body(html_text)
            if not body or len(body) < 80:
                self.logger.warning(f"No valid body found: {url}")
                return None

            og_image = self._extract_og_image(html_text)
            main_image = self._extract_main_image(html_text)
            published_time = self._extract_published_time(html_text)
            article = Article(
                url=url,
                title=title.strip(),
                body=body.strip(),
                og_image=og_image,
                main_image=main_image,
                published_time=published_time,
            )
            self.logger.info(f"Scraped: {title[:50]}...")
            return article
        except Exception as exc:
            self.logger.error(f"Failed to scrape {url}: {exc}")
            return None

    def _extract_title(self, html_text: str) -> Optional[str]:
        patterns = [
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
            r'"headline"\s*:\s*"([^"]+)"',
            r'<title>([^<]+)</title>',
            r'<h1[^>]*>([^<]+)</h1>',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if not match:
                continue
            title = self._clean_text(match.group(1))
            title = re.sub(r"\s*[-|]\s*India Today.*$", "", title, flags=re.IGNORECASE)
            if len(title) > 15:
                return title
        return None

    def _extract_body(self, html_text: str) -> Optional[str]:
        paragraphs = re.findall(r'<p\b[^>]*>(.*?)</p>', html_text, re.DOTALL | re.IGNORECASE)
        valid: List[str] = []
        for paragraph in paragraphs:
            text = self._clean_text(re.sub(r"<[^>]+>", " ", paragraph))
            if len(text) > 28 and not any(x in text.lower() for x in ["copyright", "advertisement", "read more"]):
                valid.append(text)
        if len(valid) >= 3 or len(" ".join(valid[:10])) > 260:
            return " ".join(valid[:10])

        meta_patterns = [
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            r'"description"\s*:\s*"([^"]{80,})"',
        ]
        for pattern in meta_patterns:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                text = self._clean_text(match.group(1))
                if len(text) > 75:
                    return text
        if valid:
            return " ".join(valid[:10])
        return None

    def _is_preferred_story_image(self, url: str) -> bool:
        low = (url or "").lower()
        if not low.startswith("http"):
            return False
        if any(token in low for token in ("/reporter/", "androidtv-app", "default-690x413", "screenshot_", "/sites/indiatoday/resources/")):
            return False
        return "/images/story/" in low or "16x9_0" in low

    def _extract_og_image(self, html_text: str) -> Optional[str]:
        patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                candidate = html.unescape(match.group(1).strip())
                if candidate.startswith("http") and ".svg" not in candidate.lower():
                    return candidate
        return None

    def _extract_main_image(self, html_text: str) -> Optional[str]:
        preferred_patterns = [
            r'(https://akm-img-a-in\.tosshub\.com/indiatoday/images/story/[^"\s]+16x9_0\.(?:jpg|jpeg|png|webp)(?:\?[^"\s]+)?)',
            r'(https://akm-img-a-in\.tosshub\.com/indiatoday/images/story/[^"\s]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\s]+)?)',
        ]
        for pattern in preferred_patterns:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                candidate = html.unescape(match.group(1).strip())
                if self._is_preferred_story_image(candidate):
                    return candidate

        image_matches = re.findall(r'https://akm-img-a-in\.tosshub\.com/[^"\s]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\s]+)?', html_text, re.IGNORECASE)
        for raw in image_matches:
            candidate = html.unescape(raw.strip())
            if self._is_preferred_story_image(candidate):
                return candidate
        return None

    def _extract_published_time(self, html_text: str) -> Optional[str]:
        for pattern in [r'"datePublished"\s*:\s*"([^"]+)"', r'<time[^>]+datetime=["\']([^"\']+)["\']']:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _clean_text(self, value: str) -> str:
        text = html.unescape(value or "")
        text = text.replace("\\n", " ").replace("\\u0027", "'").replace('\\"', '"')
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def close(self):
        self.client.close()
