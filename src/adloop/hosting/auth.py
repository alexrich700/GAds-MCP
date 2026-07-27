"""Supabase OAuth 2.1 auth + per-tenant runtime binding (Phase B).

Wires FastMCP's ``SupabaseProvider`` (OAuth discovery + JWT/JWKS verification)
onto the server, and adds a middleware that — per tool call — pins the
connector's ``client_id`` and binds the caller's tenant config via
``use_runtime`` so tools run as that Supabase user.

Env (placeholders until the Phase-0 admin items land):
  ADLOOP_SUPABASE_URL        Supabase project URL, e.g. https://<ref>.supabase.co
  ADLOOP_BASE_URL            this server's public URL (its OAuth resource id)
  ADLOOP_JWT_ALGORITHM       "ES256" (default) or "RS256"
  ADLOOP_EXPECTED_CLIENT_ID  connector client_id to pin (fail-closed)

Everything is env-gated: if Supabase isn't configured the builders return
None so the transport still boots for local dev (unauthenticated — never
expose that).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware

from adloop.hosting.tenant_config import build_tenant_config
from adloop.runtime import use_runtime

log = logging.getLogger("adloop.hosting.auth")


def build_supabase_auth() -> Any | None:
    """Return a configured ``SupabaseProvider``, or ``None`` if env is incomplete.

    Constructing the provider is cheap (no network) — JWKS is fetched lazily on
    first token verification.
    """
    project_url = os.environ.get("ADLOOP_SUPABASE_URL", "").strip()
    base_url = os.environ.get("ADLOOP_BASE_URL", "").strip()
    if not project_url or not base_url:
        return None

    from fastmcp.server.auth.providers.supabase import SupabaseProvider

    algorithm = os.environ.get("ADLOOP_JWT_ALGORITHM", "ES256").strip() or "ES256"
    return SupabaseProvider(
        project_url=project_url,
        base_url=base_url,
        algorithm=algorithm,  # type: ignore[arg-type]
    )


def expected_client_id() -> str | None:
    """The connector client_id to pin, or None if pinning isn't configured."""
    return os.environ.get("ADLOOP_EXPECTED_CLIENT_ID", "").strip() or None


def resolve_tenant(token: Any, expected: str | None) -> str:
    """Enforce the pinned client_id and return the tenant id from a verified token.

    Pure/decoupled from FastMCP request plumbing so it can be unit-tested.

    * ``token`` is the verified ``AccessToken`` (or None if unauthenticated).
    * ``expected`` is the connector client_id to require, or None to skip
      pinning (dev only).

    Raises :class:`ToolError` (fail-closed) on any problem. Supabase does not
    support RFC 8707 resource indicators, so a token minted for a *different*
    connector would still verify against this server — the client_id pin is
    what stops it.
    """
    if token is None:
        raise ToolError("Unauthenticated: no verified access token on this request.")

    if expected and getattr(token, "client_id", None) != expected:
        raise ToolError("Access token was not issued for this connector.")

    tenant = getattr(token, "subject", None) or (getattr(token, "claims", None) or {}).get("sub")
    if not tenant:
        raise ToolError("Access token has no subject (Supabase user id) to key the tenant.")
    return str(tenant)


class TenantContextMiddleware(Middleware):
    """Pin the connector and bind the caller's tenant config per tool call.

    Runs after ``SupabaseProvider`` has verified the JWT. Reads the verified
    ``AccessToken`` from the request context, enforces the pinned client_id,
    resolves the tenant (Supabase user id), builds that tenant's config, and
    executes the tool inside ``use_runtime(config, tenant=user_id)``.
    """

    def __init__(self, expected_client_id: str | None) -> None:
        self._expected = expected_client_id

    async def on_call_tool(self, context, call_next):  # noqa: ANN001 - FastMCP types
        from fastmcp.server.dependencies import get_access_token

        tenant = resolve_tenant(get_access_token(), self._expected)
        config = build_tenant_config(tenant)
        with use_runtime(config, tenant=tenant):
            return await call_next(context)


def install_auth(mcp) -> bool:  # noqa: ANN001 - FastMCP server
    """Attach Supabase auth + the tenant middleware to ``mcp`` if configured.

    Returns True if auth was installed, False if running unauthenticated
    (env not set — local dev only). Must be called BEFORE ``mcp.http_app()``
    since the OAuth routes are built from ``mcp.auth`` at that point.
    """
    provider = build_supabase_auth()
    if provider is None:
        log.warning(
            "ADLOOP_SUPABASE_URL / ADLOOP_BASE_URL not set — starting the HTTP "
            "server UNAUTHENTICATED. This is for local dev only; never expose it."
        )
        return False

    mcp.auth = provider
    mcp.add_middleware(TenantContextMiddleware(expected_client_id()))
    if expected_client_id() is None:
        log.warning(
            "ADLOOP_EXPECTED_CLIENT_ID not set — connector client_id pinning is "
            "OFF. A token minted for another Supabase OAuth connector would be "
            "accepted. Set it before production."
        )
    log.info("Supabase auth + tenant middleware installed.")
    return True
