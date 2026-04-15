from functools import lru_cache
from supabase import Client, create_client

from .config import get_settings


@lru_cache(maxsize=1)
def get_supabase_admin() -> Client:
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_service_role_key)


@lru_cache(maxsize=1)
def get_supabase_anon() -> Client:
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_anon_key)
