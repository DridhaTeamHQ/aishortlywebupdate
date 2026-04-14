"""Al Jazeera News Scraper - resilient extraction for dynamic pages."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import List, Optional

import httpx

from utils.logger import get_logger


@dataclass
class Article:
    url: str
    title: str
    body: str
    og_image: Optional[str]
    published_time: Optional[str]
    source: str = "AlJazeera"


class AlJazeeraScraper:
    BASE_URL = "https://www.aljazeera.com"
    NEWS_URL = "https://www.aljazeera.com/news"

    def __init__(self):
        self.logger = get_logger("aljazeera_scraper")
        self.client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )

    def get_article_links(self, limit: int = 20) -> List[str]:
        try:
            response = self.client.get(self.NEWS_URL)
            response.raise_for_status()
            html_text = response.text

            patterns = [
                r'href="(/news/\d{4}/\d{1,2}/\d{1,2}/[^"]+)"',
                r'href="(https://www\.aljazeera\.com/news/\d{4}/\d{1,2}/\d{1,2}/[^"]+)"',
            ]
            matches: List[str] = []
            for pattern in patterns:
                matches.extend(re.findall(pattern, html_text, re.IGNORECASE))

            unique_urls: List[str] = []
            seen = set()
            for match in matches:
                url = f"{self.BASE_URL}{match}" if match.startswith("/") else match
                if url in seen:
                    continue
                seen.add(url)
                unique_urls.append(url)
                if len(unique_urls) >= limit:
                    break

            self.logger.info(f"Found {len(unique_urls)} article links")
            return unique_urls
        except Exception as exc:
            self.logger.error(f"Failed to get article links: {exc}")
            return []

    def scrape_article(self, url: str) -> Optional[Article]:
        try:
            response = self.client.get(url)
            response.raise_for_status()
            html_text = response.text

            title = self._extract_title(html_text)
            if not title or len(title) < 16:
                self.logger.warning(f"No valid title found: {url}")
                return None

            body = self._extract_body(html_text)
            if not body or len(body) < 60:
                self.logger.warning(f"No valid body found: {url}")
                return None

            og_image = self._extract_og_image(html_text)
            published_time = self._extract_published_time(html_text)
            article = Article(
                url=url,
                title=title.strip(),
                body=body.strip(),
                og_image=og_image,
                published_time=published_time,
            )
            self.logger.info(f"✅ Scraped: {title[:50]}...")
            return article
        except Exception as exc:
            self.logger.error(f"Failed to scrape {url}: {exc}")
            return None

    def _extract_title(self, html_text: str) -> Optional[str]:
        for pattern in [
            r'"headline"\s*:\s*"([^"]+)"',
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
            r'<title>([^<]+)</title>',
            r'<h1[^>]*>([^<]+)</h1>',
        ]:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if not match:
                continue
            title = self._clean_text(match.group(1))
            title = re.sub(r"\s*[-|]\s*Al Jazeera.*$", "", title, flags=re.IGNORECASE)
            if len(title) > 12:
                return title
        return None

    def _extract_body(self, html_text: str) -> Optional[str]:
        for pattern in [
            r'"articleBody"\s*:\s*"([^"]{80,})"',
            r'"description"\s*:\s*"([^"]{80,})"',
        ]:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if not match:
                continue
            text = self._clean_text(match.group(1))
            if len(text) > 55:
                return text

        paragraphs = re.findall(r'<p\b[^>]*>(.*?)</p>', html_text, re.DOTALL | re.IGNORECASE)
        valid = []
        for paragraph in paragraphs:
            text = self._clean_text(re.sub(r"<[^>]+>", " ", paragraph))
            if len(text) > 24 and not any(x in text.lower() for x in ["copyright", "javascript", "cookie", "newsletter"]):
                valid.append(text)
        if len(valid) >= 3 or len(" ".join(valid[:10])) > 260:
            return " ".join(valid[:10])

        for pattern in [
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)"',
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)"',
        ]:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if not match:
                continue
            text = self._clean_text(match.group(1))
            if len(text) > 55:
                return text
        if valid:
            return " ".join(valid[:10])
        return None

    def _extract_og_image(self, html_text: str) -> Optional[str]:
        for pattern in [
            r'"image"\s*:\s*\{\s*[^}]*"url"\s*:\s*"([^"]+)"',
            r'"image"\s*:\s*"([^"]+)"',
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)"',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)"',
        ]:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                url = match.group(1).strip()
                if url.startswith("http"):
                    return url
        return None

    def _extract_published_time(self, html_text: str) -> Optional[str]:
        for pattern in [r'<time[^>]+datetime=["\']([^"\']+)["\']', r'"datePublished"\s*:\s*"([^"]+)"']:
            match = re.search(pattern, html_text)
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
