"""The API's Supabase client, for server-side writes.

It is built from the secret key, which bypasses Row Level Security, so it must
never leave the server. One client is created on first use and shared.
"""

from functools import lru_cache

from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

from services.core.config import get_settings

# The library waits two minutes by default; an ingest request should give up
# long before the browser does, so the frontend can retry.
POSTGREST_TIMEOUT_SECONDS = 10


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    settings = get_settings()
    return create_client(
        settings.supabase_url,
        settings.supabase_secret_key.get_secret_value(),
        options=SyncClientOptions(postgrest_client_timeout=POSTGREST_TIMEOUT_SECONDS),
    )
