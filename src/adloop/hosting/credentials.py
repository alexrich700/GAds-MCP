"""Per-user Google credentials provider for server mode (Phase C).

Implements the ``CredentialsProvider`` protocol from :mod:`adloop.auth` and is
installed with ``set_credentials_provider`` — the exact seam the merged
upstream exposes for a hosted deployment.

In server mode every request is bound to a tenant (Supabase user id) via
``use_runtime`` (Phase B middleware). This provider reads that tenant's stored
Google OAuth **refresh token** and returns
``google.oauth2.credentials.Credentials``; the Google client libraries refresh
it (refresh_token -> access_token) on use. The developer token, MCC, and
customer id come from the tenant's ``AdLoopConfig`` (not from here).

v1 scope is **Ads + GA4 only** — ``gtm/gsc/merchant_credentials`` are
intentionally absent so upstream's capability check reports them unsupported
until their scopes are added.

The token source is injectable so Phase E can drop in the real backend without
touching this class:
  * default: env ``ADLOOP_DEV_REFRESH_TOKEN`` (single-user LOCAL DEV ONLY)
  * Phase E: a callable that reads + decrypts this user's refresh token from
    the ``gads.google_ad_credentials`` table.

Shared Web OAuth client (server-side secret) from env:
  ADLOOP_GOOGLE_CLIENT_ID
  ADLOOP_GOOGLE_CLIENT_SECRET
"""

from __future__ import annotations

import os
from typing import Callable

from google.oauth2.credentials import Credentials

from adloop.config import AdLoopConfig
from adloop.runtime import current_tenant

ADWORDS_SCOPE = "https://www.googleapis.com/auth/adwords"
GA4_READONLY_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
_TOKEN_URI = "https://oauth2.googleapis.com/token"

# tenant id (Supabase user id) -> refresh token (or None if not connected)
TokenLookup = Callable[[str], "str | None"]


class MissingGoogleConnection(RuntimeError):
    """Raised when the tenant has no stored Google refresh token."""


def _default_token_lookup(tenant_id: str) -> str | None:
    """LOCAL-DEV single-user fallback. Phase E injects a real per-tenant lookup."""
    return os.environ.get("ADLOOP_DEV_REFRESH_TOKEN", "").strip() or None


class SupabaseCredentialsProvider:
    """Mints per-user Google credentials from the tenant's stored refresh token."""

    def __init__(self, token_lookup: TokenLookup | None = None) -> None:
        self._lookup = token_lookup or _default_token_lookup

    # --- CredentialsProvider protocol (v1: Ads + GA4) -------------------
    def ads_credentials(self, config: AdLoopConfig) -> Credentials:
        return self._build([ADWORDS_SCOPE])

    def ga4_credentials(self, config: AdLoopConfig) -> Credentials:
        return self._build([GA4_READONLY_SCOPE])

    # gtm/gsc/merchant_credentials intentionally omitted for v1 — upstream's
    # hasattr capability check turns a call into a clear "does not support"
    # error rather than a leak or AttributeError.

    # --- internals ------------------------------------------------------
    def _build(self, scopes: list[str]) -> Credentials:
        tenant = current_tenant()
        refresh_token = self._lookup(tenant)
        if not refresh_token:
            raise MissingGoogleConnection(
                "This user hasn't connected a Google Ads account yet. Connect it "
                "in MotiventOS ('Connect Google Ads'), then try again."
            )

        client_id = os.environ.get("ADLOOP_GOOGLE_CLIENT_ID", "").strip()
        client_secret = os.environ.get("ADLOOP_GOOGLE_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise RuntimeError(
                "Server is missing ADLOOP_GOOGLE_CLIENT_ID / "
                "ADLOOP_GOOGLE_CLIENT_SECRET (the shared Web OAuth client)."
            )

        # token=None -> refreshed from refresh_token on first use.
        return Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=_TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
        )


def install_credentials_provider(token_lookup: TokenLookup | None = None) -> None:
    """Install the Supabase credentials provider (required in server mode).

    In server mode the default ``LocalFileCredentialsProvider`` refuses to run,
    so a hosted process must install this before any tool executes.
    """
    from adloop.auth import set_credentials_provider

    set_credentials_provider(SupabaseCredentialsProvider(token_lookup))
