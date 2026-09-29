"""Tests for the engagement-report tool call log (public.mcp_tool_calls)."""

import asyncio
import types
from contextlib import contextmanager

import pytest

from adloop.hosting.call_log import CallLogMiddleware, SupabaseCallLog, install_call_log

TENANT = "0b6f3f0e-6a53-4c2f-9f5a-2a1c1f6c9d10"


class _FakeDB:
    def __init__(self, fail=False):
        self.rows = []
        self.fail = fail

    @contextmanager
    def connect(self):
        db = self

        class _Conn:
            def execute(self, sql, params):
                if db.fail:
                    raise RuntimeError("db down")
                assert "insert into public.mcp_tool_calls" in sql
                db.rows.append(params)

        yield _Conn()


def _context(name="get_campaign_performance"):
    return types.SimpleNamespace(message=types.SimpleNamespace(name=name))


def _run(middleware, call_next, monkeypatch, subject=TENANT):
    token = types.SimpleNamespace(subject=subject, claims={})
    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: token)

    async def go():
        try:
            return await middleware.on_call_tool(_context(), call_next)
        finally:
            # Let the executor-thread insert finish.
            await asyncio.sleep(0.05)

    return asyncio.run(go())


def test_records_a_successful_call(monkeypatch):
    db = _FakeDB()
    mw = CallLogMiddleware(SupabaseCallLog(db.connect))

    async def call_next(_ctx):
        return types.SimpleNamespace(is_error=False)

    _run(mw, call_next, monkeypatch)
    assert len(db.rows) == 1
    tenant, tool, ok, duration = db.rows[0]
    assert (tenant, tool, ok) == (TENANT, "get_campaign_performance", True)
    assert duration >= 0


def test_records_a_failed_call_and_reraises(monkeypatch):
    db = _FakeDB()
    mw = CallLogMiddleware(SupabaseCallLog(db.connect))

    async def call_next(_ctx):
        raise ValueError("boom")

    with pytest.raises(ValueError):
        _run(mw, call_next, monkeypatch)
    assert db.rows and db.rows[0][2] is False


def test_skips_calls_without_a_uuid_tenant(monkeypatch):
    db = _FakeDB()
    mw = CallLogMiddleware(SupabaseCallLog(db.connect))

    async def call_next(_ctx):
        return types.SimpleNamespace(is_error=False)

    _run(mw, call_next, monkeypatch, subject="not-a-uuid")
    assert db.rows == []


def test_a_failing_insert_never_fails_the_tool(monkeypatch):
    db = _FakeDB(fail=True)
    mw = CallLogMiddleware(SupabaseCallLog(db.connect))

    async def call_next(_ctx):
        return "result"

    assert _run(mw, call_next, monkeypatch) == "result"


def test_install_needs_a_database(monkeypatch):
    monkeypatch.delenv("ADLOOP_DATABASE_URL", raising=False)
    added = []
    mcp = types.SimpleNamespace(add_middleware=added.append)
    assert install_call_log(mcp) is False
    assert install_call_log(mcp, connect=_FakeDB().connect) is True
    assert isinstance(added[0], CallLogMiddleware)

