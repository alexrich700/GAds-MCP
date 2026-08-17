"""Safety guards — budget caps, bid limits, and blocked operation enforcement."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adloop.config import SafetyConfig


class SafetyViolation(Exception):
    """Raised when a proposed change violates safety constraints."""


def check_budget_cap(daily_budget: float, config: SafetyConfig) -> None:
    """Reject if proposed daily budget exceeds configured maximum.

    Kept for callers that want a hard stop. The draft_* tools use
    :func:`clamp_budget_cap` instead so an over-cap request still produces a
    usable plan at the cap, with a warning the agent must surface.
    """
    if daily_budget > config.max_daily_budget:
        raise SafetyViolation(
            f"Daily budget {daily_budget:.2f} exceeds maximum {config.max_daily_budget:.2f}"
        )


def clamp_budget_cap(
    daily_budget: float,
    config: SafetyConfig,
    config_path: str | None = None,
) -> tuple[float, str | None]:
    """Clamp ``daily_budget`` to ``config.max_daily_budget``.

    Returns ``(applied_budget, warning)``. ``warning`` is ``None`` when the
    request was within the cap. When it was over, ``applied_budget`` is the
    cap and ``warning`` is a bolded, ready-to-show sentence explaining that
    the plan carries the cap, not the requested figure, and why.

    ``config_path`` is the config file behind this server, if any (local
    installs). When given, the remediation tells the user which file holds
    the cap; when empty (hosted / server mode) it says the operator sets it.

    Scope: this bounds the budget figure the connector itself will SET on a
    campaign it creates or updates. It does not bound campaigns that already
    exist above the cap, bids, or other levers; those are governed by the
    preview flow, PAUSED-on-create and the audit trail.
    """
    cap = float(config.max_daily_budget)
    if daily_budget <= cap:
        return daily_budget, None
    if config_path:
        how_to_raise = (
            f"To allow more, raise safety.max_daily_budget in {config_path} "
            f"and restart the AdLoop MCP server."
        )
    else:
        how_to_raise = (
            "Only the server operator can raise the cap; otherwise adjust "
            "the budget in the Google Ads UI after the campaign is live and "
            "reviewed."
        )
    warning = (
        f"**FYI: daily budget set to {cap:.2f}, not the {daily_budget:.2f} you "
        f"asked for.** This server caps any campaign budget it sets at "
        f"{cap:.2f}/day (safety.max_daily_budget). The cap is intentional: if "
        f"this connector is ever used by someone who should not have it, the "
        f"budgets it can set stay small and catchable. {how_to_raise}"
    )
    return cap, warning


def check_bid_increase(current_bid: float, proposed_bid: float, config: SafetyConfig) -> None:
    """Reject if bid increase percentage exceeds configured maximum."""
    if current_bid <= 0:
        return
    increase_pct = ((proposed_bid - current_bid) / current_bid) * 100
    if increase_pct > config.max_bid_increase_pct:
        raise SafetyViolation(
            f"Bid increase {increase_pct:.0f}% exceeds maximum {config.max_bid_increase_pct}%"
        )


def check_blocked_operation(operation: str, config: SafetyConfig) -> None:
    """Reject if operation is in the blocked list."""
    if operation in config.blocked_operations:
        raise SafetyViolation(f"Operation '{operation}' is blocked by configuration")


def requires_double_confirmation(operation: str, **kwargs: object) -> bool:
    """Return True if this operation is destructive enough to need double confirmation.

    Triggers on:
    - Any delete or remove operation
    - Budget increases >50%
    """
    if "delete" in operation or "remove" in operation:
        return True

    current = kwargs.get("current_budget")
    proposed = kwargs.get("proposed_budget")
    if isinstance(current, (int, float)) and isinstance(proposed, (int, float)) and current > 0:
        if ((proposed - current) / current) > 0.5:
            return True

    return False
