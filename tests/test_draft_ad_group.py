"""Tests for draft_ad_group validation and plan creation."""

from unittest.mock import patch

import pytest

from adloop.ads.write import (
    _preflight_ad_group_checks,
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

    def test_keyword_null_match_type(self):
        """MCP/JSON callers can send null for match_type."""
        errors = _validate_ad_group(
            campaign_id="123",
            ad_group_name="Test",
            keywords=[{"text": "shoes", "match_type": None}],
            cpc_bid_micros=0,
        )
        assert any("invalid match_type" in e for e in errors)

    def test_keyword_missing_match_type_key(self):
        """Keywords without a match_type key should get a validation error."""
        errors = _validate_ad_group(
            campaign_id="123",
            ad_group_name="Test",
            keywords=[{"text": "shoes"}],
            cpc_bid_micros=0,
        )
        assert any("invalid match_type" in e for e in errors)


class TestPreflightAdGroupChecks:
    """Tests for _preflight_ad_group_checks using mocked GAQL queries."""

    def _mock_execute(self, campaign_rows, ad_group_rows=None):
        """Return a side_effect function that returns different results per query."""
        ad_group_rows = ad_group_rows or []

        def side_effect(config, customer_id, query):
            if "campaign.advertising_channel_type" in query:
                return campaign_rows
            if "ad_group.name" in query:
                return ad_group_rows
            return []

        return side_effect

    @patch("adloop.ads.gaql.execute_query")
    def test_search_campaign_no_issues(self, mock_query, config):
        mock_query.side_effect = self._mock_execute(
            [{"campaign.advertising_channel_type": "SEARCH",
              "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
              "campaign.name": "My Campaign"}],
        )
        errors, warnings = _preflight_ad_group_checks(
            config, "1234567890", "999", "New Group", [], 0
        )
        assert errors == []
        assert warnings == []

    @patch("adloop.ads.gaql.execute_query")
    def test_display_campaign_rejected(self, mock_query, config):
        mock_query.side_effect = self._mock_execute(
            [{"campaign.advertising_channel_type": "DISPLAY",
              "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
              "campaign.name": "Display Campaign"}],
        )
        errors, warnings = _preflight_ad_group_checks(
            config, "1234567890", "999", "New Group", [], 0
        )
        assert any("DISPLAY" in e for e in errors)
        assert any("only supports SEARCH" in e for e in errors)

    @patch("adloop.ads.gaql.execute_query")
    def test_shopping_campaign_rejected(self, mock_query, config):
        mock_query.side_effect = self._mock_execute(
            [{"campaign.advertising_channel_type": "SHOPPING",
              "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
              "campaign.name": "Shopping Campaign"}],
        )
        errors, warnings = _preflight_ad_group_checks(
            config, "1234567890", "999", "New Group", [], 0
        )
        assert any("SHOPPING" in e for e in errors)

    @patch("adloop.ads.gaql.execute_query")
    def test_campaign_not_found(self, mock_query, config):
        mock_query.side_effect = self._mock_execute([])
        errors, warnings = _preflight_ad_group_checks(
            config, "1234567890", "999", "New Group", [], 0
        )
        assert any("not found" in e for e in errors)

    @patch("adloop.ads.gaql.execute_query")
    def test_duplicate_ad_group_name_warns(self, mock_query, config):
        mock_query.side_effect = self._mock_execute(
            [{"campaign.advertising_channel_type": "SEARCH",
              "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
              "campaign.name": "My Campaign"}],
            [{"ad_group.name": "Existing Group"}, {"ad_group.name": "Another"}],
        )
        errors, warnings = _preflight_ad_group_checks(
            config, "1234567890", "999", "Existing Group", [], 0
        )
        assert errors == []
        assert any("already exists" in w for w in warnings)

    @patch("adloop.ads.gaql.execute_query")
    def test_no_duplicate_name_no_warning(self, mock_query, config):
        mock_query.side_effect = self._mock_execute(
            [{"campaign.advertising_channel_type": "SEARCH",
              "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
              "campaign.name": "My Campaign"}],
            [{"ad_group.name": "Other Group"}],
        )
        errors, warnings = _preflight_ad_group_checks(
            config, "1234567890", "999", "New Group", [], 0
        )
        assert errors == []
        assert not any("already exists" in w for w in warnings)

    @patch("adloop.ads.gaql.execute_query")
    def test_cpc_bid_on_smart_bidding_warns(self, mock_query, config):
        mock_query.side_effect = self._mock_execute(
            [{"campaign.advertising_channel_type": "SEARCH",
              "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
              "campaign.name": "Smart Campaign"}],
        )
        errors, warnings = _preflight_ad_group_checks(
            config, "1234567890", "999", "New Group", [], cpc_bid_micros=500000
        )
        assert errors == []
        assert any("ignored" in w for w in warnings)

    @patch("adloop.ads.gaql.execute_query")
    def test_cpc_bid_on_manual_cpc_no_warning(self, mock_query, config):
        mock_query.side_effect = self._mock_execute(
            [{"campaign.advertising_channel_type": "SEARCH",
              "campaign.bidding_strategy_type": "MANUAL_CPC",
              "campaign.name": "Manual Campaign"}],
        )
        errors, warnings = _preflight_ad_group_checks(
            config, "1234567890", "999", "New Group", [], cpc_bid_micros=500000
        )
        assert errors == []
        assert not any("ignored" in w for w in warnings)

    @patch("adloop.ads.gaql.execute_query")
    def test_broad_match_non_smart_bidding_warns(self, mock_query, config):
        mock_query.side_effect = self._mock_execute(
            [{"campaign.advertising_channel_type": "SEARCH",
              "campaign.bidding_strategy_type": "MANUAL_CPC",
              "campaign.name": "Manual Campaign"}],
        )
        keywords = [{"text": "shoes", "match_type": "BROAD"}]
        errors, warnings = _preflight_ad_group_checks(
            config, "1234567890", "999", "New Group", keywords, 0
        )
        assert errors == []
        assert any("DANGEROUS" in w and "BROAD" in w for w in warnings)

    @patch("adloop.ads.gaql.execute_query")
    def test_broad_match_smart_bidding_no_warning(self, mock_query, config):
        mock_query.side_effect = self._mock_execute(
            [{"campaign.advertising_channel_type": "SEARCH",
              "campaign.bidding_strategy_type": "TARGET_CPA",
              "campaign.name": "Smart Campaign"}],
        )
        keywords = [{"text": "shoes", "match_type": "BROAD"}]
        errors, warnings = _preflight_ad_group_checks(
            config, "1234567890", "999", "New Group", keywords, 0
        )
        assert errors == []
        assert not any("BROAD" in w for w in warnings)

    @patch("adloop.ads.gaql.execute_query")
    def test_api_failure_surfaces_warning(self, mock_query, config):
        """If API calls fail, preflight should warn but not block the draft."""
        mock_query.side_effect = Exception("API unavailable")
        errors, warnings = _preflight_ad_group_checks(
            config, "1234567890", "999", "New Group", [], 0
        )
        assert errors == []
        assert len(warnings) == 1
        assert "Preflight checks could not complete" in warnings[0]
        assert "API unavailable" in warnings[0]


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
