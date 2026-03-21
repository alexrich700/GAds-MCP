"""Tests for draft_ad_group validation and plan creation."""

from unittest.mock import patch

import pytest

from adloop.ads.write import (
    _check_broad_match_safety_by_campaign,
    _validate_ad_group,
    draft_ad_group,
)
from adloop.config import AdLoopConfig, AdsConfig, SafetyConfig
from adloop.safety.preview import get_plan, remove_plan


@pytest.fixture
def config():
    return AdLoopConfig(
        ads=AdsConfig(customer_id="1234567890", developer_token="test"),
        safety=SafetyConfig(max_daily_budget=50.0, require_dry_run=True),
    )


class TestValidateAdGroup:
    def test_valid_inputs(self):
        errors = _validate_ad_group(
            campaign_id="123",
            ad_group_name="Test Ad Group",
            keywords=None,
            cpc_bid_micros=0,
        )
        assert errors == []

    def test_missing_campaign_id(self):
        errors = _validate_ad_group(
            campaign_id="",
            ad_group_name="Test",
            keywords=None,
            cpc_bid_micros=0,
        )
        assert any("campaign_id" in e for e in errors)

    def test_missing_ad_group_name(self):
        errors = _validate_ad_group(
            campaign_id="123",
            ad_group_name="",
            keywords=None,
            cpc_bid_micros=0,
        )
        assert any("ad_group_name" in e for e in errors)

    def test_whitespace_ad_group_name(self):
        errors = _validate_ad_group(
            campaign_id="123",
            ad_group_name="   ",
            keywords=None,
            cpc_bid_micros=0,
        )
        assert any("ad_group_name" in e for e in errors)

    def test_negative_cpc_bid(self):
        errors = _validate_ad_group(
            campaign_id="123",
            ad_group_name="Test",
            keywords=None,
            cpc_bid_micros=-100,
        )
        assert any("cpc_bid_micros" in e for e in errors)

    def test_valid_with_keywords(self):
        errors = _validate_ad_group(
            campaign_id="123",
            ad_group_name="Test",
            keywords=[
                {"text": "buy shoes", "match_type": "EXACT"},
                {"text": "running shoes", "match_type": "PHRASE"},
            ],
            cpc_bid_micros=0,
        )
        assert errors == []

    def test_keyword_missing_text(self):
        errors = _validate_ad_group(
            campaign_id="123",
            ad_group_name="Test",
            keywords=[{"text": "", "match_type": "EXACT"}],
            cpc_bid_micros=0,
        )
        assert any("no text" in e for e in errors)

    def test_keyword_invalid_match_type(self):
        errors = _validate_ad_group(
            campaign_id="123",
            ad_group_name="Test",
            keywords=[{"text": "shoes", "match_type": "INVALID"}],
            cpc_bid_micros=0,
        )
        assert any("invalid match_type" in e for e in errors)


class TestDraftAdGroup:
    def test_returns_preview_with_plan_id(self, config):
        result = draft_ad_group(
            config,
            customer_id="1234567890",
            campaign_id="999",
            ad_group_name="My Ad Group",
        )
        assert "plan_id" in result
        assert result["operation"] == "create_ad_group"
        assert result["changes"]["campaign_id"] == "999"
        assert result["changes"]["ad_group_name"] == "My Ad Group"

        # Clean up stored plan
        remove_plan(result["plan_id"])

    def test_stores_plan(self, config):
        result = draft_ad_group(
            config,
            customer_id="1234567890",
            campaign_id="999",
            ad_group_name="My Ad Group",
        )
        plan = get_plan(result["plan_id"])
        assert plan is not None
        assert plan.operation == "create_ad_group"
        assert plan.entity_type == "ad_group"

        remove_plan(result["plan_id"])

    def test_includes_keywords_in_plan(self, config):
        keywords = [{"text": "buy shoes", "match_type": "EXACT"}]
        result = draft_ad_group(
            config,
            customer_id="1234567890",
            campaign_id="999",
            ad_group_name="Shoes Group",
            keywords=keywords,
        )
        assert result["changes"]["keywords"] == keywords

        remove_plan(result["plan_id"])

    def test_includes_cpc_bid(self, config):
        result = draft_ad_group(
            config,
            customer_id="1234567890",
            campaign_id="999",
            ad_group_name="Test",
            cpc_bid_micros=500000,
        )
        assert result["changes"]["cpc_bid_micros"] == 500000

        remove_plan(result["plan_id"])

    def test_validation_error_missing_campaign_id(self, config):
        result = draft_ad_group(
            config,
            customer_id="1234567890",
            campaign_id="",
            ad_group_name="Test",
        )
        assert "error" in result

    def test_validation_error_missing_name(self, config):
        result = draft_ad_group(
            config,
            customer_id="1234567890",
            campaign_id="999",
            ad_group_name="",
        )
        assert "error" in result

    def test_blocked_operation(self, config):
        config.safety.blocked_operations = ["create_ad_group"]
        result = draft_ad_group(
            config,
            customer_id="1234567890",
            campaign_id="999",
            ad_group_name="Test",
        )
        assert "error" in result
        config.safety.blocked_operations = []
