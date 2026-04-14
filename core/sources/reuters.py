"""
Reuters scraper using pure HTTP requests.
"""

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
    published_time: Optional[str]
    source: str = "Reuters"


class ReutersScraper:
    BASE_URL = "https://www.reuters.com"
    NEWS_URL = "https://www.reuters.com/world/"

    def __init__(self):
        self.logger = get_logger("reuters_scraper")
        self.client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
        )

    def _section_root(self) -> str:
        path = urlparse(self.NEWS_URL).path.strip("/")
        return path.split("/")[0] if path else ""

    def _is_candidate_url(self, url: str, section_root: str) -> bool:
        if not url.startswith(self.BASE_URL):
            return False

        low = url.lower()
        if any(x in low for x in ("/video/", "/pictures/", "/graphics/", "/podcast/", "/live/")):
            return False

        parsed = urlparse(url)
        path = parsed.path
        if path.endswith("/") and path.count("/") <= 2:
            return False

        if section_root and f"/{section_root}/" not in path:
            return False

        # Reuters article urls usually end with an uppercase id token.
        if re.search(r"-[A-Z0-9]{6,}/?$", path):
            return True

        # Backup signal for newer URL formats.
        if path.count("/") >= 4 and "-" in path.split("/")[-1]:
            return True

        return False

    def get_article_links(self, limit: int = 20) -> List[str]:
        try:
            response = self.client.get(self.NEWS_URL)
            response.raise_for_status()
            html = response.text

            section_root = self._section_root()

            patterns = [
                r'href="(https://www\.reuters\.com/[^"]+)"',
                r'href="(/[^"#?]+)"',
            ]

            found: List[str] = []
            for pattern in patterns:
                found.extend(re.findall(pattern, html, re.IGNORECASE))

            normalized: List[str] = []
            seen = set()
            for link in found:
                url = f"{self.BASE_URL}{link}" if link.startswith("/") else link
                if url in seen:
                    continue
                seen.add(url)
                if self._is_candidate_url(url, section_root):
                    normalized.append(url)
                if len(normalized) >= limit:
                    break

            self.logger.info(f"Found {len(normalized)} article links")
            return normalized
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
                source="Reuters",
            )

            self.logger.info(f"Scraped: {title[:50]}...")
            return article
        except Exception as exc:
            self.logger.error(f"Failed to scrape {url}: {exc}")
            return None

    def _extract_title(self, html: str) -> Optional[str]:
        patterns = [
            r'"headline"\s*:\s*"([^"]+)"',
            r'<meta\s+property="og:title"\s+content="([^"]+)"',
            r'<meta\s+content="([^"]+)"\s+property="og:title"',
            r'<title>([^<]+)</title>',
            r'<h1[^>]*>([^<]+)</h1>',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                title = match.group(1).strip()
                title = re.sub(r"\s*[-|]\s*Reuters.*$", "", title)
                if len(title) > 15:
                    return title
        return None

    def _extract_body(self, html: str) -> Optional[str]:
        patterns = [
            r'"articleBody"\s*:\s*"([^"]+)"',
            r'"description"\s*:\s*"([^"]{50,})"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                body = match.group(1).strip().replace("\\n", " ").replace("\\u0027", "'")
                if len(body) > 100:
                    return body

        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL)
        valid: List[str] = []
        for paragraph in paragraphs:
            text = re.sub(r"<[^>]+>", "", paragraph).strip()
            if len(text) > 50 and not any(x in text.lower() for x in ["copyright", "cookie", "javascript", "browser"]):
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
                body = match.group(1).strip().replace("\\n", " ").replace("\\u0027", "'")
                if len(body) > 100:
                    return body

        if valid:
            return " ".join(valid[:8])
        return None

    def _extract_og_image(self, html: str) -> Optional[str]:
        patterns = [
            r'"image"\s*:\s*\{[^}]*"url"\s*:\s*"([^"]+)"',
            r'"image"\s*:\s*"([^"]+)"',
            r'<meta\s+property="og:image"\s+content="([^"]+)"',
            r'<meta\s+content="([^"]+)"\s+property="og:image"',
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
