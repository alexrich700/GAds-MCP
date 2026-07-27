"""Tests for GA4 key-event drafting and execution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from adloop.config import AdLoopConfig, GA4Config, SafetyConfig
from adloop.ga4 import write as ga4_write
from adloop.safety import preview as preview_store
from adloop.safety.preview import InMemoryPlanStore


@pytest.fixture(autouse=True)
def clear_pending_plans():
    preview_store.set_plan_store(InMemoryPlanStore())
    yield
    preview_store.set_plan_store(InMemoryPlanStore())


@pytest.fixture
def config() -> AdLoopConfig:
    return AdLoopConfig(
        ga4=GA4Config(property_id="properties/123456"),
        safety=SafetyConfig(require_dry_run=True),
    )


class TestDraft:
    def test_returns_preview_with_future_data_warnings(self, config):
        result = ga4_write.draft_key_event(
            config, property_id="123456", event_name="sign_up",
            counting_method="ONCE_PER_SESSION",
        )

        assert result["operation"] == "create_key_event"
        assert result["entity_id"] == "sign_up"
        assert result["changes"]["counting_method"] == "ONCE_PER_SESSION"
        assert any("FUTURE data" in w for w in result["warnings"])
        assert any("linked" in w for w in result["warnings"])

    def test_strips_properties_prefix(self, config):
        result = ga4_write.draft_key_event(
            config, property_id="properties/123456", event_name="purchase",
        )
        assert result["changes"]["property_id"] == "123456"

    def test_validates_inputs(self, config):
        result = ga4_write.draft_key_event(
            config, property_id="", event_name="", counting_method="ALWAYS",
        )
        details = " ".join(result["details"])
        assert "property_id is required" in details
        assert "event_name is required" in details
        assert "ONCE_PER_EVENT" in details

    def test_respects_blocked_operations(self, config):
        config.safety.blocked_operations = ["create_key_event"]
        result = ga4_write.draft_key_event(
            config, property_id="123456", event_name="sign_up",
        )
        assert "error" in result and "blocked" in result["error"].lower()


class TestApply:
    def test_creates_key_event_via_admin_api(self, config):
        created = MagicMock()
        created.name = "properties/123456/keyEvents/999"
        created.event_name = "sign_up"
        created.counting_method.name = "ONCE_PER_SESSION"
        admin = MagicMock()
        admin.create_key_event.return_value = created

        with patch("adloop.ga4.client.get_admin_client", return_value=admin):
            result = ga4_write._apply_create_key_event(config, {
                "property_id": "123456",
                "event_name": "sign_up",
                "counting_method": "ONCE_PER_SESSION",
            })

        call = admin.create_key_event.call_args
        assert call.kwargs["parent"] == "properties/123456"
        assert call.kwargs["key_event"].event_name == "sign_up"
        assert result["resource_names"] == ["properties/123456/keyEvents/999"]

    def test_execute_plan_routes_ga4_ops_without_ads_client(self, config):
        """GA4-only setups (no developer token) must be able to apply
        key-event plans — the dispatch must not build an Ads client."""
        from adloop.ads import write as ads_write
        from adloop.safety.preview import ChangePlan

        plan = ChangePlan(
            operation="create_key_event",
            entity_type="key_event",
            entity_id="sign_up",
            customer_id="",
            changes={"property_id": "123456", "event_name": "sign_up",
                     "counting_method": "ONCE_PER_EVENT"},
        )

        created = MagicMock()
        created.name = "properties/123456/keyEvents/1"
        created.event_name = "sign_up"
        created.counting_method.name = "ONCE_PER_EVENT"
        admin = MagicMock()
        admin.create_key_event.return_value = created

        def _no_ads_client(*_a, **_k):
            raise AssertionError("Ads client must not be built for GA4 plans")

        with (
            patch("adloop.ads.client.get_ads_client", _no_ads_client),
            patch("adloop.ga4.client.get_admin_client", return_value=admin),
        ):
            result = ads_write._execute_plan(config, plan)

        assert result["event_name"] == "sign_up"
