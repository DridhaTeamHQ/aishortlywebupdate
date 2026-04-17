"""
Create the published_articles table in Supabase.

Run once:
    python scripts/create_supabase_table.py
"""
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")
    raise SystemExit(1)

from supabase import create_client  # type: ignore

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

DDL = """
CREATE TABLE IF NOT EXISTS published_articles (
    id          BIGSERIAL PRIMARY KEY,
    url         TEXT        NOT NULL UNIQUE,
    title       TEXT,
    story_key   TEXT,
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_published_articles_url
    ON published_articles (url);

CREATE INDEX IF NOT EXISTS idx_published_articles_story_key
    ON published_articles (story_key)
    WHERE story_key IS NOT NULL AND story_key <> '';

CREATE INDEX IF NOT EXISTS idx_published_articles_published_at
    ON published_articles (published_at DESC);
"""

try:
    sb.rpc("query", {"query": DDL}).execute()
    print("Table created (via RPC).")
except Exception:
    # Supabase JS client exposes `pg_query`; fall back to REST SQL endpoint.
    import httpx

    resp = httpx.post(
        f"{SUPABASE_URL}/rest/v1/rpc/query",
        headers={
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json",
        },
        json={"query": DDL},
        timeout=30,
    )
    if resp.status_code in (200, 204):
        print("Table created (via REST RPC).")
    else:
        # Most likely the RPC doesn't exist; print raw DDL for user to run in SQL editor
        print("\nAutomatic table creation not available via RPC.")
        print("Please run the following SQL in your Supabase SQL editor:\n")
        print(DDL)
