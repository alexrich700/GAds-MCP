"""Per-tenant ``AdLoopConfig`` construction for server mode.

Phase B **placeholder**. In server mode every request must bind its tenant's
config via ``use_runtime`` before tools run (see ``adloop.runtime``). This
module builds that config from the incoming tenant id.

For now it stamps a default config with the *shared* Google Ads developer
token + MCC from the environment, so the transport + auth middleware can be
exercised end-to-end before Supabase is wired.

TODO(Phase C/E): resolve the tenant's real config from Supabase —
  * this user's Google Ads ``customer_id`` and GA4 ``property_id`` (the
    per-client account map),
  * per-user Google refresh token (handled by the credentials provider in
    Phase C, not here),
while keeping the developer token + MCC server-side secrets.
"""

from __future__ import annotations

import os

from adloop.config import AdLoopConfig


def build_tenant_config(tenant_id: str) -> AdLoopConfig:
    """Build the ``AdLoopConfig`` for one tenant (Supabase user id).

    Placeholder: starts from library defaults and stamps the shared Ads
    developer token / MCC from env. Per-tenant ``customer_id`` / GA4 property
    are Phase E lookups — for now the MCC stands in as the customer id.
    """
    config = AdLoopConfig()

    # Hosted tenants always run preview -> confirm (upstream's two-phase gate).
    config.safety.two_phase_apply = True

    dev_token = os.environ.get("ADLOOP_ADS_DEVELOPER_TOKEN", "").strip()
    mcc = os.environ.get("ADLOOP_ADS_LOGIN_CUSTOMER_ID", "").strip()
    if dev_token:
        config.ads.developer_token = dev_token
    if mcc:
        config.ads.login_customer_id = mcc
        # Phase E replaces this with the tenant's real customer id from the
        # client map; until then, default to operating at the MCC level.
        config.ads.customer_id = config.ads.customer_id or mcc

    return config
