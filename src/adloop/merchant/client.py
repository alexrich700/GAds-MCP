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


def merchant_get(config: AdLoopConfig, path: str, params: dict | None = None) -> dict:
    """Authorized GET against the Merchant API accounts_v1 surface."""
    from google.auth.transport.requests import AuthorizedSession

    from adloop.auth import get_merchant_credentials

    session = AuthorizedSession(get_merchant_credentials(config))
    response = session.get(f"{_BASE}/{path.lstrip('/')}", params=params or {}, timeout=30)
    if response.status_code != 200:
        detail = ""
        try:
            detail = response.json().get("error", {}).get("message", "")
        except Exception:
            pass
        raise RuntimeError(f"Merchant API returned {response.status_code}: {detail}")
    return response.json()
