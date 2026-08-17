"""Tests for safety guard enforcement."""

import pytest

from adloop.config import SafetyConfig
from adloop.safety.guards import (
    SafetyViolation,
    check_bid_increase,
    check_blocked_operation,
    check_budget_cap,
    clamp_budget_cap,
    requires_double_confirmation,
)


@pytest.fixture
def safety_config():
    return SafetyConfig(
        max_daily_budget=50.0,
        max_bid_increase_pct=100,
        blocked_operations=["delete_campaign"],
    )


class TestBudgetCap:
    def test_allows_within_cap(self, safety_config):
        check_budget_cap(49.99, safety_config)

    def test_rejects_over_cap(self, safety_config):
        with pytest.raises(SafetyViolation, match="exceeds maximum"):
            check_budget_cap(51.0, safety_config)


class TestClampBudgetCap:
    """clamp_budget_cap is what the draft_* tools use: over-cap requests
    come back AT the cap with a bolded warning instead of an error."""

    def test_within_cap_passes_through_unchanged(self, safety_config):
        applied, warning = clamp_budget_cap(49.99, safety_config)
        assert applied == 49.99
        assert warning is None

    def test_exactly_at_cap_is_not_a_clamp(self, safety_config):
        applied, warning = clamp_budget_cap(50.0, safety_config)
        assert applied == 50.0
        assert warning is None

    def test_over_cap_is_clamped_to_cap(self, safety_config):
        applied, warning = clamp_budget_cap(500.0, safety_config)
        assert applied == 50.0
        assert warning is not None

    def test_warning_is_bold_and_names_both_figures(self, safety_config):
        _, warning = clamp_budget_cap(500.0, safety_config)
        assert warning.startswith("**FYI")
        assert "50.00" in warning
        assert "500.00" in warning
        assert "max_daily_budget" in warning

    def test_zero_and_negative_are_left_for_validation(self, safety_config):
        # Not the clamp's job: draft_* validation rejects <= 0 separately.
        assert clamp_budget_cap(0, safety_config) == (0, None)
        assert clamp_budget_cap(-5.0, safety_config) == (-5.0, None)

    def test_respects_configured_cap(self):
        cfg = SafetyConfig(max_daily_budget=200.0)
        assert clamp_budget_cap(150.0, cfg) == (150.0, None)
        applied, warning = clamp_budget_cap(250.0, cfg)
        assert applied == 200.0
        assert "200.00" in warning


class TestBidIncrease:
    def test_allows_within_limit(self, safety_config):
        check_bid_increase(1.0, 2.0, safety_config)

    def test_rejects_over_limit(self, safety_config):
        with pytest.raises(SafetyViolation, match="exceeds maximum"):
            check_bid_increase(1.0, 3.0, safety_config)

    def test_handles_zero_current_bid(self, safety_config):
        check_bid_increase(0, 5.0, safety_config)


class TestBlockedOperations:
    def test_allows_unblocked(self, safety_config):
        check_blocked_operation("pause_campaign", safety_config)

    def test_rejects_blocked(self, safety_config):
        with pytest.raises(SafetyViolation, match="blocked"):
            check_blocked_operation("delete_campaign", safety_config)


class TestDoubleConfirmation:
    def test_delete_requires_double(self):
        assert requires_double_confirmation("delete_campaign") is True

    def test_pause_does_not(self):
        assert requires_double_confirmation("pause_campaign") is False

    def test_large_budget_increase_requires_double(self):
        assert requires_double_confirmation(
            "update_budget", current_budget=10.0, proposed_budget=20.0
        ) is True

    def test_small_budget_increase_does_not(self):
        assert requires_double_confirmation(
            "update_budget", current_budget=10.0, proposed_budget=14.0
        ) is False
