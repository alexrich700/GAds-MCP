"""Merchant API client helper (the Content API for Shopping successor).

The Merchant API (merchantapi.googleapis.com) replaced the deprecated
Content API for Shopping; v1beta was shut down 2026-02-28, so this talks
to the stable v1 surface directly over REST. Same OAuth scope
(.../auth/content) as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adloop.config import AdLoopConfig

_BASE = "https://merchantapi.googleapis.com/accounts/v1"

# Google blocks every Merchant API call from a GCP project that is not
# registered as a developer with the merchant account (401 UNAUTHENTICATED,
# "GCP project with id ... is not registered with the merchant account").
# The registerGcp call itself is the single exception.
_REGISTRATION_MARKER = "is not registered with the merchant account"


class MerchantNotRegistered(RuntimeError):
    """The calling GCP project is not registered as a developer with the
    merchant account — a one-time Merchant API requirement per account."""


def _request(
    config: AdLoopConfig,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
) -> dict:
    from google.auth.transport.requests import AuthorizedSession

    from adloop.auth import get_merchant_credentials

    session = AuthorizedSession(get_merchant_credentials(config))
    response = session.request(
        method,
        f"{_BASE}/{path.lstrip('/')}",
        params=params or {},
        json=json_body,
        timeout=30,
    )
    if response.status_code != 200:
        detail = ""
        try:
            detail = response.json().get("error", {}).get("message", "")
        except Exception:
            pass
        if _REGISTRATION_MARKER in detail:
            raise MerchantNotRegistered(detail)
        raise RuntimeError(f"Merchant API returned {response.status_code}: {detail}")
    return response.json()


def merchant_get(config: AdLoopConfig, path: str, params: dict | None = None) -> dict:
    """Authorized GET against the Merchant API accounts_v1 surface."""
    return _request(config, "GET", path, params=params)


def merchant_post(config: AdLoopConfig, path: str, body: dict | None = None) -> dict:
    """Authorized POST against the Merchant API accounts_v1 surface."""
    return _request(config, "POST", path, json_body=body or {})


def register_gcp(config: AdLoopConfig, account_id: str) -> dict:
    """Register the calling GCP project as a developer with the merchant
    account. The only Merchant API call Google allows while unregistered;
    requires the authorized user to have Admin access on the account.
    ``developerEmail`` is deliberately omitted — without it the call just
    links the GCP project, with it Google sends an invitation flow.
    """
    return merchant_post(
        config, f"accounts/{account_id}/developerRegistration:registerGcp"
    )
