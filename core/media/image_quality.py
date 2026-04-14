"""Image quality pipeline with strict branding rejection and robust candidate recovery."""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

from utils.gemini_client import GeminiClient
from utils.image_utils import get_image_dimensions
from utils.logger import get_logger


@dataclass
class ImageCandidate:
    url: str
    source: str
    priority: int
    width_hint: int = 0
    context_text: str = ""


@dataclass
class ImageDecision:
    passed: bool
    selected_url: Optional[str] = None
    local_path: Optional[str] = None
    score: float = 0.0
    needs_image: bool = False
    metadata: Dict[str, object] = field(default_factory=dict)
    rejection_reasons: List[str] = field(default_factory=list)


class ImageQualityPipeline:
    _VISION_JSON_SCHEMA = {
        "type": "object",
        "properties": {
            "usable": {"type": "boolean"},
            "quality": {"type": "number"},
            "relevance": {"type": "number"},
            "is_relevant": {"type": "boolean"},
            "has_logo": {"type": "boolean"},
            "has_watermark": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["usable", "quality", "relevance", "is_relevant", "has_logo", "has_watermark", "reason"],
    }

    BLOCKED_URL_TOKENS = {
        "watermark",
        "logo",
        "branding",
        "badge",
        "channelbug",
        "sprite",
        "overlay-toi_sw",
        "branded_news",
        "grey-placeholder",
        "bbcdotcom",
        "1x1_spacer",
        "androidtv-app",
        "default-690x413",
        "screenshot_",
        "/reporter/",
        "/_next/image",
        "ai-generated-image",
        "representational-purpose",
        "used-only-for-representational-purpose",
    }
    BLOCKED_QUERY_KEYS = {
        "overlay",
        "overlay-base64",
        "watermark",
        "logo",
        "branding",
        "credit",
    }
    BLOCKED_QUERY_VALUE_TOKENS = {
        "toi_sw",
        "overlay",
        "watermark",
        "logo",
        "branding",
        "credit",
    }
    BLOCKED_HOST_TOKENS = {"scorecardresearch.com"}
    NEGATIVE_CONTEXT_TOKENS = {
        "logo",
        "icon",
        "avatar",
        "author",
        "profile",
        "placeholder",
        "spacer",
        "sprite",
        "branding",
        "badge",
        "credit",
        "reporter",
        "screenshot",
        "promo",
    }
    TITLE_STOPWORDS = {
        "the", "and", "with", "from", "this", "that", "into", "after", "over", "under",
        "amid", "what", "when", "where", "which", "who", "will", "have", "has", "been",
        "about", "into", "against", "their", "its", "than", "news",
    }

    def __init__(self, thresholds: Dict[str, float], download_dir: str = "downloads/images"):
        self.logger = get_logger("image_quality")
        self.thresholds = thresholds
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

        self.vision_weight = float(self.thresholds.get("vision_weight", 0.25))
        self.min_vision_quality = float(self.thresholds.get("min_vision_quality", 0.58))
        self.max_vision_candidates = int(self.thresholds.get("vision_max_candidates", 6))
        self.max_probe_candidates = int(self.thresholds.get("max_probe_candidates", 8))

        vision_enabled_env = (os.getenv("IMAGE_VISION_ENABLED", "true") or "true").strip().lower()
        self.vision_enabled = vision_enabled_env in {"1", "true", "yes", "y"}

        self._vision_cache: Dict[str, Dict[str, object]] = {}
        self._vision_client = None

        if self.vision_enabled:
            try:
                self._vision_client = GeminiClient()
            except Exception as exc:
                self.logger.warning(f"Vision client init failed, continuing without vision filter: {exc}")
                self._vision_client = None

    def select_best(
        self,
        article_url: str,
        title: str,
        fallback_urls: Optional[List[str]] = None,
        article_context: Optional[str] = None,
    ) -> ImageDecision:
        html = self._fetch_html(article_url)
        candidates = self._extract_candidates(html, article_url)

        if fallback_urls:
            for url in fallback_urls:
                normalized = self._normalize_candidate_url(url or "")
                if normalized:
                    candidates.append(ImageCandidate(url=normalized, source="fallback", priority=5, context_text="fallback"))

        if not candidates:
            return ImageDecision(passed=False, needs_image=True, rejection_reasons=["no_candidates"])

        static_passes: List[Tuple[ImageCandidate, Dict[str, object]]] = []
        rejections: List[str] = []

        for cand in self._limit_probe_candidates(self._dedupe_candidates(candidates)):
            probe = self._probe(cand, title, article_context or "")
            if not probe["ok"]:
                rejections.append(str(probe.get("reason", "unknown")))
                continue
            static_passes.append((cand, probe))

        if not static_passes:
            return ImageDecision(
                passed=False,
                needs_image=True,
                rejection_reasons=rejections or ["all_candidates_rejected"],
                metadata={"candidates": len(candidates)},
            )

        static_passes.sort(key=lambda row: float(row[1]["score"]), reverse=True)
        static_passes = static_passes[: max(1, self.max_vision_candidates)]

        best: Optional[Tuple[ImageCandidate, Dict[str, object], float, Dict[str, object]]] = None
        for cand, probe in static_passes:
            vision = (
                self._vision_assess(probe["bytes"], title, cand.context_text, article_context or "")
                if self.vision_enabled and self._vision_client is not None
                else {
                    "usable": True,
                    "quality": 0.6,
                    "relevance": 0.6,
                    "reason": "vision_not_checked",
                    "has_logo": False,
                    "has_watermark": False,
                }
            )

            if not bool(vision.get("usable", True)):
                vision_reason = str(vision.get("reason", "vision_rejected"))
                rejections.append(vision_reason)
                continue

            static_score = float(probe["score"])
            vision_score = float(vision.get("quality", 0.6))
            vision_relevance = float(vision.get("relevance", 0.6))
            vision_composite = (vision_score * 0.7) + (vision_relevance * 0.3)
            final_score = (static_score * (1.0 - self.vision_weight)) + (vision_composite * self.vision_weight)

            if best is None or final_score > best[2]:
                best = (cand, probe, final_score, vision)

        if not best:
            # Publish-first fallback: if static checks passed, keep the strongest static image
            # instead of dropping the article on the vision pass.
            if static_passes:
                cand, probe = static_passes[0]
                path = self._store_image(probe["bytes"], title)
                return ImageDecision(
                    passed=True,
                    selected_url=cand.url,
                    local_path=path,
                    score=float(probe["score"]),
                    needs_image=False,
                    metadata={
                        "source": cand.source,
                        "resolution": [probe["width"], probe["height"]],
                        "bytes": probe["bytes_len"],
                        "content_type": probe.get("content_type", ""),
                        "sharpness": probe["sharpness"],
                        "relevance": probe["relevance"],
                        "static_score": probe["score"],
                        "vision": {"reason": "vision_fallback_static"},
                    },
                    rejection_reasons=rejections,
                )

            return ImageDecision(
                passed=False,
                needs_image=True,
                rejection_reasons=rejections or ["vision_rejected_all"],
                metadata={"candidates": len(candidates)},
            )

        selected, probe, final_score, vision = best
        path = self._store_image(probe["bytes"], title)

        return ImageDecision(
            passed=True,
            selected_url=selected.url,
            local_path=path,
            score=final_score,
            needs_image=False,
            metadata={
                "source": selected.source,
                "resolution": [probe["width"], probe["height"]],
                "bytes": probe["bytes_len"],
                "content_type": probe.get("content_type", ""),
                "sharpness": probe["sharpness"],
                "relevance": probe["relevance"],
                "static_score": probe["score"],
                "candidate_context": selected.context_text,
                "vision": vision,
                "vision_relevance": vision.get("relevance", 0.6),
                "score": final_score,
            },
        )

    def _fetch_html(self, url: str) -> str:
        try:
            with httpx.Client(timeout=20.0, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.text
        except Exception:
            return ""

    def _extract_source_specific_candidates(self, html: str, base_url: str) -> List[ImageCandidate]:
        out: List[ImageCandidate] = []
        host = urlparse(base_url).netloc.lower()

        if "timesofindia.indiatimes.com" in host:
            article_msid = ""
            article_match = re.search(r'articleshow/(\d+)\.cms', base_url, re.IGNORECASE)
            if article_match:
                article_msid = article_match.group(1)

            matches = re.findall(r'https://static\.toiimg\.com/[^"\s]+', html, re.IGNORECASE)
            seen = set()
            for raw in matches:
                candidate = self._normalize_candidate_url(raw)
                low = candidate.lower()
                if candidate in seen:
                    continue
                seen.add(candidate)
                if article_msid and f'msid-{article_msid}' not in low and f'/{article_msid}.jpg' not in low:
                    continue
                if any(token in low for token in ('ai-generated-image', 'representational-purpose', 'used-only-for-representational-purpose')):
                    continue
                priority = 1 if article_msid and f'msid-{article_msid}' in low else 2
                out.append(
                    ImageCandidate(
                        url=candidate,
                        source="source:toi",
                        priority=priority,
                        context_text="toi hero",
                    )
                )

        if "indiatoday.in" in host:
            slug_tokens = self._story_slug_tokens(base_url)
            matches = re.findall(
                r'https://akm-img-a-in\.tosshub\.com/indiatoday/images/story/[^"\s]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\s]+)?',
                html,
                re.IGNORECASE,
            )
            seen = set()
            for raw in matches:
                candidate = self._normalize_candidate_url(raw)
                low = candidate.lower()
                if candidate in seen:
                    continue
                seen.add(candidate)
                if any(token in low for token in ("/reporter/", "androidtv-app", "default-690x413", "screenshot_", "/sites/indiatoday/resources/")):
                    continue
                if slug_tokens and self._candidate_slug_overlap(low, slug_tokens) < 2:
                    continue
                priority = 1 if "16x9_0" in low else 2
                out.append(ImageCandidate(url=candidate, source="source:indiatoday", priority=priority, context_text="indiatoday hero"))

        return out

    def _extract_candidates(self, html: str, base_url: str) -> List[ImageCandidate]:
        out: List[ImageCandidate] = []
        if not html:
            return out

        out.extend(self._extract_source_specific_candidates(html, base_url))

        og = self._meta(html, "property", "og:image")
        if og:
            out.append(ImageCandidate(url=self._normalize_candidate_url(urljoin(base_url, og)), source="og:image", priority=1))

        tw = self._meta(html, "name", "twitter:image")
        if tw:
            out.append(ImageCandidate(url=self._normalize_candidate_url(urljoin(base_url, tw)), source="twitter:image", priority=2))

        for schema_url in self._schema_images(html):
            out.append(ImageCandidate(url=self._normalize_candidate_url(urljoin(base_url, schema_url)), source="schema", priority=3))

        for src, width in self._srcset_images(html):
            out.append(
                ImageCandidate(
                    url=self._normalize_candidate_url(urljoin(base_url, src)),
                    source="srcset",
                    priority=4,
                    width_hint=width,
                    context_text="srcset",
                )
            )

        for src, width_hint, context_text in self._body_images(html):
            out.append(
                ImageCandidate(
                    url=self._normalize_candidate_url(urljoin(base_url, src)),
                    source="body",
                    priority=5,
                    width_hint=width_hint,
                    context_text=context_text,
                )
            )

        out = [c for c in out if c.url]
        out = self._filter_source_candidates(out, base_url)
        out.sort(key=lambda c: (c.priority, -c.width_hint))
        return out

    def _story_slug_tokens(self, base_url: str) -> List[str]:
        path = urlparse(base_url).path.lower()
        parts = [part for part in path.split('/') if part]
        if not parts:
            return []
        slug = parts[-1]
        slug = re.sub(r'articleshow/\d+\.cms$', '', slug)
        slug = re.sub(r'-\d{4}-\d{2}-\d{2}$', '', slug)
        slug = re.sub(r'-\d+$', '', slug)
        tokens = re.findall(r'[a-z]{4,}', slug)
        stop = {
            'story', 'india', 'today', 'world', 'news', 'video', 'photos', 'latest', 'middle', 'east',
            'update', 'updates', 'live', 'breaking', 'review', 'watch', 'explained', 'report', 'reports'
        }
        return [token for token in tokens if token not in stop]

    def _candidate_slug_overlap(self, candidate_url: str, slug_tokens: List[str]) -> int:
        low = candidate_url.lower()
        return sum(1 for token in slug_tokens if token in low)

    def _filter_source_candidates(self, candidates: List[ImageCandidate], base_url: str) -> List[ImageCandidate]:
        host = urlparse(base_url).netloc.lower()
        if "timesofindia.indiatimes.com" in host:
            article_match = re.search(r'articleshow/(\d+)\.cms', base_url, re.IGNORECASE)
            if article_match:
                article_msid = article_match.group(1)
                filtered: List[ImageCandidate] = []
                for cand in candidates:
                    low = cand.url.lower()
                    if "static.toiimg.com" not in low:
                        filtered.append(cand)
                        continue
                    if f"msid-{article_msid}" in low or f"/{article_msid}.jpg" in low or f"/{article_msid}.jpeg" in low:
                        filtered.append(cand)
                if filtered:
                    return filtered
        if "indiatoday.in" in host:
            slug_tokens = self._story_slug_tokens(base_url)
            if slug_tokens:
                filtered = []
                for cand in candidates:
                    low = cand.url.lower()
                    if "akm-img-a-in.tosshub.com/indiatoday/images/story/" not in low:
                        filtered.append(cand)
                        continue
                    if self._candidate_slug_overlap(low, slug_tokens) >= 2:
                        filtered.append(cand)
                if filtered:
                    return filtered
        return candidates

    def _meta(self, html: str, attr: str, value: str) -> Optional[str]:
        p1 = rf'<meta[^>]+{attr}=["\']{re.escape(value)}["\'][^>]+content=["\']([^"\']+)["\']'
        p2 = rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+{attr}=["\']{re.escape(value)}["\']'
        for pattern in [p1, p2]:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _schema_images(self, html: str) -> List[str]:
        urls: List[str] = []
        for match in re.finditer(r'"image"\s*:\s*"([^"]+)"', html):
            urls.append(match.group(1))
        for match in re.finditer(r'"image"\s*:\s*\{[^\}]*"url"\s*:\s*"([^"]+)"', html):
            urls.append(match.group(1))
        return urls[:6]

    def _body_images(self, html: str) -> List[Tuple[str, int, str]]:
        out: List[Tuple[str, int, str]] = []
        for tag in re.findall(r"<img\b[^>]*>", html, flags=re.IGNORECASE):
            src = self._img_attr(tag, "data-src") or self._img_attr(tag, "src")
            if not src or src.startswith("data:"):
                continue

            alt = self._img_attr(tag, "alt")
            title = self._img_attr(tag, "title")
            cls = self._img_attr(tag, "class")
            context = f"{alt} {title} {cls}".strip().lower()
            if context and any(tok in context for tok in self.NEGATIVE_CONTEXT_TOKENS):
                continue

            width_hint = 0
            width_str = self._img_attr(tag, "width")
            if width_str:
                m = re.search(r"\d+", width_str)
                if m:
                    width_hint = int(m.group(0))
            if width_hint and width_hint < 220:
                continue

            out.append((src, width_hint, context[:180]))
            if len(out) >= 24:
                break
        return out

    def _img_attr(self, tag: str, attr: str) -> str:
        match = re.search(rf"\b{re.escape(attr)}\s*=\s*[\"\']([^\"\']+)[\"\']", tag, flags=re.IGNORECASE)
        return (match.group(1).strip() if match else "")

    def _srcset_images(self, html: str) -> List[Tuple[str, int]]:
        out: List[Tuple[str, int]] = []
        for srcset in re.findall(r'srcset=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
            parts = [p.strip() for p in srcset.split(",") if p.strip()]
            best = ("", 0)
            for part in parts:
                chunks = part.split()
                if not chunks:
                    continue
                src = chunks[0]
                width = 0
                if len(chunks) > 1 and chunks[1].endswith("w"):
                    try:
                        width = int(chunks[1][:-1])
                    except ValueError:
                        width = 0
                if width > best[1]:
                    best = (src, width)
            if best[0]:
                out.append(best)
        return out

    def _dedupe_candidates(self, candidates: List[ImageCandidate]) -> List[ImageCandidate]:
        seen = set()
        out: List[ImageCandidate] = []
        for cand in candidates:
            normalized = self._normalize_candidate_url(cand.url)
            if not normalized:
                continue
            key = normalized.split("?")[0].lower()
            if key in seen:
                continue
            seen.add(key)
            cand.url = normalized
            out.append(cand)
        return out

    def _limit_probe_candidates(self, candidates: List[ImageCandidate]) -> List[ImageCandidate]:
        if len(candidates) <= self.max_probe_candidates:
            return candidates
        ranked = sorted(candidates, key=lambda cand: (cand.priority, -cand.width_hint))
        return ranked[: max(1, self.max_probe_candidates)]

    def _normalize_candidate_url(self, url: str) -> str:
        if not url:
            return ""
        normalized = html_lib.unescape(url.strip())
        return normalized.replace(" ", "%20")

    def _candidate_download_urls(self, url: str) -> List[str]:
        normalized = self._normalize_candidate_url(url)
        if not normalized:
            return []

        parsed = urlparse(normalized)
        host = parsed.netloc.lower()

        variants: List[str] = []
        seen = set()

        def add_variant(candidate_url: str) -> None:
            value = (candidate_url or "").strip()
            if not value or value in seen:
                return
            seen.add(value)
            variants.append(value)

        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)

        if "static.toiimg.com" in host:
            cms_match = re.search(r"/photo/(\d+)\.cms$", parsed.path, flags=re.IGNORECASE)
            if cms_match:
                msid = cms_match.group(1)
                add_variant(f"https://static.toiimg.com/thumb/msid-{msid},width-1600,height-900,resizemode-6/photo.jpg")
                add_variant(f"https://static.toiimg.com/thumb/msid-{msid},width-1280,height-720,resizemode-6/photo.jpg")

        if "i.guim.co.uk" in host:
            add_variant(
                urlunparse(
                    parsed._replace(
                        query=urlencode([("width", "1600"), ("dpr", "1"), ("s", "none"), ("crop", "none")], doseq=True)
                    )
                )
            )
            add_variant(
                urlunparse(
                    parsed._replace(
                        query=urlencode([("width", "1200"), ("dpr", "1"), ("s", "none"), ("crop", "none")], doseq=True)
                    )
                )
            )

        add_variant(normalized)

        cleaned_pairs: List[Tuple[str, str]] = []
        for key, value in query_pairs:
            k = key.lower()
            v = value.lower()
            if any(block in k for block in self.BLOCKED_QUERY_KEYS):
                continue
            if any(block in v for block in self.BLOCKED_QUERY_VALUE_TOKENS):
                continue
            cleaned_pairs.append((key, value))

        if cleaned_pairs != query_pairs:
            add_variant(urlunparse(parsed._replace(query=urlencode(cleaned_pairs, doseq=True))))

        if parsed.query:
            add_variant(urlunparse(parsed._replace(query="")))

        if "static.toiimg.com" in host:
            cleaned_path = parsed.path
            cleaned_path = re.sub(r",overlay-[^,/]+", "", cleaned_path, flags=re.IGNORECASE)
            cleaned_path = re.sub(r",(?:pt|x_pad|y_pad)-\d+", "", cleaned_path, flags=re.IGNORECASE)
            if cleaned_path != parsed.path:
                add_variant(urlunparse(parsed._replace(path=cleaned_path, query=parsed.query)))
                if cleaned_pairs:
                    add_variant(urlunparse(parsed._replace(path=cleaned_path, query=urlencode(cleaned_pairs, doseq=True))))
                add_variant(urlunparse(parsed._replace(path=cleaned_path, query="")))

        return variants

    def _probe(self, candidate: ImageCandidate, title: str, article_context: str = "") -> Dict[str, object]:
        if re.search(r"(?:^|[/_-])(logo|sprite|icon|favicon|avatar|author|profile|reporter|screenshot)(?:$|[._/-])", candidate.url.lower()):
            return {"ok": False, "reason": "url_pattern_rejected"}

        probe_urls = self._candidate_download_urls(candidate.url)
        if not probe_urls:
            return {"ok": False, "reason": "download_failed"}

        min_w = int(self.thresholds.get("min_width", 420))
        min_h = int(self.thresholds.get("min_height", 236))
        min_bytes = int(self.thresholds.get("min_file_size_bytes", 30_000))
        min_aspect = float(self.thresholds.get("min_aspect_ratio", 0.4))
        max_aspect = float(self.thresholds.get("max_aspect_ratio", 2.8))
        min_sharpness = float(self.thresholds.get("min_sharpness", 18.0))
        min_relevance = float(self.thresholds.get("min_relevance", 0.12))

        failures: List[str] = []
        best: Optional[Dict[str, object]] = None

        for probe_url in probe_urls:
            if self._is_blocked_image_url(probe_url):
                failures.append("overlay_or_branding_url_rejected")
                continue

            try:
                with httpx.Client(timeout=20.0, follow_redirects=True) as client:
                    resp = client.get(probe_url)
            except Exception:
                failures.append("download_failed")
                continue

            if resp.status_code != 200:
                failures.append("download_failed")
                continue

            final_url = str(resp.url)
            if self._is_blocked_image_url(final_url):
                failures.append("overlay_or_branding_url_rejected")
                continue

            content_type = (resp.headers.get("content-type") or "").lower()
            if content_type and "image" not in content_type:
                failures.append("non_image_content")
                continue

            data = resp.content
            bytes_len = len(data)
            dims = get_image_dimensions(data)

            if not dims:
                if content_type.startswith("image/") and candidate.width_hint >= min_w and bytes_len >= min_bytes:
                    width = int(candidate.width_hint)
                    height = max(min_h, int(width / 1.77))
                else:
                    failures.append("unknown_dimensions")
                    continue
            else:
                width, height = dims

            if width < min_w or height < min_h:
                failures.append("resolution_too_low")
                continue

            megapixels = (width * height) / 1_000_000
            if bytes_len < min_bytes and megapixels < 0.9:
                failures.append("file_too_small")
                continue

            aspect = width / max(1, height)
            if aspect < min_aspect or aspect > max_aspect:
                failures.append("extreme_aspect_ratio")
                continue

            sharpness = self._sharpness_score(data)
            if sharpness < min_sharpness:
                failures.append("blur_detected")
                continue

            relevance = self._relevance_score(probe_url, title, candidate.context_text, article_context)
            # Keep strict relevance checks on noisier discovery sources.
            if relevance < min_relevance and candidate.source in {"body", "srcset", "schema"}:
                failures.append("low_relevance")
                continue

            resolution_score = min(1.5, (width * height) / (1920 * 1080))
            source_bonus = {
                "og:image": 0.08,
                "twitter:image": 0.06,
                "schema": 0.05,
                "srcset": 0.06,
                "body": 0.09,
                "fallback": 0.04,
            }.get(candidate.source, 0.02)
            score = (resolution_score * 0.48) + (min(1.0, sharpness / 100.0) * 0.22) + (relevance * 0.24) + source_bonus

            candidate_probe = {
                "ok": True,
                "score": score,
                "width": width,
                "height": height,
                "bytes_len": bytes_len,
                "content_type": content_type,
                "sharpness": sharpness,
                "relevance": relevance,
                "bytes": data,
            }

            if best is None or float(candidate_probe["score"]) > float(best["score"]):
                best = candidate_probe

        if best is not None:
            return best

        return {"ok": False, "reason": self._collapse_reasons(failures)}

    def _collapse_reasons(self, reasons: List[str]) -> str:
        if not reasons:
            return "download_failed"
        priority = [
            "overlay_or_branding_url_rejected",
            "resolution_too_low",
            "unknown_dimensions",
            "blur_detected",
            "low_relevance",
            "file_too_small",
            "download_failed",
            "non_image_content",
            "extreme_aspect_ratio",
        ]
        for reason in priority:
            if reason in reasons:
                return reason
        return reasons[0]

    def _vision_assess(self, image_bytes: bytes, title: str, candidate_context: str = "", article_context: str = "") -> Dict[str, object]:
        default = {
            "usable": True,
            "quality": 0.6,
            "relevance": 0.6,
            "reason": "vision_unavailable",
            "has_logo": False,
            "has_watermark": False,
        }

        if self._vision_client is None or not self._vision_client.available:
            return default

        if len(image_bytes) > 5_000_000:
            # Fall back to static checks for very large images instead of hard-failing.
            return {
                "usable": True,
                "quality": 0.62,
                "relevance": 0.58,
                "reason": "vision_skipped_large_image",
                "has_logo": False,
                "has_watermark": False,
            }

        key = hashlib.sha1(image_bytes[:120000]).hexdigest()
        if key in self._vision_cache:
            return self._vision_cache[key]

        try:
            prompt = (
                "You are selecting a CMS hero image for a news article. "
                "Reject if there is ANY publisher logo, watermark, corner badge, channel bug, signature mark, "
                "or heavy text overlay anywhere in the image. "
                "Reject if blurry, low-detail, noisy, poor quality, or visually unrelated to the article. "
                "The image must clearly match the main subject of the story, not just the general topic. "
                "Reject generic stock photos, portraits, meeting shots, vehicles, fuel pumps, maps, crowds, "
                "or scenery when the article is about a specific aircraft, person, court case, sports event, location, or object. "
                "Return strict JSON only with keys: usable (bool), quality (0..1), relevance (0..1), is_relevant (bool), "
                "has_logo (bool), has_watermark (bool), reason (string). "
                f"Article title: {title[:180]}. Candidate hints: {candidate_context[:140]}. Article context: {article_context[:220]}"
            )

            raw = self._vision_client.generate_text_with_image(
                prompt,
                image_bytes,
                mime_type="image/jpeg",
                temperature=0,
                max_output_tokens=120,
                response_mime_type="application/json",
                response_schema=self._VISION_JSON_SCHEMA,
            )
            parsed = self._parse_vision_json(raw)

            usable = bool(parsed.get("usable", True))
            quality = float(parsed.get("quality", 0.6))
            relevance = float(parsed.get("relevance", 0.55))
            is_relevant = bool(parsed.get("is_relevant", True))
            has_logo = bool(parsed.get("has_logo", False))
            has_watermark = bool(parsed.get("has_watermark", False))
            reason = str(parsed.get("reason", "vision_ok"))

            if has_logo or has_watermark:
                usable = False
                reason = "logo_or_watermark_detected"

            if quality < self.min_vision_quality:
                usable = False
                reason = "vision_low_quality"

            min_vision_relevance = float(self.thresholds.get("min_vision_relevance", 0.4))
            if (not is_relevant) or relevance < min_vision_relevance:
                usable = False
                reason = "vision_irrelevant"

            reason_low = reason.lower()
            if any(tok in reason_low for tok in {"logo", "watermark", "channel bug", "corner bug", "publisher mark"}):
                usable = False
                reason = "logo_or_watermark_detected"

            if any(tok in reason_low for tok in {"text overlay", "heavy text", "branding"}):
                usable = False
                reason = "vision_text_overlay"

            if any(tok in reason_low for tok in {"unclear", "not sure", "cannot confirm"}):
                usable = False
                reason = "vision_uncertain_branding"

            result = {
                "usable": usable,
                "quality": max(0.0, min(1.0, quality)),
                "relevance": max(0.0, min(1.0, relevance)),
                "reason": reason,
                "has_logo": has_logo,
                "has_watermark": has_watermark,
            }
            self._vision_cache[key] = result
            return result
        except Exception as exc:
            self.logger.warning(f"Vision assessment failed: {exc}")
            return {
                "usable": False,
                "quality": 0.0,
                "relevance": 0.0,
                "reason": "vision_error_rejected",
                "has_logo": False,
                "has_watermark": False,
            }

    def _is_blocked_image_url(self, url: str) -> bool:
        normalized = self._normalize_candidate_url(url)
        parsed = urlparse(normalized)
        host = (parsed.netloc or "").lower()

        if any(h in host for h in self.BLOCKED_HOST_TOKENS):
            return True

        low = normalized.lower()
        if low.endswith(".svg"):
            return True
        if any(tok in low for tok in self.BLOCKED_URL_TOKENS):
            return True

        # Hard-block common BBC branded/watermark and tiny placeholder patterns.
        if "ichef.bbci.co.uk/news/" in low and "/branded_news/" in low:
            return True
        if "static.files.bbci.co.uk" in low:
            return True
        if re.search(r"(?:^|[?&,/])(w|width|h|height)=?(9[0-9]|1[0-9]{2})(?:[,&]|$)", low):
            return True

        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            k = key.lower()
            v = value.lower()
            if any(block in k for block in self.BLOCKED_QUERY_KEYS):
                return True
            if any(block in v for block in self.BLOCKED_QUERY_VALUE_TOKENS):
                return True

            if k in {"w", "width", "h", "height"}:
                m = re.search(r"\d+", v)
                if m and int(m.group(0)) < 220:
                    return True
            if k == "resize":
                m = re.search(r"(\d+)", v)
                if m and int(m.group(1)) < 220:
                    return True
        return False

    def _parse_vision_json(self, raw: str) -> Dict[str, object]:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.replace("json", "", 1).strip()

        try:
            return json.loads(text)
        except Exception:
            pass

        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass

        return {
            "usable": False,
            "quality": 0.0,
            "relevance": 0.0,
            "reason": "vision_parse_failed",
            "has_logo": False,
            "has_watermark": False,
        }

    def _sharpness_score(self, data: bytes) -> float:
        if len(data) < 4:
            return 0.0
        diffs = []
        prev = data[0]
        for byte in data[1:30000]:
            diffs.append(abs(byte - prev))
            prev = byte
        if not diffs:
            return 0.0
        mean = sum(diffs) / len(diffs)
        variance = sum((d - mean) ** 2 for d in diffs) / len(diffs)
        return math.sqrt(variance)

    def _topic_terms(self, title: str, article_context: str = "") -> List[str]:
        blob = f"{title} {article_context[:500]}".lower()
        terms: List[str] = []
        for token in re.findall(r"[a-z0-9]{4,}", blob):
            if token in self.TITLE_STOPWORDS:
                continue
            if token not in terms:
                terms.append(token)
            if len(terms) >= 12:
                break
        return terms

    def _relevance_score(self, url: str, title: str, candidate_context: str = "", article_context: str = "") -> float:
        terms = self._topic_terms(title, article_context)
        if not terms:
            return 0.1

        url_low = url.lower()
        ctx_low = (candidate_context or "").lower()

        url_hits = sum(1 for t in terms if t in url_low)
        ctx_hits = sum(1 for t in terms if t in ctx_low)
        url_score = url_hits / max(1, len(terms))
        ctx_score = ctx_hits / max(1, min(len(terms), 8))

        score = 0.08 + (url_score * 0.55) + (ctx_score * 0.37)
        if ctx_low and any(tok in ctx_low for tok in self.NEGATIVE_CONTEXT_TOKENS):
            score -= 0.25

        return max(0.0, min(1.0, score))

    def _store_image(self, data: bytes, title: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9\s-]", "", title)[:40].strip().replace(" ", "_") or "image"
        path = self.download_dir / f"{safe}.jpg"
        path.write_bytes(data)
        return str(path.resolve())






















