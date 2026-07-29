"""Supabase-backed per-user Google refresh-token lookup (Phase E, server side).

This is the real ``TokenLookup`` that replaces Phase C's env-var dev fallback
(``ADLOOP_DEV_REFRESH_TOKEN``). Given the current tenant (Supabase user id /
``sub``), it fetches that user's **decrypted** Google Ads refresh token from the
shared Supabase Postgres and returns it; Phase C's provider then mints
``google.oauth2.credentials.Credentials`` from it against the Web OAuth client.

Reuses the Phase D connection pool (``ADLOOP_DATABASE_URL``) — no separate DB
config. Importing this module is side-effect-free (the pool is built lazily on
first call); the pool is only created when a lookup actually runs.

--------------------------------------------------------------------------------
CONTRACT with the ClientBrain "Connect Google Ads" half (Phase E, client side)
--------------------------------------------------------------------------------
ClientBrain captures each user's Google Ads refresh token via an OAuth flow and
stores it encrypted in **Supabase Vault**, exposing it through a
``security definer`` RPC so the token itself never lives in a readable column
(mirrors the existing google-calendar Vault pattern). This module calls exactly
that RPC:

    gads.get_ads_refresh_token(p_user_id uuid) RETURNS text

  * argument: the tenant's Supabase auth user id (``current_tenant()``)
  * returns: the decrypted refresh token, or NULL if the user hasn't connected
  * grants: EXECUTE to the role this server connects as (the pooler role)

If the RPC returns NULL / no row, this lookup returns ``None`` and Phase C
raises ``MissingGoogleConnection`` ("connect Google Ads first"). Per-client
``customer_id`` / GA4 property resolution is intentionally NOT here — the refresh
token is per-user, account ids are per-client (``public.google_ads_services``)
and are supplied per tool call.
"""

from __future__ import annotations

import logging
from typing import Any

from adloop.hosting.credentials import TokenLookup
from adloop.hosting.datastore import ConnectionProvider, build_connection_provider

log = logging.getLogger("adloop.hosting.token_lookup")

# Single-column RPC call. psycopg passes the tenant id as text; the function
# signature takes uuid, and Postgres coerces a well-formed uuid string.
_RPC_SQL = "select gads.get_ads_refresh_token(%s) as refresh_token"

# SQLSTATEs meaning the Phase E schema/RPC isn't provisioned yet (the ClientBrain
# migration hasn't been applied to this project). Treat these as "not connected"
# so the caller gets the guided MissingGoogleConnection rather than a raw DB
# error. Permission ('42501') and connection errors are deliberately NOT
# swallowed: those are real misconfiguration and must surface.
_NOT_PROVISIONED_SQLSTATES = frozenset({
    "42883",  # undefined_function (the RPC is missing)
    "42P01",  # undefined_table
    "3F000",  # invalid_schema_name (the gads schema is missing)
})


def _first_value(row: Any) -> Any:
    """Read the single selected column from a psycopg row (dict_row) or a tuple."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get("refresh_token")
    return row[0]


class SupabaseTokenLookup:
    """Per-user refresh-token lookup via the ``gads.get_ads_refresh_token`` RPC."""

    def __init__(self, connect: ConnectionProvider) -> None:
        self._connect = connect

    def __call__(self, tenant_id: str) -> str | None:
        try:
            with self._connect() as conn:
                row = conn.execute(_RPC_SQL, (tenant_id,)).fetchone()
        except Exception as exc:  # noqa: BLE001 - re-raised unless a known "not provisioned" state
            sqlstate = getattr(exc, "sqlstate", None)
            if sqlstate in _NOT_PROVISIONED_SQLSTATES:
                log.warning(
                    "gads.get_ads_refresh_token unavailable (sqlstate %s); treating "
                    "the tenant as unconnected. Is the ClientBrain google-ads "
                    "migration applied to this Supabase project?",
                    sqlstate,
                )
                return None
            raise
        token = _first_value(row)
        return token or None


def build_supabase_token_lookup() -> TokenLookup | None:
    """Return a Supabase-backed lookup, or ``None`` if no DB is configured.

    ``None`` lets ``install_credentials_provider`` fall back to Phase C's env-var
    dev lookup (local dev only). In a hosted deployment ``ADLOOP_DATABASE_URL``
    is set, so the real per-user lookup is used.
    """
    connect = build_connection_provider()
    if connect is None:
        log.warning(
            "ADLOOP_DATABASE_URL not set — per-user token lookup unavailable; "
            "falling back to the ADLOOP_DEV_REFRESH_TOKEN dev lookup (local dev only)."
        )
        return None
    return SupabaseTokenLookup(connect)
