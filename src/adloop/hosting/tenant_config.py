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

import logging
import math
import os

from adloop.config import AdLoopConfig

log = logging.getLogger("adloop.hosting.tenant_config")

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
# max_daily_budget: 50.00 in account currency. Over-cap budgets on
#   draft_campaign / draft_pmax_campaign are CLAMPED to this figure (not
#   rejected) with a bolded warning in the preview; update_campaign clamps
#   upward to the cap but refuses to lower a live budget. Kept deliberately
#   small so the budgets a misused connector can SET stay catchable. Scope is
#   exactly that: it does not bound campaigns that already run above the cap,
#   bids, enable_entity, or other levers; those rely on the preview flow,
#   PAUSED-on-create and the per-user audit trail. Raise with
#   ADLOOP_MAX_DAILY_BUDGET.
# blocked_operations: empty. ADLOOP_BLOCKED_OPERATIONS is a comma list of
#   operation names (e.g. "remove_entity,create_pmax_campaign") that every
#   draft_* refuses outright via check_blocked_operation.
_DEFAULT_REQUIRE_DRY_RUN = False
_DEFAULT_TWO_PHASE_APPLY = False
_DEFAULT_MAX_DAILY_BUDGET = 50.0

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

# Remember which (name, raw) pairs we already warned about so a bad env value
# logs once per process, not once per request.
_warned: set[tuple[str, str]] = set()


def _warn_once(name: str, raw: str, message: str) -> None:
    key = (name, raw)
    if key in _warned:
        return
    _warned.add(key)
    log.warning(message)


def _env_bool(name: str, default: bool, *, unrecognised: bool) -> bool:
    """Parse a boolean env var.

    unset/blank -> ``default``. Any other value that is not one of the
    recognised true/false spellings -> ``unrecognised`` (callers pass the
    SAFE value for the flag, so a typo fails closed) plus a one-time warning.
    """
    raw = os.environ.get(name, "")
    norm = raw.strip().lower()
    if not norm:
        return default
    if norm in _TRUE_VALUES:
        return True
    if norm in _FALSE_VALUES:
        return False
    _warn_once(
        name,
        raw,
        f"{name}={raw!r} is not a recognised boolean "
        f"(use one of {sorted(_TRUE_VALUES | _FALSE_VALUES)}); "
        f"treating as {unrecognised} (the safe value for this flag).",
    )
    return unrecognised


def _env_float(name: str, default: float) -> float:
    """Parse a positive, finite float env var.

    unset/blank -> ``default``. Non-numeric, non-finite (nan/inf), or <= 0
    -> ``default`` plus a one-time warning. nan is rejected explicitly: it
    passes ``<= 0`` and would make every clamp comparison false.
    """
    raw = os.environ.get(name, "")
    norm = raw.strip()
    if not norm:
        return default
    try:
        value = float(norm)
    except ValueError:
        _warn_once(
            name, raw,
            f"{name}={raw!r} is not a number; using default {default}.",
        )
        return default
    if not math.isfinite(value) or value <= 0:
        _warn_once(
            name, raw,
            f"{name}={raw!r} must be a finite number > 0; using default {default}.",
        )
        return default
    return value


def _env_csv(name: str) -> list[str]:
    """Parse a comma-separated env var into a de-duplicated list, order kept."""
    raw = os.environ.get(name, "")
    seen: list[str] = []
    for part in raw.split(","):
        item = part.strip()
        if item and item not in seen:
            seen.append(item)
    return seen


def build_tenant_config(tenant_id: str) -> AdLoopConfig:
    """Build the ``AdLoopConfig`` for one tenant (Supabase user id).

    Starts from library defaults, applies the hosted safety posture, and
    stamps the shared Ads developer token / MCC from env. Per-tenant
    ``customer_id`` / GA4 property are Phase E lookups — for now the MCC
    stands in as the customer id.
    """
    config = AdLoopConfig()

    # For both flags the SAFE value is True (more gating), so a typo such as
    # ADLOOP_REQUIRE_DRY_RUN=1.0 locks writes rather than silently unlocking
    # them. The forced-dry-run response then tells users to contact the
    # operator, and the one-time log line says what to fix.
    config.safety.require_dry_run = _env_bool(
        "ADLOOP_REQUIRE_DRY_RUN", _DEFAULT_REQUIRE_DRY_RUN, unrecognised=True
    )
    config.safety.two_phase_apply = _env_bool(
        "ADLOOP_TWO_PHASE_APPLY", _DEFAULT_TWO_PHASE_APPLY, unrecognised=True
    )
    config.safety.max_daily_budget = _env_float(
        "ADLOOP_MAX_DAILY_BUDGET", _DEFAULT_MAX_DAILY_BUDGET
    )
    config.safety.blocked_operations = _env_csv("ADLOOP_BLOCKED_OPERATIONS")

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
