"""Supabase OAuth 2.1 auth + per-tenant runtime binding (Phase B).

Wires FastMCP's ``SupabaseProvider`` (OAuth discovery + JWT/JWKS verification)
onto the server, and adds a middleware that — per tool call — pins the
connector's ``client_id`` and binds the caller's tenant config via
``use_runtime`` so tools run as that Supabase user.

Env:
  ADLOOP_SUPABASE_URL             Supabase project URL, e.g. https://<ref>.supabase.co
  ADLOOP_BASE_URL                 this server's public URL (its OAuth resource id)
  ADLOOP_JWT_ALGORITHM            "ES256" (default) or "RS256"
  ADLOOP_ALLOWED_REDIRECT_URIS    connector redirect_uri allowlist, comma-separated
  ADLOOP_ALLOW_LOCALHOST_REDIRECT "on" to also accept http://localhost:<port>/...
  ADLOOP_EXPECTED_CLIENT_ID       LEGACY exact client_id pin (see below)

Why redirect_uri and not client_id
----------------------------------
Supabase's OAuth server uses *dynamic* client registration, so every user who
adds this connector gets their OWN client_id. Pinning ADLOOP_EXPECTED_CLIENT_ID
to one value therefore rejects everybody except the single user whose id is in
the env var, and keeping it current means a Cloud Run redeploy per person. It
does not scale and it is not what the pin is for.

What IS stable across those registrations is the connector's redirect_uri —
every claude.ai registration carries https://claude.ai/api/mcp/auth_callback.
It is also the field that actually carries the security: to steal a token an
attacker needs the authorization code delivered somewhere they control, and an
allowlisted redirect sends it to claude.ai instead.

Deliberately NOT client_name: that is self-declared at registration time, so
anyone able to reach the open registration endpoint could register a client
named "Claude" and pass a name check.

Everything is env-gated: if Supabase isn't configured the builders return
None so the transport still boots for local dev (unauthenticated — never
expose that).
"""

from __future__ import annotations

import logging
import os
import re
from time import monotonic
from typing import Any, Callable

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware

from adloop.hosting.datastore import ConnectionProvider, build_connection_provider
from adloop.hosting.tenant_config import build_tenant_config
from adloop.runtime import use_runtime

log = logging.getLogger("adloop.hosting.auth")

# Given a token's client_id, return True if that connector is trusted.
ClientCheck = Callable[[str], bool]

_LOCALHOST_REDIRECT = re.compile(r"^http://(?:localhost|127\.0\.0\.1):\d{1,5}(?:/|$)")

# SECURITY DEFINER lookup owned by the ClientBrain half, so this server's DB
# role never needs read access to the auth schema.
_REDIRECT_SQL = "select gads.oauth_client_redirect_uris(%s) as uris"


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


def expected_client_ids() -> frozenset[str]:
    """Connector client_id(s) to accept, parsed from comma-separated
    ``ADLOOP_EXPECTED_CLIENT_ID``. Empty set means pinning is off.

    Accepting a list lets a cutover add the new connector's id alongside the
    current one (append, don't swap), so re-pinning never leaves a window where
    a live connector is rejected.
    """
    raw = os.environ.get("ADLOOP_EXPECTED_CLIENT_ID", "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def allowed_redirect_uris() -> frozenset[str]:
    """Exact connector redirect_uris to accept, from ``ADLOOP_ALLOWED_REDIRECT_URIS``."""
    raw = os.environ.get("ADLOOP_ALLOWED_REDIRECT_URIS", "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def localhost_redirect_allowed() -> bool:
    """Whether to also accept a loopback redirect (Claude Code CLI picks a random port).

    Off by default. A loopback redirect can only deliver the authorization code
    to the caller's own machine, so this is a weak allowance — but it is still
    an allowance, so prod should leave it off.
    """
    return os.environ.get("ADLOOP_ALLOW_LOCALHOST_REDIRECT", "").strip().lower() == "on"


class RedirectUriClientCheck:
    """Trust a connector when every redirect_uri it registered is allowlisted.

    Stable across dynamic registration: a new user means a new client_id but the
    same redirect_uri, so the allowlist never needs touching. Results are cached
    for ``ttl`` seconds so this costs one DB round trip per connector, not one
    per tool call.

    Fails CLOSED: a lookup error propagates rather than returning True, and an
    unknown client_id (no row -> NULL -> no uris) is refused.
    """

    def __init__(
        self,
        connect: ConnectionProvider,
        allowed: "frozenset[str]",
        *,
        allow_localhost: bool = False,
        ttl: float = 300.0,
    ) -> None:
        self._connect = connect
        self._allowed = allowed
        self._allow_localhost = allow_localhost
        self._ttl = ttl
        self._cache: dict[str, tuple[bool, float]] = {}

    def _permitted(self, uri: str) -> bool:
        if uri in self._allowed:
            return True
        return self._allow_localhost and bool(_LOCALHOST_REDIRECT.match(uri))

    def __call__(self, client_id: str) -> bool:
        cached = self._cache.get(client_id)
        if cached is not None and cached[1] > monotonic():
            return cached[0]

        with self._connect() as conn:
            row = conn.execute(_REDIRECT_SQL, (client_id,)).fetchone()
        raw = row.get("uris") if isinstance(row, dict) else (row[0] if row else None)
        uris = [part.strip() for part in (raw or "").split(",") if part.strip()]

        # An unregistered client_id yields no uris; refuse rather than treating
        # "nothing to check" as "nothing objectionable".
        ok = bool(uris) and all(self._permitted(uri) for uri in uris)
        if not ok:
            log.warning(
                "Refusing connector client_id=%s: redirect_uris %r are not allowlisted.",
                client_id, uris,
            )
        self._cache[client_id] = (ok, monotonic() + self._ttl)
        return ok


def build_client_check() -> "ClientCheck | frozenset[str]":
    """Pick the connector check: redirect_uri allowlist, else the legacy id pin.

    Returns an empty frozenset when neither is configured, which means "no
    pinning" (dev only) and is what ``install_auth`` warns about.
    """
    allowed = allowed_redirect_uris()
    if allowed:
        connect = build_connection_provider()
        if connect is not None:
            return RedirectUriClientCheck(
                connect, allowed, allow_localhost=localhost_redirect_allowed()
            )
        log.warning(
            "ADLOOP_ALLOWED_REDIRECT_URIS is set but ADLOOP_DATABASE_URL is not, so the "
            "connector's redirect_uri cannot be looked up. Falling back to "
            "ADLOOP_EXPECTED_CLIENT_ID."
        )
    return expected_client_ids()


def resolve_tenant(token: Any, expected: "frozenset[str] | ClientCheck | None") -> str:
    """Enforce the connector check and return the tenant id from a verified token.

    Pure/decoupled from FastMCP request plumbing so it can be unit-tested.

    * ``token`` is the verified ``AccessToken`` (or None if unauthenticated).
    * ``expected`` is either a callable taking the token's client_id (the
      redirect_uri check), or a set of exact client_ids (the legacy pin), or
      empty/None to skip the check entirely (dev only).

    Raises :class:`ToolError` (fail-closed) on any problem. Supabase does not
    support RFC 8707 resource indicators, so a token minted for a *different*
    connector would still verify against this server; this check is what stops
    it.
    """
    if token is None:
        raise ToolError("Unauthenticated: no verified access token on this request.")

    if expected:
        client_id = getattr(token, "client_id", None)
        if callable(expected):
            permitted = bool(client_id) and expected(client_id)
        else:
            permitted = client_id in expected
        if not permitted:
            raise ToolError("Access token was not issued for this connector.")

    tenant = getattr(token, "subject", None) or (getattr(token, "claims", None) or {}).get("sub")
    if not tenant:
        raise ToolError("Access token has no subject (Supabase user id) to key the tenant.")
    return str(tenant)


class TenantContextMiddleware(Middleware):
    """Pin the connector and bind the caller's tenant config per tool call.

    Runs after ``SupabaseProvider`` has verified the JWT. Reads the verified
    ``AccessToken`` from the request context, enforces the connector check,
    resolves the tenant (Supabase user id), builds that tenant's config, and
    executes the tool inside ``use_runtime(config, tenant=user_id)``.
    """

    def __init__(self, expected_client_ids: "frozenset[str] | ClientCheck | None") -> None:
        self._expected = expected_client_ids

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
    pinned = build_client_check()
    mcp.add_middleware(TenantContextMiddleware(pinned))
    if callable(pinned):
        log.info("Connector pinning: redirect_uri allowlist (scales across dynamic registration).")
    elif not pinned:
        log.warning(
            "Neither ADLOOP_ALLOWED_REDIRECT_URIS nor ADLOOP_EXPECTED_CLIENT_ID "
            "is set — connector pinning is "
            "OFF. A token minted for another Supabase OAuth connector would be "
            "accepted. Set it before production."
        )
    log.info("Supabase auth + tenant middleware installed.")
    return True
