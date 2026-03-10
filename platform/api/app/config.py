import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    redis_url: str
    encryption_key: str
    rq_queue_name: str
    browser_stream_base_url: str


def get_settings() -> Settings:
    return Settings(
        supabase_url=os.getenv("SUPABASE_URL", "").strip(),
        supabase_anon_key=os.getenv("SUPABASE_ANON_KEY", "").strip(),
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0").strip(),
        encryption_key=os.getenv("PLATFORM_ENCRYPTION_KEY", "").strip(),
        rq_queue_name=os.getenv("RQ_QUEUE_NAME", "agent-runs").strip(),
        browser_stream_base_url=os.getenv("BROWSER_STREAM_BASE_URL", "http://localhost:8090").strip(),
    )
