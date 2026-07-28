"""Tests for the Phase E server-side per-user token lookup."""

from contextlib import contextmanager

from adloop.hosting.credentials import SupabaseCredentialsProvider
from adloop.hosting.token_lookup import (
    SupabaseTokenLookup,
    build_supabase_token_lookup,
)


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeDB:
    """Records the RPC call and returns a canned row (dict_row shape)."""

    def __init__(self, row):
        self._row = row
        self.calls = []

    @contextmanager
    def connect(self):
        yield self

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _FakeCursor(self._row)


def test_lookup_returns_token_for_tenant():
    db = _FakeDB({"refresh_token": "rt-123"})
    lookup = SupabaseTokenLookup(db.connect)
    assert lookup("user-abc") == "rt-123"
    # Calls the contract RPC with the tenant id.
    sql, params = db.calls[0]
    assert "gads.get_ads_refresh_token" in sql
    assert params == ("user-abc",)


def test_lookup_none_when_unconnected():
    # RPC returns NULL (no stored credential) -> row present, value None.
    assert SupabaseTokenLookup(_FakeDB({"refresh_token": None}).connect)("u") is None
    # RPC returns no row at all.
    assert SupabaseTokenLookup(_FakeDB(None).connect)("u") is None
    # Empty string is treated as unconnected too.
    assert SupabaseTokenLookup(_FakeDB({"refresh_token": ""}).connect)("u") is None


def test_lookup_tolerates_tuple_rows():
    # A non-dict row (e.g. default tuple cursor) still yields the first column.
    assert SupabaseTokenLookup(_FakeDB(("rt-tuple",)).connect)("u") == "rt-tuple"


def test_build_returns_none_without_db(monkeypatch):
    monkeypatch.delenv("ADLOOP_DATABASE_URL", raising=False)
    assert build_supabase_token_lookup() is None


def test_build_returns_lookup_with_db(monkeypatch):
    monkeypatch.setenv("ADLOOP_DATABASE_URL", "postgresql://u:p@host:6543/db")
    lookup = build_supabase_token_lookup()
    assert isinstance(lookup, SupabaseTokenLookup)


def test_plugs_into_credentials_provider(monkeypatch):
    # End-to-end: the lookup is exactly the seam Phase C's provider consumes.
    monkeypatch.setenv("ADLOOP_GOOGLE_CLIENT_ID", "web-client-id")
    monkeypatch.setenv("ADLOOP_GOOGLE_CLIENT_SECRET", "web-client-secret")
    from adloop.config import AdLoopConfig
    from adloop.runtime import use_runtime

    lookup = SupabaseTokenLookup(_FakeDB({"refresh_token": "rt-for-user"}).connect)
    prov = SupabaseCredentialsProvider(token_lookup=lookup)
    with use_runtime(AdLoopConfig(), tenant="user-xyz"):
        creds = prov.ads_credentials(AdLoopConfig())
    assert creds.refresh_token == "rt-for-user"
