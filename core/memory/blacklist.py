"""
Memory Module - Simple SQLite blacklist to prevent duplicate processing.

Tracks:
- Processed URLs (success)
- Failed URLs (with reason)
"""

import sqlite3
import shutil
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from utils.logger import get_logger


class AgentMemory:
    """
    Persistent memory for the agent.

    Database: core/memory/agent.db
    Table: article_history
    """

    DB_PATH = Path(__file__).resolve().parent / "agent.db"

    def __init__(self):
        self.logger = get_logger("memory")
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

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
        """Check if URL was successfully published."""
        normalized = self._normalize_url(url)
        if not normalized:
            return False
        try:
            with self._get_conn() as conn:
                cursor = conn.execute(
                    "SELECT 1 FROM article_history WHERE url = ? AND status = 'success'",
                    (normalized,),
                )
                return cursor.fetchone() is not None
        except Exception:
            return False

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
        """Mark URL as successfully processed."""
        self._record(url, "success")

    def is_story_success(self, story_key: str, within_hours: int = 48) -> bool:
        """Check if a story fingerprint was already published recently."""
        key = (story_key or "").strip()
        if not key:
            return False
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
                return cursor.fetchone() is not None
        except Exception:
            return False

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
