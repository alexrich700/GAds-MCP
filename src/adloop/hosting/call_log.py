"""Per-call tool log for the Client Brain engagement report.

Every tool call through the hosted server appends one row to
``public.mcp_tool_calls`` in the shared Client Brain Supabase Postgres (the
table is created by the Client Brain migration
``20260929120000_engagement_report.sql``): who called (the tenant's Supabase
auth user id), which tool, whether it worked and how long it took. Never the
arguments or the result.

The insert runs on a worker thread after the call returns and swallows its own
errors, so logging can never slow down or fail a tool. Without
``ADLOOP_DATABASE_URL`` (local dev) nothing is installed.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from fastmcp.server.middleware import Middleware

from adloop.hosting.datastore import ConnectionProvider, build_connection_provider

log = logging.getLogger("adloop.hosting.call_log")

_INSERT = """
    insert into public.mcp_tool_calls
        (server, auth_user_id, tool_name, ok, duration_ms)
    values ('google_ads', %s::uuid, %s, %s, %s)
"""


def _tenant_from_token(token: Any) -> str | None:
    """The Supabase auth user id on the verified token, if it is a UUID."""
    if token is None:
        return None
    subject = getattr(token, "subject", None) or (getattr(token, "claims", None) or {}).get("sub")
    if not subject:
        return None
    try:
        return str(uuid.UUID(str(subject)))
    except ValueError:
        return None


class SupabaseCallLog:
    """Writes one ``mcp_tool_calls`` row per call."""

    def __init__(self, connect: ConnectionProvider) -> None:
        self._connect = connect

    def record(self, tenant: str, tool_name: str, ok: bool, duration_ms: int) -> None:
        try:
            with self._connect() as conn:
                conn.execute(_INSERT, (tenant, tool_name[:120], ok, max(0, int(duration_ms))))
        except Exception as exc:  # noqa: BLE001 - telemetry must never surface
            log.warning("mcp call log insert failed: %s", exc)


class CallLogMiddleware(Middleware):
    """Times each tool call and records it after it returns."""

    def __init__(self, sink: SupabaseCallLog) -> None:
        self._sink = sink

    async def on_call_tool(self, context, call_next):  # noqa: ANN001 - FastMCP types
        from fastmcp.server.dependencies import get_access_token

        try:
            tenant = _tenant_from_token(get_access_token())
        except Exception:  # noqa: BLE001 - no request context (tests, stdio)
            tenant = None
        name = str(getattr(getattr(context, "message", None), "name", "") or "unknown")
        started = time.monotonic()
        ok = False
        try:
            result = await call_next(context)
            ok = not bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
            return result
        finally:
            if tenant:
                elapsed = int((time.monotonic() - started) * 1000)
                try:
                    loop = asyncio.get_running_loop()
                    loop.run_in_executor(None, self._sink.record, tenant, name, ok, elapsed)
                except RuntimeError:
                    self._sink.record(tenant, name, ok, elapsed)


def install_call_log(mcp, connect: ConnectionProvider | None = None) -> bool:  # noqa: ANN001 - FastMCP server
    """Attach the call-log middleware if a database is configured."""
    connect = connect or build_connection_provider()
    if connect is None:
        return False
    mcp.add_middleware(CallLogMiddleware(SupabaseCallLog(connect)))
    log.info("MCP tool call log installed (public.mcp_tool_calls).")
    return True
