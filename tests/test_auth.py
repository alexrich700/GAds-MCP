"""Tests for OAuth token scope-staleness detection and refresh errors.

Tokens granted before AdLoop gained the GTM/GSC/Merchant scopes cannot be
refreshed — Google rejects scope expansion at the token endpoint with
``invalid_scope``. These tests cover the proactive stale-token check and
the guided error fallback.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from google.auth.exceptions import RefreshError

from adloop import auth
from adloop.auth import _ALL_SCOPES, _oauth_flow, _stored_token_scopes
from adloop.config import AdLoopConfig, GoogleConfig

# The scope set granted by AdLoop versions before GTM/GSC/Merchant existed.
OLD_SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/analytics.edit",
    "https://www.googleapis.com/auth/adwords",
]

FUTURE_EXPIRY = "2099-01-01T00:00:00Z"
PAST_EXPIRY = "2020-01-01T00:00:00Z"


def _write_token(path, scopes, expiry=None):
    data = {
        "token": "ya29.fake-access-token",
        "refresh_token": "1//fake-refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "fake.apps.googleusercontent.com",
        "client_secret": "fake-secret",
        "scopes": scopes,
    }
    if expiry:
        data["expiry"] = expiry
    path.write_text(json.dumps(data))


@pytest.fixture
def config(tmp_path) -> AdLoopConfig:
    creds_path = tmp_path / "credentials.json"
    creds_path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "fake.apps.googleusercontent.com",
                    "client_secret": "fake-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
        )
    )
    return AdLoopConfig(
        google=GoogleConfig(
            credentials_path=str(creds_path),
            token_path=str(tmp_path / "token.json"),
        )
    )


@pytest.fixture
def fresh_consent(monkeypatch):
    """Stub the interactive consent flow; returns the mock credentials."""
    creds = MagicMock()
    creds.scopes = list(_ALL_SCOPES)
    creds.to_json.return_value = json.dumps({"scopes": _ALL_SCOPES})
    flow = MagicMock()
    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
        MagicMock(return_value=flow),
    )
    fallback = MagicMock(return_value=creds)
    monkeypatch.setattr(auth, "_run_oauth_with_fallback", fallback)
    return creds


class TestScopeStaleness:
    def test_stale_scope_token_triggers_fresh_consent(
        self, config, tmp_path, fresh_consent
    ):
        token_path = tmp_path / "token.json"
        _write_token(token_path, OLD_SCOPES, expiry=FUTURE_EXPIRY)

        result = _oauth_flow(config)

        assert result is fresh_consent
        # The stale token was replaced by the newly granted one.
        assert json.loads(token_path.read_text())["scopes"] == _ALL_SCOPES

    def test_unreadable_token_triggers_fresh_consent(
        self, config, tmp_path, fresh_consent
    ):
        token_path = tmp_path / "token.json"
        token_path.write_text("{not json")

        result = _oauth_flow(config)

        assert result is fresh_consent

    def test_full_scope_valid_token_is_reused(self, config, tmp_path):
        token_path = tmp_path / "token.json"
        _write_token(token_path, list(_ALL_SCOPES), expiry=FUTURE_EXPIRY)

        creds = _oauth_flow(config)

        assert creds.valid
        assert json.loads(token_path.read_text())["scopes"] == _ALL_SCOPES

    def test_superset_scope_token_is_reused(self, config, tmp_path):
        extra = list(_ALL_SCOPES) + ["https://www.googleapis.com/auth/future"]
        token_path = tmp_path / "token.json"
        _write_token(token_path, extra, expiry=FUTURE_EXPIRY)

        creds = _oauth_flow(config)

        assert creds.valid


class TestStoredTokenScopes:
    def test_reads_scopes(self, tmp_path):
        token_path = tmp_path / "token.json"
        _write_token(token_path, OLD_SCOPES)
        assert _stored_token_scopes(token_path) == OLD_SCOPES

    def test_missing_file_returns_none(self, tmp_path):
        assert _stored_token_scopes(tmp_path / "nope.json") is None

    def test_missing_scopes_key_returns_none(self, tmp_path):
        token_path = tmp_path / "token.json"
        token_path.write_text(json.dumps({"token": "ya29.fake"}))
        assert _stored_token_scopes(token_path) is None


class TestPostConsentVerification:
    def test_partial_consent_raises_guided_error(
        self, config, tmp_path, fresh_consent
    ):
        # User unchecked scopes on Google's granular consent screen.
        fresh_consent.scopes = OLD_SCOPES

        with pytest.raises(RuntimeError, match="were not granted"):
            _oauth_flow(config)
        # A partial-grant token must never be stored — it would be
        # discarded as stale on the next load, reopening consent forever.
        assert not (tmp_path / "token.json").exists()

    def test_error_names_the_missing_scopes(self, config, tmp_path, fresh_consent):
        fresh_consent.scopes = OLD_SCOPES

        with pytest.raises(RuntimeError, match="tagmanager"):
            _oauth_flow(config)

    def test_superset_grant_passes(self, config, tmp_path, fresh_consent):
        fresh_consent.scopes = list(_ALL_SCOPES) + [
            "https://www.googleapis.com/auth/extra"
        ]

        result = _oauth_flow(config)

        assert result is fresh_consent
        assert (tmp_path / "token.json").exists()


class TestRefreshErrors:
    def test_invalid_scope_refresh_gives_guided_error(
        self, config, tmp_path, monkeypatch
    ):
        # Full-scope but expired token that passes the proactive check —
        # covers tokens that go stale in ways the scope comparison misses.
        token_path = tmp_path / "token.json"
        _write_token(token_path, list(_ALL_SCOPES), expiry=PAST_EXPIRY)

        def boom(self, request):
            raise RefreshError("invalid_scope: Bad Request")

        monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh", boom)

        with pytest.raises(RuntimeError, match="invalid_scope"):
            _oauth_flow(config)
        assert not token_path.exists()

    def test_invalid_grant_refresh_gives_guided_error(
        self, config, tmp_path, monkeypatch
    ):
        token_path = tmp_path / "token.json"
        _write_token(token_path, list(_ALL_SCOPES), expiry=PAST_EXPIRY)

        def boom(self, request):
            raise RefreshError("invalid_grant: Token has been revoked.")

        monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh", boom)

        with pytest.raises(RuntimeError, match="revoked or expired"):
            _oauth_flow(config)
        assert not token_path.exists()
