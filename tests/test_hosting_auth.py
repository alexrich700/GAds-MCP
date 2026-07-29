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
