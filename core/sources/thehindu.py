"""
The Hindu National News Scraper - Pure HTML scraping only.

NO BROWSER. Uses httpx to fetch HTML directly.
Source: https://www.thehindu.com/news/national/
"""

import httpx
import re
from typing import Optional, List
from dataclasses import dataclass
from utils.logger import get_logger


@dataclass
class Article:
    """Article data container."""
    url: str
    title: str
    body: str
    og_image: Optional[str]
    main_image: Optional[str]  # Larger in-article image (better quality than og:image)
    published_time: Optional[str]
    source: str = "The Hindu"


class TheHinduScraper:
    """
    The Hindu National section scraper using pure HTTP requests.
    """

    BASE_URL = "https://www.thehindu.com"
    NEWS_URL = "https://www.thehindu.com/news/national/"

    def __init__(self):
        self.logger = get_logger("thehindu_scraper")
        self.client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )

    def get_article_links(self, limit: int = 20) -> List[str]:
        """Get article links from The Hindu National page."""
        try:
            response = self.client.get(self.NEWS_URL)
            response.raise_for_status()
            html = response.text

            # The Hindu article URLs: /news/national/.../article12345678.ece or full URL
            pattern = r'href="(https?://(?:www\.)?thehindu\.com/news/national/[^"]+\.ece[^"]*)"'
            matches = re.findall(pattern, html, re.IGNORECASE)
            if not matches:
                pattern = r'href="(/news/national/[^"]+\.ece[^"]*)"'
                matches = re.findall(pattern, html, re.IGNORECASE)
                matches = [self.BASE_URL + m if m.startswith("/") else m for m in matches]
            unique_links = list(dict.fromkeys(matches))[:limit]

            self.logger.info(f"Found {len(unique_links)} article links (The Hindu National)")
            return unique_links

        except Exception as e:
            self.logger.error(f"Failed to get article links: {e}")
            return []

    def scrape_article(self, url: str) -> Optional[Article]:
        """Scrape a single article. Returns Article or None."""
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
            main_image = self._extract_main_image(html)
            published_time = self._extract_published_time(html)

            article = Article(
                url=url,
                title=title.strip(),
                body=body.strip(),
                og_image=og_image,
                main_image=main_image,
                published_time=published_time,
                source="The Hindu"
            )
            self.logger.info(f"✅ Scraped: {title[:50]}...")
            return article

        except Exception as e:
            self.logger.error(f"Failed to scrape {url}: {e}")
            return None

    def _extract_title(self, html: str) -> Optional[str]:
        patterns = [
            r'<meta\s+property="og:title"\s+content="([^"]+)"',
            r'<meta\s+content="([^"]+)"\s+property="og:title"',
            r'"headline"\s*:\s*"([^"]+)"',
            r'<title>([^<]+)</title>',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                title = match.group(1).strip()
                title = re.sub(r'\s*\|\s*The Hindu.*$', '', title, flags=re.IGNORECASE)
                if len(title) > 15:
                    return title
        return None

    def _extract_body(self, html: str) -> Optional[str]:
        p_pattern = r'<p[^>]*>(.*?)</p>'
        paragraphs = re.findall(p_pattern, html, re.DOTALL)
        valid = [re.sub(r'<[^>]+>', '', p).strip() for p in paragraphs if len(re.sub(r'<[^>]+>', '', p).strip()) > 50]
        if len(valid) >= 3 or len(" ".join(valid[:8])) > 260:
            return ' '.join(valid[:8])

        patterns = [
            r'<meta\s+property="og:description"\s+content="([^"]+)"',
            r'<meta\s+content="([^"]+)"\s+property="og:description"',
            r'<meta\s+name="description"\s+content="([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                body = match.group(1).strip()
                if len(body) > 100:
                    return body
        if valid:
            return ' '.join(valid[:8])
        return None

    def _extract_og_image(self, html: str) -> Optional[str]:
        patterns = [
            r'<meta\s+property="og:image"\s+content="([^"]+)"',
            r'<meta\s+content="([^"]+)"\s+property="og:image"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                u = match.group(1)
                if u.startswith('http'):
                    return u
        return None

    def _extract_main_image(self, html: str) -> Optional[str]:
        """Extract main article image URL (often larger than og:image). Prefer non-thumbnail."""
        # The Hindu: article img, picture source, or img in story body
        patterns = [
            r'<picture[^>]*>.*?<source[^>]+srcset="(https?://[^"]+)"',
            r'<img[^>]+(?:data-src|src)="(https?://[^"]+)"[^>]*(?:class="[^"]*article[^"]*"|class="[^"]*story[^"]*"|class="[^"]*lead[^"]*")',
            r'<figure[^>]*>.*?<img[^>]+(?:data-src|src)="(https?://[^"]+)"',
            r'<div[^>]+class="[^"]*article-body[^"]*"[^>]*>.*?<img[^>]+(?:data-src|src)="(https?://[^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
            if match:
                u = match.group(1).strip().split()[0]  # srcset can have "url 1x, url2 2x"
                if u.startswith("http") and "thumb" not in u.lower() and "small" not in u.lower() and ".svg" not in u.lower():
                    return u
        # First img with data-src (lazy full-res) or src in content area
        img_src = re.search(r'<img[^>]+(?:data-src|src)="(https?://[^"]+)"[^>]+(?:class|alt)=', html, re.IGNORECASE)
        if img_src:
            u = img_src.group(1).strip()
            if u.startswith("http") and ".svg" not in u.lower() and "logo" not in u.lower() and "icon" not in u.lower():
                return u
        return None

    def _extract_published_time(self, html: str) -> Optional[str]:
        for pattern in [r'"datePublished"\s*:\s*"([^"]+)"', r'<time[^>]+datetime="([^"]+)"']:
            match = re.search(pattern, html)
            if match:
                return match.group(1)
        return None

    def close(self):
        self.client.close()
