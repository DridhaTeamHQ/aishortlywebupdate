"""Times of India scraper using pure HTTP requests."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse

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
    source: str = "TOI"


class TimesOfIndiaScraper:
    BASE_URL = "https://timesofindia.indiatimes.com"
    NEWS_URL = "https://timesofindia.indiatimes.com/india"

    def __init__(self):
        self.logger = get_logger("toi_scraper")
        self.client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )

    def _section_root(self) -> str:
        path = urlparse(self.NEWS_URL).path.strip("/")
        return path.split("/")[0] if path else "india"

    def get_article_links(self, limit: int = 20) -> List[str]:
        try:
            response = self.client.get(self.NEWS_URL)
            response.raise_for_status()
            html_text = response.text

            section = re.escape(self._section_root())
            patterns = [
                rf'href="(https://timesofindia\.indiatimes\.com/{section}/[^\"]+/articleshow/\d+\.cms)"',
                rf'href="(/{section}/[^\"]+/articleshow/\d+\.cms)"',
            ]

            found: List[str] = []
            for pattern in patterns:
                found.extend(re.findall(pattern, html_text, re.IGNORECASE))

            # Fallback to generic TOI article pattern when section extraction is sparse.
            if len(found) < 3:
                found.extend(
                    re.findall(
                        r'href="(https://timesofindia\.indiatimes\.com/[^"]+/articleshow/\d+\.cms)"',
                        html_text,
                        re.IGNORECASE,
                    )
                )

            normalized: List[str] = []
            seen = set()
            for link in found:
                url = f"{self.BASE_URL}{link}" if link.startswith("/") else link
                if "/articleshow/" not in url:
                    continue
                if url in seen:
                    continue
                seen.add(url)
                normalized.append(url)
                if len(normalized) >= limit:
                    break

            self.logger.info(f"Found {len(normalized)} article links (TOI {self._section_root().capitalize()})")
            return normalized
        except Exception as exc:
            self.logger.error(f"Failed to get article links: {exc}")
            return []

    def scrape_article(self, url: str) -> Optional[Article]:
        try:
            response = self.client.get(url)
            response.raise_for_status()
            html_text = response.text

            title = self._extract_title(html_text)
            if not title or len(title) < 20:
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
                source="TOI",
            )
            self.logger.info(f"Scraped: {title[:50]}...")
            return article
        except Exception as exc:
            self.logger.error(f"Failed to scrape {url}: {exc}")
            return None

    def _extract_title(self, html_text: str) -> Optional[str]:
        patterns = [
            r'<meta\s+property="og:title"\s+content="([^"]+)"',
            r'<meta\s+content="([^"]+)"\s+property="og:title"',
            r'"headline"\s*:\s*"([^"]+)"',
            r'<title>([^<]+)</title>',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                title = html.unescape(match.group(1).strip())
                title = re.sub(r"\s*\|\s*Times of India.*$", "", title, flags=re.IGNORECASE)
                if len(title) > 15:
                    return title
        return None

    def _extract_body(self, html_text: str) -> Optional[str]:
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html_text, re.DOTALL | re.IGNORECASE)
        valid: List[str] = []
        for paragraph in paragraphs:
            text = html.unescape(re.sub(r"<[^>]+>", " ", paragraph)).strip()
            text = re.sub(r"\s+", " ", text)
            if len(text) > 24 and not any(x in text.lower() for x in ["copyright", "subscribe", "advertisement"]):
                valid.append(text)
        if len(valid) >= 3 or len(" ".join(valid[:10])) > 260:
            return " ".join(valid[:10])

        patterns = [
            r'<meta\s+property="og:description"\s+content="([^"]+)"',
            r'<meta\s+content="([^"]+)"\s+property="og:description"',
            r'<meta\s+name="description"\s+content="([^"]+)"',
            r'"description"\s*:\s*"([^"]{70,})"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                body = html.unescape(match.group(1).strip().replace("\\n", " "))
                if len(body) > 70:
                    return body
        if valid:
            return " ".join(valid[:10])
        return None

    def _convert_photo_cms_to_jpg(self, url: str) -> str:
        match = re.search(r"/photo/(\d+)\.cms", url, re.IGNORECASE)
        if not match:
            return url
        msid = match.group(1)
        return f"https://static.toiimg.com/thumb/msid-{msid},width-1280,height-720,resizemode-6/photo.jpg"

    def _extract_og_image(self, html_text: str) -> Optional[str]:
        patterns = [
            r'<meta\s+property="og:image"\s+content="([^"]+)"',
            r'<meta\s+content="([^"]+)"\s+property="og:image"',
            r'<meta\s+name="twitter:image"\s+content="([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if not match:
                continue
            image_url = html.unescape(match.group(1).strip())
            if not image_url.startswith("http"):
                continue
            if "/photo/" in image_url and image_url.endswith(".cms"):
                image_url = self._convert_photo_cms_to_jpg(image_url)
            return image_url
        return None

    def _extract_main_image(self, html_text: str) -> Optional[str]:
        thumb = re.search(r'(https://static\.toiimg\.com/thumb/msid-[^"\s]+/photo\.(?:jpg|jpeg|webp))', html_text, re.IGNORECASE)
        if thumb:
            return html.unescape(thumb.group(1).strip())

        photo_jpg = re.search(r'(https://static\.toiimg\.com/photo/[^"\s]+\.(?:jpg|jpeg|webp))', html_text, re.IGNORECASE)
        if photo_jpg:
            return html.unescape(photo_jpg.group(1).strip())

        patterns = [
            r'<figure[^>]*>.*?<img[^>]+(?:data-src|src)="(https?://[^"]+)"',
            r'<article[^>]*>.*?<img[^>]+(?:data-src|src)="(https?://[^"]+)"',
            r'<img[^>]+(?:data-src|src)="(https?://[^"]+)"[^>]*(?:article|story)',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, re.DOTALL | re.IGNORECASE)
            if not match:
                continue
            image_url = html.unescape(match.group(1).strip())
            if "/photo/" in image_url and image_url.endswith(".cms"):
                image_url = self._convert_photo_cms_to_jpg(image_url)
            if image_url.startswith("http") and ".svg" not in image_url.lower():
                return image_url
        return None

    def _extract_published_time(self, html_text: str) -> Optional[str]:
        for pattern in [r'"datePublished"\s*:\s*"([^"]+)"', r'<time[^>]+datetime="([^"]+)"']:
            match = re.search(pattern, html_text)
            if match:
                return match.group(1)
        return None

    def close(self):
        self.client.close()
