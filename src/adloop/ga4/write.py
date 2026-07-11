"""GA4 write tools — key events (conversions), preview-first like all writes.

The tracking loop's missing end: attribution_check can diagnose "this
event fires but isn't a key event, so Ads can't optimize on it" — this
lets the user fix that in-chat instead of clicking through the GA4 UI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adloop.config import AdLoopConfig

_COUNTING_METHODS = ("ONCE_PER_EVENT", "ONCE_PER_SESSION")


def draft_key_event(
    config: AdLoopConfig,
    *,
    property_id: str = "",
    event_name: str = "",
    counting_method: str = "ONCE_PER_EVENT",
) -> dict:
    """Draft marking a GA4 event as a key event (conversion) — returns PREVIEW."""
    from adloop.safety.guards import SafetyViolation, check_blocked_operation
    from adloop.safety.preview import ChangePlan, store_plan

    try:
        check_blocked_operation("create_key_event", config.safety)
    except SafetyViolation as e:
        return {"error": str(e)}

    property_id = str(property_id or "").strip().removeprefix("properties/")
    event_name = (event_name or "").strip()
    counting_method = (counting_method or "").strip().upper()

    errors = []
    if not property_id:
        errors.append(
            "property_id is required (falls back to the configured GA4 "
            "property when set)"
        )
    elif not property_id.isdigit():
        errors.append("property_id must be a numeric GA4 property ID")
    if not event_name:
        errors.append("event_name is required (e.g. 'sign_up')")
    if counting_method not in _COUNTING_METHODS:
        errors.append(
            "counting_method must be ONCE_PER_EVENT (recommended for e.g. "
            "purchases) or ONCE_PER_SESSION (recommended for e.g. sign-ups)"
        )
    if errors:
        return {"error": "Validation failed", "details": errors}

    plan = ChangePlan(
        operation="create_key_event",
        entity_type="key_event",
        entity_id=event_name,
        customer_id="",
        changes={
            "property_id": property_id,
            "event_name": event_name,
            "counting_method": counting_method,
        },
    )
    store_plan(plan)
    preview = plan.to_preview()
    preview["warnings"] = [
        "Key-event status applies to FUTURE data only — historical events "
        "are not reclassified.",
        "For Google Ads to optimize on it, the GA4 property must be linked "
        "to the Ads account and the conversion imported there — check with "
        "attribution_check after a few days.",
    ]
    return preview


def _apply_create_key_event(config: AdLoopConfig, changes: dict) -> dict:
    """Execute a previewed key-event creation via the GA4 Admin API."""
    from google.analytics.admin_v1beta import types

    from adloop.ga4.client import get_admin_client

    client = get_admin_client(config)
    key_event = types.KeyEvent(
        event_name=changes["event_name"],
        counting_method=types.KeyEvent.CountingMethod[
            changes["counting_method"]
        ],
    )
    created = client.create_key_event(
        parent=f"properties/{changes['property_id']}",
        key_event=key_event,
    )
    return {
        "resource_names": [created.name],
        "event_name": created.event_name,
        "counting_method": created.counting_method.name,
    }
