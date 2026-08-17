"""Per-tenant ``AdLoopConfig`` construction for server mode.

In server mode every request must bind its tenant's config via ``use_runtime``
before tools run (see ``adloop.runtime``). This module builds that config from
the incoming tenant id.

It stamps a default config with the *shared* Google Ads developer token + MCC
from the environment, plus the hosted safety posture (also from the
environment, see ``_env_bool`` / ``_env_float`` below). There is no
``~/.adloop/config.yaml`` on a hosted deployment, so env vars are the only
way to change these; ``source_path`` is left empty on purpose so
``confirm_and_apply`` never points a hosted user at a file they cannot edit.

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

# Hosted safety defaults. Each is overridable by the env var named next to it.
#
# require_dry_run: OFF. Hosted tenants apply real writes. The value that
#   protects the account is the two-step preview -> confirm_and_apply flow
#   (every draft_* returns a plan the agent must show first), the fact that
#   campaigns/RSAs are created PAUSED, the per-user audit sink, and the plan
#   TTL. Leaving this ON turned the hosted server into a read-only tool that
#   *looked* writable and told users to edit a config file that does not exist.
# two_phase_apply: OFF. The dry-run pass never calls Google, so on a hosted
#   deployment it was a second round trip per change with no validation
#   value. The draft_* preview already forces the agent to show the change
#   before applying. Flip on with ADLOOP_TWO_PHASE_APPLY=true if you want the
#   extra ceremony back.
# max_daily_budget: 50.00 in account currency. Over-cap budgets are CLAMPED
#   to this figure (not rejected) with a bolded warning in the preview. Kept
#   deliberately small: if a connector token is ever misused, the worst case
#   is a paused campaign at the cap. Raise with ADLOOP_MAX_DAILY_BUDGET.
_DEFAULT_REQUIRE_DRY_RUN = False
_DEFAULT_TWO_PHASE_APPLY = False
_DEFAULT_MAX_DAILY_BUDGET = 50.0

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var; unset/blank/unrecognised -> ``default``."""
    raw = os.environ.get(name, "").strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return default


def _env_float(name: str, default: float) -> float:
    """Parse a positive float env var; unset/blank/invalid/<=0 -> ``default``."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return value


def build_tenant_config(tenant_id: str) -> AdLoopConfig:
    """Build the ``AdLoopConfig`` for one tenant (Supabase user id).

    Starts from library defaults, applies the hosted safety posture, and
    stamps the shared Ads developer token / MCC from env. Per-tenant
    ``customer_id`` / GA4 property are Phase E lookups — for now the MCC
    stands in as the customer id.
    """
    config = AdLoopConfig()

    config.safety.require_dry_run = _env_bool(
        "ADLOOP_REQUIRE_DRY_RUN", _DEFAULT_REQUIRE_DRY_RUN
    )
    config.safety.two_phase_apply = _env_bool(
        "ADLOOP_TWO_PHASE_APPLY", _DEFAULT_TWO_PHASE_APPLY
    )
    config.safety.max_daily_budget = _env_float(
        "ADLOOP_MAX_DAILY_BUDGET", _DEFAULT_MAX_DAILY_BUDGET
    )

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
