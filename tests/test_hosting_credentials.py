"""Tests for the Phase C hosting shell: per-user Google credentials provider."""

import pytest

from adloop.config import AdLoopConfig
from adloop.hosting.credentials import (
    ADWORDS_SCOPE,
    GA4_READONLY_SCOPE,
    MissingGoogleConnection,
    SupabaseCredentialsProvider,
    install_credentials_provider,
)
from adloop.runtime import use_runtime


def _with_client_env(monkeypatch):
    monkeypatch.setenv("ADLOOP_GOOGLE_CLIENT_ID", "web-client-id")
    monkeypatch.setenv("ADLOOP_GOOGLE_CLIENT_SECRET", "web-client-secret")


def test_ads_credentials_built_from_tenant_refresh_token(monkeypatch):
    _with_client_env(monkeypatch)
    prov = SupabaseCredentialsProvider(token_lookup=lambda t: f"rt-for-{t}")
    with use_runtime(AdLoopConfig(), tenant="user-1"):
        creds = prov.ads_credentials(AdLoopConfig())
    assert creds.refresh_token == "rt-for-user-1"  # keyed by the bound tenant
    assert creds.client_id == "web-client-id"
    assert ADWORDS_SCOPE in (creds.scopes or [])


def test_ga4_credentials_use_analytics_scope(monkeypatch):
    _with_client_env(monkeypatch)
    prov = SupabaseCredentialsProvider(token_lookup=lambda t: "rt")
    with use_runtime(AdLoopConfig(), tenant="u"):
        creds = prov.ga4_credentials(AdLoopConfig())
    assert GA4_READONLY_SCOPE in (creds.scopes or [])


def test_missing_connection_raises_guided_error(monkeypatch):
    _with_client_env(monkeypatch)
    prov = SupabaseCredentialsProvider(token_lookup=lambda t: None)
    with use_runtime(AdLoopConfig(), tenant="u"):
        with pytest.raises(MissingGoogleConnection):
            prov.ads_credentials(AdLoopConfig())


def test_missing_server_client_config_raises(monkeypatch):
    monkeypatch.delenv("ADLOOP_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("ADLOOP_GOOGLE_CLIENT_SECRET", raising=False)
    prov = SupabaseCredentialsProvider(token_lookup=lambda t: "rt")
    with use_runtime(AdLoopConfig(), tenant="u"):
        with pytest.raises(RuntimeError):
            prov.ads_credentials(AdLoopConfig())


def test_default_lookup_reads_dev_env(monkeypatch):
    _with_client_env(monkeypatch)
    monkeypatch.setenv("ADLOOP_DEV_REFRESH_TOKEN", "dev-refresh-token")
    prov = SupabaseCredentialsProvider()  # default lookup
    with use_runtime(AdLoopConfig(), tenant="u"):
        creds = prov.ads_credentials(AdLoopConfig())
    assert creds.refresh_token == "dev-refresh-token"


def test_optional_services_report_unsupported():
    # v1 = Ads + GA4 only. Upstream's capability check keys off hasattr, so
    # these must be genuinely absent (not stubbed).
    prov = SupabaseCredentialsProvider(token_lookup=lambda t: "rt")
    assert not hasattr(prov, "gtm_credentials")
    assert not hasattr(prov, "gsc_credentials")
    assert not hasattr(prov, "merchant_credentials")


def test_install_sets_global_provider():
    from adloop import auth as gauth

    prior = gauth.get_credentials_provider()
    try:
        install_credentials_provider(token_lookup=lambda t: "rt")
        assert isinstance(gauth.get_credentials_provider(), SupabaseCredentialsProvider)
    finally:
        gauth.set_credentials_provider(prior)
