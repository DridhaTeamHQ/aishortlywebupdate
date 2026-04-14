"""
The Guardian scraper using pure HTTP requests.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from utils.logger import get_logger

_MONTH = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


@dataclass
class Article:
    url: str
    title: str
    body: str
    og_image: Optional[str]
    published_time: Optional[str]
    source: str = "Guardian"


class GuardianScraper:
    BASE_URL = "https://www.theguardian.com"
    NEWS_URL = "https://www.theguardian.com/world"

    def __init__(self):
        self.logger = get_logger("guardian_scraper")
        self.client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )

    def _date_from_guardian_url(self, url: str) -> Optional[Tuple[int, int, int]]:
        m = re.search(r"/(\d{4})/([a-z]{3})/(\d{1,2})/", url)
        if not m:
            return None
        year = int(m.group(1))
        mon = _MONTH.get(m.group(2).lower())
        day = int(m.group(3))
        if mon is None:
            return None
        return (year, mon, day)

    def _section_root(self) -> str:
        path = urlparse(self.NEWS_URL).path.strip("/")
        return path.split("/")[0] if path else ""

    def get_article_links(self, limit: int = 20) -> List[str]:
        try:
            response = self.client.get(self.NEWS_URL)
            response.raise_for_status()
            html = response.text

            patterns = [
                r'href="(https://www\.theguardian\.com/[^"#?]+/\d{4}/[a-z]{3}/\d{1,2}/[^"#?]+)"',
                r'href="(/[^"#?]+/\d{4}/[a-z]{3}/\d{1,2}/[^"#?]+)"',
            ]

            found: List[str] = []
            for pattern in patterns:
                found.extend(re.findall(pattern, html, re.IGNORECASE))

            section_root = self._section_root()
            normalized: List[str] = []
            seen = set()
            for link in found:
                url = f"{self.BASE_URL}{link}" if link.startswith("/") else link
                if "/live/" in url or "/video/" in url:
                    continue
                if section_root and f"/{section_root}/" not in url:
                    # Keep cross-links only if we still have too few in-section links.
                    pass
                if url in seen:
                    continue
                seen.add(url)
                normalized.append(url)

            in_section = [u for u in normalized if f"/{section_root}/" in u] if section_root else normalized
            candidates = in_section if len(in_section) >= max(3, min(limit, 6)) else normalized

            candidates.sort(key=lambda u: self._date_from_guardian_url(u) or (0, 0, 0), reverse=True)
            result = candidates[:limit]

            self.logger.info(f"Found {len(result)} article links (newest first)")
            return result
        except Exception as exc:
            self.logger.error(f"Failed to get article links: {exc}")
            return []

    def scrape_article(self, url: str) -> Optional[Article]:
        try:
            response = self.client.get(url)
            response.raise_for_status()
            html = response.text

            title = self._extract_title(html)
            if not title or len(title) < 20:
                self.logger.warning(f"No valid title found: {url}")
                return None

            body = self._extract_body(html)
            if not body or len(body) < 100:
                self.logger.warning(f"No valid body found: {url}")
                return None

            og_image = self._extract_og_image(html)
            if not og_image:
                self.logger.warning(f"No OG image found: {url}")
                return None

            published_time = self._extract_published_time(html)
            article = Article(
                url=url,
                title=title.strip(),
                body=body.strip(),
                og_image=og_image,
                published_time=published_time,
                source="Guardian",
            )
            self.logger.info(f"Scraped: {title[:50]}...")
            return article
        except Exception as exc:
            self.logger.error(f"Failed to scrape {url}: {exc}")
            return None

    def _extract_title(self, html: str) -> Optional[str]:
        patterns = [
            r'<meta\s+property="og:title"\s+content="([^"]+)"',
            r'<meta\s+content="([^"]+)"\s+property="og:title"',
            r'"headline"\s*:\s*"([^"]+)"',
            r'<title>([^<]+)</title>',
            r'<h1[^>]*>([^<]+)</h1>',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                title = match.group(1).strip()
                title = re.sub(r"\s*\|\s*.*$", "", title)
                if len(title) > 15:
                    return title
        return None

    def _extract_body(self, html: str) -> Optional[str]:
        patterns = [
            r'"description"\s*:\s*"([^"]{50,})"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                body = match.group(1).strip()
                if len(body) > 100:
                    return body

        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL)
        valid: List[str] = []
        for paragraph in paragraphs:
            text = re.sub(r"<[^>]+>", "", paragraph).strip()
            if len(text) > 50 and not any(x in text.lower() for x in ["copyright", "cookie", "javascript"]):
                valid.append(text)

        if len(valid) >= 3 or len(" ".join(valid[:8])) > 260:
            return " ".join(valid[:8])

        meta_patterns = [
            r'<meta\s+property="og:description"\s+content="([^"]+)"',
            r'<meta\s+content="([^"]+)"\s+property="og:description"',
            r'<meta\s+name="description"\s+content="([^"]+)"',
        ]
        for pattern in meta_patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                body = match.group(1).strip()
                if len(body) > 100:
                    return body

        if valid:
            return " ".join(valid[:8])
        return None

    def _extract_og_image(self, html: str) -> Optional[str]:
        patterns = [
            r'<meta\s+property="og:image"\s+content="([^"]+)"',
            r'<meta\s+content="([^"]+)"\s+property="og:image"',
            r'"image"\s*:\s*"([^"]+)"',
            r'<meta\s+name="twitter:image"\s+content="([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                url = match.group(1)
                if url.startswith("http"):
                    return url
        return None

    def _extract_published_time(self, html: str) -> Optional[str]:
        patterns = [
            r'"datePublished"\s*:\s*"([^"]+)"',
            r'<time[^>]+datetime="([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return match.group(1)
        return None

    def close(self):
        self.client.close()
