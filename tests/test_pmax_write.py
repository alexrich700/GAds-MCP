"""Tests for Performance Max draft tools — input validation behavior.

These tests cover validation paths (no Google Ads API calls). The actual
mutate roundtrip is exercised at runtime via confirm_and_apply with
validate_only=True.
"""

import pytest

from adloop.ads.pmax_write import (
    draft_asset_group,
    draft_asset_group_assets,
    draft_asset_group_signal,
    draft_pmax_campaign,
)
from adloop.ads.write import draft_campaign
from adloop.config import AdLoopConfig, AdsConfig, GA4Config, SafetyConfig


@pytest.fixture
def config():
    return AdLoopConfig(
        ads=AdsConfig(customer_id="1234567890", developer_token="test"),
        ga4=GA4Config(property_id="properties/123456"),
        safety=SafetyConfig(max_daily_budget=100.0),
    )


def _valid_asset_group():
    return {
        "name": "Group A",
        "final_urls": ["https://example.com/"],
        "path1": "products",
        "path2": "shoes",
        "headlines": [
            "Buy running shoes",
            "Free shipping today",
            "Top brands on sale",
        ],
        "long_headlines": ["Find the perfect running shoes for your stride"],
        "descriptions": [
            "Browse 100+ models from top brands.",
            "Free returns within 30 days.",
        ],
        "business_name": "Acme Sports",
        "marketing_image_assets": ["customers/1234567890/assets/1001"],
        "square_marketing_image_assets": ["customers/1234567890/assets/1002"],
        "logo_assets": ["customers/1234567890/assets/1003"],
    }


# ---------------------------------------------------------------------------
# draft_campaign should reject channel_type=PERFORMANCE_MAX
# ---------------------------------------------------------------------------


class TestDraftCampaignRejectsPMax:
    def test_rejects_performance_max_channel(self, config):
        result = draft_campaign(
            config,
            customer_id="1234567890",
            campaign_name="Test",
            daily_budget=10.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            channel_type="PERFORMANCE_MAX",
            geo_target_ids=["2840"],
            language_ids=["1000"],
        )

        assert "error" in result
        assert "draft_pmax_campaign" in result["error"]


# ---------------------------------------------------------------------------
# draft_pmax_campaign
# ---------------------------------------------------------------------------


class TestDraftPmaxCampaign:
    def test_accepts_valid_campaign(self, config):
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=_valid_asset_group(),
        )

        assert "error" not in result
        assert result["status"] == "PENDING_CONFIRMATION"
        assert result["operation"] == "create_pmax_campaign"
        assert result["plan_id"]

    def test_rejects_manual_cpc(self, config):
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MANUAL_CPC",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=_valid_asset_group(),
        )

        assert "error" in result
        details = " ".join(result["details"])
        assert "MANUAL_CPC" not in details or "PMax" in details
        assert "MAXIMIZE_CONVERSIONS" in details

    def test_rejects_target_spend(self, config):
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="TARGET_SPEND",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=_valid_asset_group(),
        )

        assert "error" in result

    def test_requires_asset_group(self, config):
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=None,
        )

        assert "error" in result
        details = " ".join(result["details"])
        assert "asset_group is required" in details

    def test_requires_geo_targets(self, config):
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=[],
            language_ids=["1000"],
            asset_group=_valid_asset_group(),
        )

        assert "error" in result
        assert any("geo_target_ids" in d for d in result["details"])

    def test_rejects_budget_above_cap(self, config):
        # Cap is 100 in fixture; 200 exceeds it
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=200.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=_valid_asset_group(),
        )

        assert "error" in result

    def test_validates_text_char_limits(self, config):
        bad = _valid_asset_group()
        bad["headlines"] = [
            "This headline is way more than thirty characters long for sure",
            "Short",
            "Also short",
        ]
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=bad,
        )

        assert "error" in result
        details = " ".join(result["details"])
        assert "30 chars" in details or "exceeds" in details

    def test_enforces_min_headlines(self, config):
        bad = _valid_asset_group()
        bad["headlines"] = ["only one"]
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=bad,
        )

        assert "error" in result
        details = " ".join(result["details"])
        assert "HEADLINE" in details

    def test_requires_image_resource_names_not_urls(self, config):
        bad = _valid_asset_group()
        bad["marketing_image_assets"] = ["https://example.com/img.png"]
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=bad,
        )

        assert "error" in result
        details = " ".join(result["details"])
        assert "resource_names" in details or "customers/" in details


# ---------------------------------------------------------------------------
# draft_asset_group
# ---------------------------------------------------------------------------


class TestDraftAssetGroup:
    def test_accepts_valid(self, config):
        result = draft_asset_group(
            config,
            customer_id="1234567890",
            campaign_id="22488112473",
            asset_group=_valid_asset_group(),
        )

        assert "error" not in result
        assert result["operation"] == "create_asset_group"

    def test_requires_campaign_id(self, config):
        result = draft_asset_group(
            config,
            customer_id="1234567890",
            campaign_id="",
            asset_group=_valid_asset_group(),
        )

        assert "error" in result
        details = " ".join(result["details"])
        assert "campaign_id" in details

    def test_requires_asset_group(self, config):
        result = draft_asset_group(
            config,
            customer_id="1234567890",
            campaign_id="22488112473",
            asset_group=None,
        )

        assert "error" in result


# ---------------------------------------------------------------------------
# draft_asset_group_assets
# ---------------------------------------------------------------------------


class TestDraftAssetGroupAssets:
    def test_accepts_text_only(self, config):
        result = draft_asset_group_assets(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
            headlines=["New headline 1", "New headline 2"],
        )

        assert "error" not in result
        assert result["operation"] == "create_asset_group_assets"

    def test_requires_at_least_one_asset(self, config):
        result = draft_asset_group_assets(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
        )

        assert "error" in result
        details = " ".join(result["details"])
        assert "At least one asset" in details

    def test_validates_headline_length(self, config):
        result = draft_asset_group_assets(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
            headlines=["a" * 31],
        )

        assert "error" in result

    def test_image_resource_names_must_be_resource_format(self, config):
        result = draft_asset_group_assets(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
            marketing_image_assets=["not-a-resource-name"],
        )

        assert "error" in result


# ---------------------------------------------------------------------------
# draft_asset_group_signal
# ---------------------------------------------------------------------------


class TestDraftAssetGroupSignal:
    def test_accepts_search_theme(self, config):
        result = draft_asset_group_signal(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
            search_theme="buy women's running shoes",
        )

        assert "error" not in result
        assert result["operation"] == "create_asset_group_signal"

    def test_accepts_audience(self, config):
        result = draft_asset_group_signal(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
            audience_resource_name="customers/1234567890/audiences/abc",
        )

        assert "error" not in result

    def test_rejects_both_signal_types_at_once(self, config):
        result = draft_asset_group_signal(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
            search_theme="buy shoes",
            audience_resource_name="customers/1234567890/audiences/abc",
        )

        assert "error" in result

    def test_rejects_missing_signal_content(self, config):
        result = draft_asset_group_signal(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
        )

        assert "error" in result

    def test_rejects_bad_audience_format(self, config):
        result = draft_asset_group_signal(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
            audience_resource_name="not-a-resource-name",
        )

        assert "error" in result
