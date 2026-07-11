"""Content API for Shopping client wrapper (v2.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adloop.config import AdLoopConfig


def get_merchant_client(config: AdLoopConfig):
    """Return an authenticated Content API for Shopping client."""
    from googleapiclient.discovery import build

    from adloop.auth import get_merchant_credentials

    credentials = get_merchant_credentials(config)
    return build(
        "content", "v2.1", credentials=credentials, cache_discovery=False
    )
