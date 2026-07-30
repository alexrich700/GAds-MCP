"""Tests for the Phase B hosting shell: Supabase auth + tenant resolution."""

import types

import pytest
from fastmcp.exceptions import ToolError

from adloop.hosting import auth as hosting_auth
from adloop.hosting.tenant_config import build_tenant_config


def _token(client_id="cid-123", subject="user-abc", claims=None):
    return types.SimpleNamespace(client_id=client_id, subject=subject, claims=claims or {})


# --- build_supabase_auth (env-gated) ---------------------------------------

def test_build_supabase_auth_none_without_env(monkeypatch):
    monkeypatch.delenv("ADLOOP_SUPABASE_URL", raising=False)
    monkeypatch.delenv("ADLOOP_BASE_URL", raising=False)
    assert hosting_auth.build_supabase_auth() is None


def test_build_supabase_auth_configured(monkeypatch):
    monkeypatch.setenv("ADLOOP_SUPABASE_URL", "https://demo.supabase.co")
    monkeypatch.setenv("ADLOOP_BASE_URL", "https://gads-mcp.example.run.app")
    provider = hosting_auth.build_supabase_auth()
    assert provider is not None
    # It's FastMCP's SupabaseProvider (a RemoteAuthProvider).
    assert type(provider).__name__ == "SupabaseProvider"


def test_expected_client_ids(monkeypatch):
    monkeypatch.delenv("ADLOOP_EXPECTED_CLIENT_ID", raising=False)
    assert hosting_auth.expected_client_ids() == frozenset()
    monkeypatch.setenv("ADLOOP_EXPECTED_CLIENT_ID", "  connector-xyz  ")
    assert hosting_auth.expected_client_ids() == frozenset({"connector-xyz"})
    # Comma-separated: a cutover can accept the old and new connector ids at once.
    monkeypatch.setenv("ADLOOP_EXPECTED_CLIENT_ID", "old-cid, new-cid")
    assert hosting_auth.expected_client_ids() == frozenset({"old-cid", "new-cid"})


# --- resolve_tenant (client_id pinning + tenant extraction) ----------------

def test_resolve_tenant_happy_path():
    assert hosting_auth.resolve_tenant(_token(subject="user-42"), frozenset({"cid-123"})) == "user-42"


def test_resolve_tenant_rejects_unauthenticated():
    with pytest.raises(ToolError):
        hosting_auth.resolve_tenant(None, frozenset({"cid-123"}))


def test_resolve_tenant_pins_client_id():
    # A token minted for a different connector must be refused.
    with pytest.raises(ToolError):
        hosting_auth.resolve_tenant(_token(client_id="some-other-connector"), frozenset({"cid-123"}))


def test_resolve_tenant_accepts_any_of_multiple_pinned_ids():
    # Cutover: both the current and the new connector id are accepted.
    expected = frozenset({"old-cid", "new-cid"})
    assert hosting_auth.resolve_tenant(_token(client_id="new-cid", subject="u9"), expected) == "u9"


def test_resolve_tenant_skips_pin_when_expected_is_empty():
    # Dev fallback: no pin configured => any verified token's tenant is used.
    assert hosting_auth.resolve_tenant(_token(client_id="whatever", subject="u1"), frozenset()) == "u1"


def test_resolve_tenant_falls_back_to_claims_sub():
    tok = _token(subject=None, claims={"sub": "user-from-claims"})
    assert hosting_auth.resolve_tenant(tok, frozenset({"cid-123"})) == "user-from-claims"


def test_resolve_tenant_requires_a_subject():
    with pytest.raises(ToolError):
        hosting_auth.resolve_tenant(_token(subject=None, claims={}), frozenset({"cid-123"}))


# --- build_tenant_config (placeholder) -------------------------------------

def test_build_tenant_config_stamps_shared_ads_creds(monkeypatch):
    monkeypatch.setenv("ADLOOP_ADS_DEVELOPER_TOKEN", "dev-tok-xyz")
    monkeypatch.setenv("ADLOOP_ADS_LOGIN_CUSTOMER_ID", "4762726066")
    cfg = build_tenant_config("user-abc")
    assert cfg.ads.developer_token == "dev-tok-xyz"
    assert cfg.ads.login_customer_id == "4762726066"
    assert cfg.ads.customer_id == "4762726066"  # MCC stands in until Phase E
    assert cfg.safety.two_phase_apply is True  # hosted tenants always two-phase


def test_build_tenant_config_defaults_when_env_absent(monkeypatch):
    monkeypatch.delenv("ADLOOP_ADS_DEVELOPER_TOKEN", raising=False)
    monkeypatch.delenv("ADLOOP_ADS_LOGIN_CUSTOMER_ID", raising=False)
    cfg = build_tenant_config("user-abc")
    assert cfg.ads.developer_token == ""
    assert cfg.safety.two_phase_apply is True


# --- redirect_uri connector pinning (scales across dynamic registration) ----

class _FakeCursor:
    def __init__(self, row): self._row = row
    def fetchone(self): return self._row


class _FakeConn:
    """Minimal psycopg-ish connection recording the statements it ran."""
    def __init__(self, row, calls): self._row, self.calls = row, calls
    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _FakeCursor(self._row)
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _connect_returning(row, calls=None):
    calls = calls if calls is not None else []
    def provider(): return _FakeConn(row, calls)
    provider.calls = calls
    return provider


CLAUDE = "https://claude.ai/api/mcp/auth_callback"


def test_allowed_redirect_uris_parses_env(monkeypatch):
    monkeypatch.delenv("ADLOOP_ALLOWED_REDIRECT_URIS", raising=False)
    assert hosting_auth.allowed_redirect_uris() == frozenset()
    monkeypatch.setenv("ADLOOP_ALLOWED_REDIRECT_URIS", f"  {CLAUDE} , https://other.example/cb ")
    assert hosting_auth.allowed_redirect_uris() == frozenset({CLAUDE, "https://other.example/cb"})


def test_redirect_check_accepts_allowlisted_connector():
    check = hosting_auth.RedirectUriClientCheck(
        _connect_returning({"uris": CLAUDE}), frozenset({CLAUDE})
    )
    assert check("any-dynamically-registered-client-id") is True


def test_redirect_check_accepts_every_user_without_config_changes():
    """The whole point: a different client_id per user, same redirect_uri."""
    check = hosting_auth.RedirectUriClientCheck(
        _connect_returning({"uris": CLAUDE}), frozenset({CLAUDE})
    )
    for cid in ("user-a-cid", "user-b-cid", "user-c-cid"):
        assert check(cid) is True


def test_redirect_check_rejects_unknown_redirect():
    check = hosting_auth.RedirectUriClientCheck(
        _connect_returning({"uris": "https://attacker.example/steal"}), frozenset({CLAUDE})
    )
    assert check("cid") is False


def test_redirect_check_rejects_when_any_registered_uri_is_not_allowlisted():
    check = hosting_auth.RedirectUriClientCheck(
        _connect_returning({"uris": f"{CLAUDE},https://attacker.example/steal"}),
        frozenset({CLAUDE}),
    )
    assert check("cid") is False


def test_redirect_check_rejects_unknown_client_id():
    # No row -> NULL -> no uris. "Nothing to check" must not mean "allowed".
    check = hosting_auth.RedirectUriClientCheck(
        _connect_returning({"uris": None}), frozenset({CLAUDE})
    )
    assert check("never-registered") is False


def test_redirect_check_localhost_is_off_by_default():
    check = hosting_auth.RedirectUriClientCheck(
        _connect_returning({"uris": "http://localhost:3118/callback"}), frozenset({CLAUDE})
    )
    assert check("cid") is False


def test_redirect_check_localhost_any_port_when_enabled():
    for uri in ("http://localhost:3118/callback", "http://127.0.0.1:51234/callback"):
        check = hosting_auth.RedirectUriClientCheck(
            _connect_returning({"uris": uri}), frozenset({CLAUDE}), allow_localhost=True
        )
        assert check("cid") is True


def test_redirect_check_localhost_flag_does_not_admit_remote_hosts():
    check = hosting_auth.RedirectUriClientCheck(
        _connect_returning({"uris": "http://localhost.attacker.example/cb"}),
        frozenset({CLAUDE}), allow_localhost=True,
    )
    assert check("cid") is False


def test_redirect_check_caches_per_client_id():
    calls = []
    check = hosting_auth.RedirectUriClientCheck(
        _connect_returning({"uris": CLAUDE}, calls), frozenset({CLAUDE})
    )
    for _ in range(5):
        assert check("cid") is True
    assert len(calls) == 1, "lookup should be cached, not one round trip per tool call"


def test_redirect_check_handles_tuple_rows():
    check = hosting_auth.RedirectUriClientCheck(
        _connect_returning((CLAUDE,)), frozenset({CLAUDE})
    )
    assert check("cid") is True


def test_redirect_check_fails_closed_on_db_error():
    class _Boom:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): raise RuntimeError("pool exhausted")
    check = hosting_auth.RedirectUriClientCheck(lambda: _Boom(), frozenset({CLAUDE}))
    with pytest.raises(RuntimeError):
        check("cid")


# --- resolve_tenant with a callable check ---------------------------------

def test_resolve_tenant_accepts_callable_check():
    assert hosting_auth.resolve_tenant(_token(subject="u7"), lambda cid: True) == "u7"


def test_resolve_tenant_rejects_via_callable_check():
    with pytest.raises(ToolError):
        hosting_auth.resolve_tenant(_token(), lambda cid: False)


def test_resolve_tenant_rejects_missing_client_id_under_callable_check():
    with pytest.raises(ToolError):
        hosting_auth.resolve_tenant(_token(client_id=None), lambda cid: True)


def test_build_client_check_prefers_redirect_allowlist(monkeypatch):
    monkeypatch.setenv("ADLOOP_ALLOWED_REDIRECT_URIS", CLAUDE)
    monkeypatch.setenv("ADLOOP_DATABASE_URL", "postgresql://u:p@example.test:6543/postgres")
    monkeypatch.setattr(hosting_auth, "build_connection_provider", lambda: _connect_returning({"uris": CLAUDE}))
    assert callable(hosting_auth.build_client_check())


def test_build_client_check_falls_back_to_legacy_pin_without_db(monkeypatch):
    monkeypatch.setenv("ADLOOP_ALLOWED_REDIRECT_URIS", CLAUDE)
    monkeypatch.setenv("ADLOOP_EXPECTED_CLIENT_ID", "legacy-cid")
    monkeypatch.setattr(hosting_auth, "build_connection_provider", lambda: None)
    assert hosting_auth.build_client_check() == frozenset({"legacy-cid"})
