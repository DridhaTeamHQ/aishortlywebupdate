"""
OG Image Downloader - Downloads OG images via HTTP.

NO BROWSER. NO UI SCRAPING.
Prefers higher quality: tries larger URL variants (strip/bump size params) and keeps best.
"""

import httpx
import re
from pathlib import Path
from typing import Optional, List
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from utils.logger import get_logger
from utils.image_utils import meets_minimum_resolution


class OGImageDownloader:
    """
    Downloads OG images via direct HTTP.
    Tries to get a larger version by requesting common "large" URL variants.
    NO BROWSER REQUIRED.
    """

    DOWNLOAD_DIR = Path("downloads/images")
    MIN_SIZE_BYTES = 30000  # 30KB minimum
    PREFERRED_MIN_BYTES = 100000  # Prefer 100KB+ for better quality

    def __init__(self):
        self.logger = get_logger("og_image")
        self.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    def _larger_url_variants(self, image_url: str) -> List[str]:
        """Return candidate URLs for higher-res image (original first, then larger variants)."""
        candidates: List[str] = [image_url]
        try:
            parsed = urlparse(image_url)
            qs = parse_qs(parsed.query, keep_blank_values=True)
            # Common size/width params used by CDNs (TOI, The Hindu, etc.)
            size_keys = ["w", "width", "h", "height", "size", "q", "quality", "resize"]
            changed = False
            for key in size_keys:
                if key in qs:
                    try:
                        val = int(qs[key][0])
                        # Bump to at least 1200 for width/size; 90 for quality
                        if key in ("w", "width", "size", "h", "height") and val < 1200:
                            qs[key] = ["1200"]
                            changed = True
                        elif key == "quality" and val < 90:
                            qs[key] = ["90"]
                            changed = True
                    except (ValueError, IndexError):
                        pass
            if changed:
                new_query = urlencode(qs, doseq=True)
                candidates.append(urlunparse(parsed._replace(query=new_query)))

            # Also try URL with common size params stripped (some CDNs serve full size without params)
            if parsed.query:
                stripped = urlunparse(parsed._replace(query=""))
                if stripped not in candidates:
                    candidates.append(stripped)
        except Exception:
            pass
        return candidates

    def download(self, image_url: str, article_title: str = "article") -> Optional[str]:
        """
        Download image from URL. Tries larger variants and keeps the best-quality result.
        Returns: Local file path or None
        """
        if not image_url:
            self.logger.warning("No image URL provided")
            return None

        candidates = self._larger_url_variants(image_url)
        best_content: Optional[bytes] = None
        best_content_type = ""

        try:
            with httpx.Client(timeout=20.0, follow_redirects=True) as client:
                for url in candidates:
                    try:
                        response = client.get(url)
                        if response.status_code != 200:
                            continue
                        ct = response.headers.get("content-type", "")
                        if not any(t in ct for t in ["image/jpeg", "image/png", "image/webp", "image/gif"]):
                            continue
                        content = response.content
                        if len(content) < self.MIN_SIZE_BYTES:
                            continue
                        # Prefer larger file (better quality)
                        if best_content is None or len(content) > len(best_content):
                            best_content = content
                            best_content_type = ct
                            if len(content) >= self.PREFERRED_MIN_BYTES:
                                break
                    except Exception:
                        continue

                if not best_content:
                    self.logger.warning("No valid image from any URL variant")
                    return None

                if not meets_minimum_resolution(best_content):
                    self.logger.warning("Image below minimum resolution (640x480), rejecting")
                    return None

                ext = ".jpg"
                if "png" in best_content_type:
                    ext = ".png"
                elif "webp" in best_content_type:
                    ext = ".webp"
                elif "gif" in best_content_type:
                    ext = ".gif"

                safe_title = re.sub(r"[^\w\s-]", "", article_title)[:40].strip().replace(" ", "_")
                filename = self.DOWNLOAD_DIR / f"{safe_title}{ext}"
                with open(filename, "wb") as f:
                    f.write(best_content)

                self.logger.info(f"✅ Downloaded: {filename.name} ({len(best_content) // 1024}KB)")
                return str(filename)

        except Exception as e:
            self.logger.error(f"Download failed: {e}")
            return None
