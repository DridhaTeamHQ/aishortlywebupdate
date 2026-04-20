"""
Memory Module - SQLite blacklist + Supabase cloud dedup.

Tracks:
- Processed URLs (success / failed / blacklisted) in local SQLite
- Published article URLs and story keys in Supabase `published_articles`
  table so dedup survives worker restarts and cross-deploy scenarios.
"""

import os
import sqlite3
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from utils.logger import get_logger


def _make_supabase_client():
    """Return a Supabase client if credentials are available, else None."""
    try:
        url = (os.getenv("SUPABASE_URL") or "").strip()
        key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        if not url or not key:
            return None
        from supabase import create_client  # type: ignore
        return create_client(url, key)
    except Exception:
        return None


class AgentMemory:
    """
    Persistent memory for the agent.

    Database: core/memory/agent.db (local SQLite – fast in-run guard)
    Remote:   Supabase published_articles table (cross-run / cross-deploy guard)
    """

    DB_PATH = Path(__file__).resolve().parent / "agent.db"
    _SUPABASE_TABLE = "published_articles"

    def __init__(self):
        self.logger = get_logger("memory")
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._sb = _make_supabase_client()
        if self._sb:
            self.logger.info("Supabase cloud dedup enabled")
        else:
            self.logger.error(
                "Supabase cloud dedup UNAVAILABLE — articles will repeat across restarts. "
                "Check SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and ensure the "
                "'published_articles' table exists."
            )

    # ── Supabase cloud dedup ────────────────────────────────────────────────

    def is_published_in_supabase(self, url: str) -> bool:
        """Return True if this URL was already published (cloud check)."""
        if not self._sb:
            return False
        normalized = self._normalize_url(url)
        if not normalized:
            return False
        try:
            result = (
                self._sb.table(self._SUPABASE_TABLE)
                .select("id")
                .eq("url", normalized)
                .limit(1)
                .execute()
            )
            return bool((result.data or []))
        except Exception as exc:
            self.logger.warning(f"Supabase dedup check failed (url): {exc}")
            return False

    def is_story_published_in_supabase(self, story_key: str, within_hours: int = 48) -> bool:
        """Return True if this story key was published recently (cloud check)."""
        if not self._sb or not (story_key or "").strip():
            return False
        try:
            from datetime import timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=within_hours)).isoformat()
            result = (
                self._sb.table(self._SUPABASE_TABLE)
                .select("id")
                .eq("story_key", story_key.strip())
                .gte("published_at", cutoff)
                .limit(1)
                .execute()
            )
            return bool((result.data or []))
        except Exception as exc:
            self.logger.warning(f"Supabase dedup check failed (story_key): {exc}")
            return False

    def mark_published_in_supabase(self, url: str, title: str = "", story_key: str = "", image_url: str = "") -> None:
        """Record a successful publish to Supabase so future runs skip it."""
        if not self._sb:
            return
        normalized = self._normalize_url(url)
        if not normalized:
            return
        try:
            self._sb.table(self._SUPABASE_TABLE).upsert(
                {
                    "url": normalized,
                    "title": (title or "").strip()[:512],
                    "story_key": (story_key or "").strip()[:64],
                    "image_url": (image_url or "").strip()[:1024],
                    "published_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="url",
            ).execute()
            self.logger.info(f"Supabase dedup recorded: {normalized[:60]}")
        except Exception as exc:
            self.logger.warning(f"Supabase dedup write failed: {exc}")

    def is_image_used_in_supabase(self, image_url: str) -> bool:
        """Return True if this image URL was already used in a published article."""
        if not self._sb or not (image_url or "").strip():
            return False
        normalized = (image_url or "").strip()
        try:
            result = (
                self._sb.table(self._SUPABASE_TABLE)
                .select("id")
                .eq("image_url", normalized)
                .limit(1)
                .execute()
            )
            return bool((result.data or []))
        except Exception as exc:
            self.logger.warning(f"Supabase image dedup check failed: {exc}")
            return False

    # ── Local SQLite ────────────────────────────────────────────────────────

    def _get_conn(self):
        return sqlite3.connect(self.DB_PATH)

    def _init_db(self):
        """Initialize database table."""
        self._migrate_legacy_db_if_needed()
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS article_history (
                        url TEXT PRIMARY KEY,
                        status TEXT NOT NULL,  -- 'success', 'failed', 'blacklisted'
                        reason TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS story_history (
                        story_key TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        title TEXT,
                        url TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.commit()
        except Exception as exc:
            self.logger.error(f"DB init failed: {exc}")

    def _migrate_legacy_db_if_needed(self):
        """
        If an older run created DB at cwd-relative path, migrate it once.
        This keeps dedupe history across launch-directory changes.
        """
        try:
            legacy = (Path.cwd() / "core" / "memory" / "agent.db").resolve()
            current = self.DB_PATH.resolve()
            if legacy == current:
                return
            if current.exists():
                return
            if not legacy.exists():
                return
            current.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, current)
            self.logger.info(f"Migrated legacy memory DB from {legacy} to {current}")
        except Exception as exc:
            self.logger.warning(f"Legacy memory DB migration skipped: {exc}")

    def is_processed(self, url: str) -> bool:
        """Check if URL has been processed (success or failure)."""
        normalized = self._normalize_url(url)
        if not normalized:
            return False
        try:
            with self._get_conn() as conn:
                cursor = conn.execute("SELECT 1 FROM article_history WHERE url = ?", (normalized,))
                return cursor.fetchone() is not None
        except Exception:
            return False

    def is_success(self, url: str) -> bool:
        """Check if URL was successfully published (local + Supabase)."""
        normalized = self._normalize_url(url)
        if not normalized:
            return False
        # Fast local check first
        try:
            with self._get_conn() as conn:
                cursor = conn.execute(
                    "SELECT 1 FROM article_history WHERE url = ? AND status = 'success'",
                    (normalized,),
                )
                if cursor.fetchone() is not None:
                    return True
        except Exception:
            pass
        # Cloud check for cross-run dedup
        return self.is_published_in_supabase(url)

    def is_recent_failure(self, url: str, within_minutes: int = 360) -> bool:
        """True if URL failed recently; used to avoid retry loops across runs."""
        normalized = self._normalize_url(url)
        if not normalized:
            return False
        try:
            with self._get_conn() as conn:
                cursor = conn.execute(
                    """
                    SELECT 1
                    FROM article_history
                    WHERE url = ?
                      AND status = 'failed'
                      AND timestamp >= datetime('now', ?)
                    """,
                    (normalized, f"-{int(within_minutes)} minutes"),
                )
                return cursor.fetchone() is not None
        except Exception:
            return False

    def mark_success(self, url: str):
        """Mark URL as successfully processed (locally)."""
        self._record(url, "success")

    def is_story_success(self, story_key: str, within_hours: int = 48) -> bool:
        """Check if a story fingerprint was already published recently."""
        key = (story_key or "").strip()
        if not key:
            return False
        # Local check
        try:
            with self._get_conn() as conn:
                cursor = conn.execute(
                    """
                    SELECT 1
                    FROM story_history
                    WHERE story_key = ?
                      AND status = 'success'
                      AND timestamp >= datetime('now', ?)
                    """,
                    (key, f"-{int(within_hours)} hours"),
                )
                if cursor.fetchone() is not None:
                    return True
        except Exception:
            pass
        # Cloud check
        return self.is_story_published_in_supabase(key, within_hours=within_hours)

    def mark_story_success(self, story_key: str, url: str = "", title: str = ""):
        """Mark a story fingerprint as successfully published."""
        key = (story_key or "").strip()
        if not key:
            return
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO story_history (story_key, status, title, url, timestamp)
                    VALUES (?, 'success', ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (key, (title or "").strip(), (url or "").strip()),
                )
                conn.commit()
            self.logger.info(f"Memory story: {key[:24]} -> success")
        except Exception as exc:
            self.logger.error(f"Failed to record story memory: {exc}")

    def mark_failed(self, url: str, reason: str):
        """Mark URL as failed."""
        self._record(url, "failed", reason)

    def blacklist(self, url: str, reason: str):
        """Blacklist a URL (skip forever)."""
        self._record(url, "blacklisted", reason)

    def _record(self, url: str, status: str, reason: str = None):
        """Record status in DB."""
        normalized = self._normalize_url(url)
        if not normalized:
            return
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO article_history (url, status, reason, timestamp)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (normalized, status, reason),
                )
                conn.commit()
            self.logger.info(f"Memory: {normalized[:50]} -> {status}")
        except Exception as exc:
            self.logger.error(f"Failed to record memory: {exc}")

    def _normalize_url(self, url: str) -> str:
        raw = (url or "").strip()
        if not raw:
            return ""
        try:
            parsed = urlparse(raw)
            if not parsed.scheme or not parsed.netloc:
                return raw
            scheme = parsed.scheme.lower()
            netloc = parsed.netloc.lower()
            path = parsed.path or "/"
            if path != "/" and path.endswith("/"):
                path = path.rstrip("/")
            return urlunparse((scheme, netloc, path, "", "", ""))
        except Exception:
            return raw
