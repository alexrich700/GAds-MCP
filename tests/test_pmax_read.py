"""Tests for Performance Max read tools and analyze_pmax_performance cross-ref."""

from unittest.mock import patch

import pytest

from adloop.ads.pmax_read import (
    get_asset_group_assets,
    get_asset_group_signals,
    get_asset_group_top_combinations,
    get_asset_groups,
    get_pmax_campaigns,
    get_pmax_channel_breakdown,
    get_pmax_search_terms,
)
from adloop.config import AdLoopConfig, AdsConfig, GA4Config, SafetyConfig
from adloop.crossref import analyze_pmax_performance


@pytest.fixture
def config():
    return AdLoopConfig(
        ads=AdsConfig(customer_id="1234567890", developer_token="test"),
        ga4=GA4Config(property_id="properties/123456"),
        safety=SafetyConfig(max_daily_budget=50.0, require_dry_run=True),
    )


# ---------------------------------------------------------------------------
# get_pmax_campaigns
# ---------------------------------------------------------------------------


class TestGetPmaxCampaigns:
    @patch("adloop.ads.gaql.execute_query")
    def test_filters_to_performance_max(self, mock_query, config):
        mock_query.return_value = []

        get_pmax_campaigns(config, customer_id="1234567890")

        call_query = mock_query.call_args[0][2]
        assert "campaign.advertising_channel_type = 'PERFORMANCE_MAX'" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_enriches_cost_budget_roas(self, mock_query, config):
        mock_query.return_value = [
            {
                "campaign.id": 111,
                "campaign.name": "PMax A",
                "campaign.status": "ENABLED",
                "campaign.advertising_channel_type": "PERFORMANCE_MAX",
                "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
                "campaign.brand_guidelines_enabled": True,
                "campaign_budget.amount_micros": 25_000_000,
                "metrics.impressions": 10_000,
                "metrics.clicks": 200,
                "metrics.cost_micros": 80_000_000,
                "metrics.conversions": 8,
                "metrics.conversions_value": 800.0,
                "metrics.ctr": 0.02,
                "metrics.average_cpc": 400_000,
            }
        ]

        result = get_pmax_campaigns(config, customer_id="1234567890")

        assert result["total_campaigns"] == 1
        row = result["campaigns"][0]
        assert row["metrics.cost"] == 80.0
        assert row["metrics.cpa"] == 10.0
        assert row["metrics.roas"] == 10.0  # 800 / 80
        assert row["campaign_budget.amount"] == 25.0
        assert row["metrics.average_cpc_eur"] == 0.4

    @patch("adloop.ads.gaql.execute_query")
    def test_with_date_range(self, mock_query, config):
        mock_query.return_value = []

        get_pmax_campaigns(
            config,
            customer_id="1234567890",
            date_range_start="2026-04-01",
            date_range_end="2026-04-30",
        )

        call_query = mock_query.call_args[0][2]
        assert "BETWEEN '2026-04-01' AND '2026-04-30'" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_default_date_uses_last_30_days(self, mock_query, config):
        mock_query.return_value = []

        get_pmax_campaigns(config, customer_id="1234567890")

        call_query = mock_query.call_args[0][2]
        assert "DURING LAST_30_DAYS" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_empty_results(self, mock_query, config):
        mock_query.return_value = []

        result = get_pmax_campaigns(config, customer_id="1234567890")

        assert result["campaigns"] == []
        assert result["total_campaigns"] == 0


# ---------------------------------------------------------------------------
# get_pmax_channel_breakdown
# ---------------------------------------------------------------------------


class TestGetPmaxChannelBreakdown:
    @patch("adloop.ads.gaql.execute_query")
    def test_returns_per_channel(self, mock_query, config):
        mock_query.return_value = [
            {
                "campaign.id": 111,
                "campaign.name": "PMax A",
                "segments.ad_network_type": "SEARCH",
                "metrics.impressions": 1000,
                "metrics.clicks": 50,
                "metrics.cost_micros": 30_000_000,
                "metrics.conversions": 3,
                "metrics.conversions_value": 300.0,
            },
            {
                "campaign.id": 111,
                "campaign.name": "PMax A",
                "segments.ad_network_type": "YOUTUBE_WATCH",
                "metrics.impressions": 5000,
                "metrics.clicks": 20,
                "metrics.cost_micros": 10_000_000,
                "metrics.conversions": 1,
                "metrics.conversions_value": 100.0,
            },
        ]

        result = get_pmax_channel_breakdown(config, customer_id="1234567890")

        assert result["total_rows"] == 2
        first = result["channel_breakdown"][0]
        assert first["metrics.cost"] == 30.0
        assert first["metrics.roas"] == 10.0  # 300 / 30

    @patch("adloop.ads.gaql.execute_query")
    def test_warns_about_pre_june_2025(self, mock_query, config):
        mock_query.return_value = []

        result = get_pmax_channel_breakdown(
            config,
            customer_id="1234567890",
            date_range_start="2025-04-01",
            date_range_end="2025-04-30",
        )

        assert any("2025-06-01" in i for i in result["insights"])

    @patch("adloop.ads.gaql.execute_query")
    def test_warns_when_mixed_present(self, mock_query, config):
        mock_query.return_value = [
            {
                "campaign.id": 111,
                "campaign.name": "PMax A",
                "segments.ad_network_type": "MIXED",
                "metrics.impressions": 100,
                "metrics.clicks": 5,
                "metrics.cost_micros": 1_000_000,
                "metrics.conversions": 0,
            }
        ]

        result = get_pmax_channel_breakdown(config, customer_id="1234567890")

        assert any("MIXED" in i for i in result["insights"])

    @patch("adloop.ads.gaql.execute_query")
    def test_campaign_filter(self, mock_query, config):
        mock_query.return_value = []

        get_pmax_channel_breakdown(
            config, customer_id="1234567890", campaign_id="999"
        )

        call_query = mock_query.call_args[0][2]
        assert "campaign.id = 999" in call_query

    def test_invalid_campaign_id_raises(self, config):
        with pytest.raises(ValueError, match="must be numeric"):
            get_pmax_channel_breakdown(
                config, customer_id="1234567890", campaign_id="DROP TABLE"
            )


# ---------------------------------------------------------------------------
# get_asset_groups
# ---------------------------------------------------------------------------


class TestGetAssetGroups:
    @patch("adloop.ads.gaql.execute_query")
    def test_returns_ad_strength_and_metrics(self, mock_query, config):
        mock_query.return_value = [
            {
                "asset_group.id": 555,
                "asset_group.name": "Group 1",
                "asset_group.status": "ENABLED",
                "asset_group.final_urls": ["https://example.com/a"],
                "asset_group.path1": "products",
                "asset_group.path2": "shoes",
                "asset_group.ad_strength": "GOOD",
                "campaign.id": 111,
                "campaign.name": "PMax A",
                "metrics.impressions": 5000,
                "metrics.clicks": 100,
                "metrics.cost_micros": 40_000_000,
                "metrics.conversions": 4,
                "metrics.conversions_value": 400.0,
            }
        ]

        result = get_asset_groups(config, customer_id="1234567890")

        assert result["total_asset_groups"] == 1
        row = result["asset_groups"][0]
        assert row["asset_group.ad_strength"] == "GOOD"
        assert row["metrics.cost"] == 40.0
        assert row["metrics.roas"] == 10.0
        assert row["metrics.cpa"] == 10.0

    @patch("adloop.ads.gaql.execute_query")
    def test_campaign_filter(self, mock_query, config):
        mock_query.return_value = []

        get_asset_groups(
            config, customer_id="1234567890", campaign_id="999"
        )

        call_query = mock_query.call_args[0][2]
        assert "campaign.id = 999" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_filters_to_performance_max(self, mock_query, config):
        mock_query.return_value = []

        get_asset_groups(config, customer_id="1234567890")

        call_query = mock_query.call_args[0][2]
        assert "campaign.advertising_channel_type = 'PERFORMANCE_MAX'" in call_query


# ---------------------------------------------------------------------------
# get_asset_group_assets
# ---------------------------------------------------------------------------


class TestGetAssetGroupAssets:
    @patch("adloop.ads.gaql.execute_query")
    def test_builds_youtube_url(self, mock_query, config):
        mock_query.return_value = [
            {
                "asset_group.id": 555,
                "asset_group.name": "Group 1",
                "asset_group_asset.field_type": "YOUTUBE_VIDEO",
                "asset_group_asset.status": "ENABLED",
                "asset_group_asset.policy_summary.review_status": "REVIEWED",
                "asset.id": 999,
                "asset.type": "YOUTUBE_VIDEO",
                "asset.text_asset.text": None,
                "asset.image_asset.full_size.url": None,
                "asset.youtube_video_asset.youtube_video_id": "dQw4w9WgXcQ",
                "asset.youtube_video_asset.youtube_video_title": "Sample",
                "campaign.id": 111,
                "campaign.name": "PMax A",
            }
        ]

        result = get_asset_group_assets(config, customer_id="1234567890")

        row = result["assets"][0]
        assert (
            row["asset.youtube_video_asset.youtube_url"]
            == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )

    @patch("adloop.ads.gaql.execute_query")
    def test_does_not_select_dropped_v24_fields(self, mock_query, config):
        """Verify the query no longer references fields removed in API v24."""
        mock_query.return_value = []

        get_asset_group_assets(config, customer_id="1234567890")

        call_query = mock_query.call_args[0][2]
        assert "performance_label" not in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_asset_group_id_filter(self, mock_query, config):
        mock_query.return_value = []

        get_asset_group_assets(
            config, customer_id="1234567890", asset_group_id="777"
        )

        call_query = mock_query.call_args[0][2]
        assert "asset_group.id = 777" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_campaign_id_filter(self, mock_query, config):
        mock_query.return_value = []

        get_asset_group_assets(
            config, customer_id="1234567890", campaign_id="999"
        )

        call_query = mock_query.call_args[0][2]
        assert "campaign.id = 999" in call_query

    def test_invalid_asset_group_id_raises(self, config):
        with pytest.raises(ValueError, match="must be numeric"):
            get_asset_group_assets(
                config, customer_id="1234567890", asset_group_id="abc"
            )


# ---------------------------------------------------------------------------
# get_asset_group_signals
# ---------------------------------------------------------------------------


class TestGetAssetGroupSignals:
    @patch("adloop.ads.gaql.execute_query")
    def test_classifies_search_theme_vs_audience(self, mock_query, config):
        mock_query.return_value = [
            {
                "asset_group.id": 555,
                "asset_group.name": "Group 1",
                "asset_group_signal.resource_name": "x/1",
                "asset_group_signal.audience.audience": None,
                "asset_group_signal.search_theme.text": "buy running shoes",
                "campaign.id": 111,
                "campaign.name": "PMax A",
            },
            {
                "asset_group.id": 555,
                "asset_group.name": "Group 1",
                "asset_group_signal.resource_name": "x/2",
                "asset_group_signal.audience.audience": "customers/1/audiences/abc",
                "asset_group_signal.search_theme.text": None,
                "campaign.id": 111,
                "campaign.name": "PMax A",
            },
        ]

        result = get_asset_group_signals(config, customer_id="1234567890")

        signals = result["signals"]
        assert signals[0]["signal_type"] == "SEARCH_THEME"
        assert signals[1]["signal_type"] == "AUDIENCE"

    @patch("adloop.ads.gaql.execute_query")
    def test_empty_signals(self, mock_query, config):
        mock_query.return_value = []

        result = get_asset_group_signals(config, customer_id="1234567890")

        assert result["total_signals"] == 0


# ---------------------------------------------------------------------------
# get_asset_group_top_combinations
# ---------------------------------------------------------------------------


class TestGetAssetGroupTopCombinations:
    @patch("adloop.ads.gaql.execute_query")
    def test_basic_query(self, mock_query, config):
        mock_query.return_value = [
            {
                "asset_group.id": 555,
                "asset_group.name": "Group 1",
                "asset_group_top_combination_view.asset_group_top_combinations": "...",
                "campaign.id": 111,
                "campaign.name": "PMax A",
            }
        ]

        result = get_asset_group_top_combinations(
            config, customer_id="1234567890", asset_group_id="555"
        )

        assert result["total_rows"] == 1

    @patch("adloop.ads.gaql.execute_query")
    def test_query_excludes_metrics(self, mock_query, config):
        """asset_group_top_combination_view does not expose metrics.* in v24."""
        mock_query.return_value = []

        get_asset_group_top_combinations(config, customer_id="1234567890")

        call_query = mock_query.call_args[0][2]
        assert "metrics." not in call_query
        # And cannot ORDER BY a metric we don't select.
        assert "ORDER BY metrics" not in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_includes_limit(self, mock_query, config):
        mock_query.return_value = []

        get_asset_group_top_combinations(config, customer_id="1234567890")

        call_query = mock_query.call_args[0][2]
        assert "LIMIT 50" in call_query


# ---------------------------------------------------------------------------
# get_pmax_search_terms
# ---------------------------------------------------------------------------


class TestGetPmaxSearchTerms:
    def test_requires_campaign_id(self, config):
        result = get_pmax_search_terms(config, customer_id="1234567890")

        assert "error" in result
        assert "campaign_id" in result["error"]

    @patch("adloop.ads.gaql.execute_query")
    def test_returns_categories(self, mock_query, config):
        mock_query.return_value = [
            {
                "campaign_search_term_insight.id": "abc/123",
                "campaign_search_term_insight.category_label": "buy running shoes",
                "metrics.impressions": 500,
                "metrics.clicks": 30,
            }
        ]

        result = get_pmax_search_terms(
            config, customer_id="1234567890", campaign_id="111"
        )

        assert result["total_rows"] == 1
        row = result["search_term_categories"][0]
        assert row["metrics.impressions"] == 500
        assert row["metrics.clicks"] == 30
        # Per Google Ads API v24, cost/conversion metrics are not selectable
        # on campaign_search_term_insight. The tool surfaces a `note` field
        # so callers know cost is not available.
        assert "note" in result

    @patch("adloop.ads.gaql.execute_query")
    def test_query_excludes_prohibited_metrics(self, mock_query, config):
        """The API rejects cost_micros/conversions on campaign_search_term_insight."""
        mock_query.return_value = []

        get_pmax_search_terms(
            config, customer_id="1234567890", campaign_id="111"
        )

        call_query = mock_query.call_args[0][2]
        assert "cost_micros" not in call_query
        assert "conversions" not in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_handles_unsupported_api_version(self, mock_query, config):
        mock_query.side_effect = Exception(
            "UNRECOGNIZED_FIELD: campaign_search_term_insight"
        )

        result = get_pmax_search_terms(
            config, customer_id="1234567890", campaign_id="111"
        )

        assert "error" in result
        assert "v23.2" in result["hint"]

    def test_invalid_campaign_id_raises(self, config):
        with pytest.raises(ValueError, match="must be numeric"):
            get_pmax_search_terms(
                config, customer_id="1234567890", campaign_id="DROP TABLE"
            )


# ---------------------------------------------------------------------------
# analyze_pmax_performance (cross-ref)
# ---------------------------------------------------------------------------


class TestAnalyzePmaxPerformance:
    @patch("adloop.ga4.reports.run_ga4_report")
    @patch("adloop.ads.pmax_read.get_pmax_channel_breakdown")
    @patch("adloop.ads.pmax_read.get_asset_group_assets")
    @patch("adloop.ads.pmax_read.get_asset_groups")
    @patch("adloop.ads.pmax_read.get_pmax_campaigns")
    def test_aggregates_full_diagnostic(
        self, mock_camps, mock_groups, mock_assets, mock_channels, mock_ga4, config
    ):
        mock_camps.return_value = {
            "campaigns": [
                {
                    "campaign.id": 111,
                    "campaign.name": "PMax A",
                    "campaign.status": "ENABLED",
                    "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
                    "campaign.brand_guidelines_enabled": True,
                    "campaign_budget.amount": 25.0,
                    "metrics.clicks": 200,
                    "metrics.cost": 80.0,
                    "metrics.conversions": 8,
                    "metrics.conversions_value": 800.0,
                    "metrics.cpa": 10.0,
                    "metrics.roas": 10.0,
                }
            ]
        }
        mock_groups.return_value = {
            "asset_groups": [
                {
                    "asset_group.id": 555,
                    "asset_group.name": "Group 1",
                    "asset_group.ad_strength": "POOR",
                    "campaign.id": 111,
                    "metrics.cost": 50.0,
                    "metrics.clicks": 100,
                    "metrics.conversions": 3,
                }
            ]
        }
        mock_assets.return_value = {
            "assets": [
                {
                    "asset.id": 999,
                    "asset_group.id": 555,
                    "asset_group_asset.field_type": "HEADLINE",
                    "asset.text_asset.text": "Headline 1",
                    "asset.image_asset.full_size.url": None,
                },
                {
                    "asset.id": 1000,
                    "asset_group.id": 555,
                    "asset_group_asset.field_type": "HEADLINE",
                    "asset.text_asset.text": "Headline 2",
                    "asset.image_asset.full_size.url": None,
                },
            ]
        }
        mock_channels.return_value = {
            "channel_breakdown": [
                {
                    "campaign.id": 111,
                    "segments.ad_network_type": "YOUTUBE_WATCH",
                    "metrics.cost": 75.0,
                    "metrics.clicks": 150,
                    "metrics.conversions": 6,
                },
                {
                    "campaign.id": 111,
                    "segments.ad_network_type": "SEARCH",
                    "metrics.cost": 5.0,
                    "metrics.clicks": 50,
                    "metrics.conversions": 2,
                },
            ],
            "insights": [],
        }
        mock_ga4.return_value = {
            "rows": [
                {
                    "sessionCampaignName": "PMax A",
                    "sessionSource": "google",
                    "sessionMedium": "cpc",
                    "sessions": "60",
                    "conversions": "5",
                    "engagedSessions": "45",
                }
            ]
        }

        result = analyze_pmax_performance(
            config,
            customer_id="1234567890",
            property_id="properties/123456",
        )

        assert result["total_campaigns"] == 1
        camp = result["campaigns"][0]
        assert camp["campaign_id"] == "111"
        assert camp["weak_asset_groups"] == 1
        assert len(camp["asset_groups"]) == 1
        ag = camp["asset_groups"][0]
        assert ag["ad_strength"] == "POOR"
        assert ag["asset_counts_by_type"]["HEADLINE"] == 2
        # Asset group has only HEADLINEs — every other required type is missing.
        # missing_asset_minimums lists each one with current vs needed counts.
        assert len(ag["missing_asset_minimums"]) >= 5
        # Channel skew check: YouTube is ~94% of spend (75/80)
        skew_insights = [i for i in result["insights"] if "skewed" in i]
        assert len(skew_insights) == 1
        # POOR ad strength insight
        ad_strength_insights = [i for i in result["insights"] if "ad strength is POOR" in i]
        assert len(ad_strength_insights) == 1
        # Missing-asset-minimums insight (replaces the old LOW-performing-asset check)
        minimums_insights = [
            i for i in result["insights"] if "below minimums" in i
        ]
        assert len(minimums_insights) == 1
        # GA4 paid attached
        assert camp["ga4_paid"]["sessions"] == 60
        assert camp["ga4_paid"]["conversions"] == 5

    @patch("adloop.ads.pmax_read.get_pmax_channel_breakdown")
    @patch("adloop.ads.pmax_read.get_asset_group_assets")
    @patch("adloop.ads.pmax_read.get_asset_groups")
    @patch("adloop.ads.pmax_read.get_pmax_campaigns")
    def test_works_without_ga4_property(
        self, mock_camps, mock_groups, mock_assets, mock_channels, config
    ):
        mock_camps.return_value = {
            "campaigns": [
                {
                    "campaign.id": 111,
                    "campaign.name": "PMax A",
                    "campaign.status": "ENABLED",
                    "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
                    "metrics.clicks": 100,
                    "metrics.cost": 40.0,
                    "metrics.conversions": 4,
                    "metrics.conversions_value": 400.0,
                }
            ]
        }
        mock_groups.return_value = {"asset_groups": []}
        mock_assets.return_value = {"assets": []}
        mock_channels.return_value = {"channel_breakdown": [], "insights": []}

        result = analyze_pmax_performance(
            config, customer_id="1234567890", property_id=""
        )

        assert result["campaigns"][0]["ga4_paid"] is None

    @patch("adloop.ads.pmax_read.get_pmax_channel_breakdown")
    @patch("adloop.ads.pmax_read.get_asset_group_assets")
    @patch("adloop.ads.pmax_read.get_asset_groups")
    @patch("adloop.ads.pmax_read.get_pmax_campaigns")
    def test_zero_conversion_warning(
        self, mock_camps, mock_groups, mock_assets, mock_channels, config
    ):
        mock_camps.return_value = {
            "campaigns": [
                {
                    "campaign.id": 111,
                    "campaign.name": "PMax A",
                    "campaign.status": "ENABLED",
                    "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
                    "metrics.clicks": 50,
                    "metrics.cost": 100.0,
                    "metrics.conversions": 0,
                    "metrics.conversions_value": 0,
                }
            ]
        }
        mock_groups.return_value = {"asset_groups": []}
        mock_assets.return_value = {"assets": []}
        mock_channels.return_value = {"channel_breakdown": [], "insights": []}

        result = analyze_pmax_performance(config, customer_id="1234567890")

        zero_conv = [i for i in result["insights"] if "0 conversions" in i]
        assert len(zero_conv) == 1

    @patch("adloop.ads.pmax_read.get_pmax_channel_breakdown")
    @patch("adloop.ads.pmax_read.get_asset_group_assets")
    @patch("adloop.ads.pmax_read.get_asset_groups")
    @patch("adloop.ads.pmax_read.get_pmax_campaigns")
    def test_campaign_id_filter_returns_error_when_missing(
        self, mock_camps, mock_groups, mock_assets, mock_channels, config
    ):
        mock_camps.return_value = {
            "campaigns": [
                {
                    "campaign.id": 111,
                    "campaign.name": "PMax A",
                    "campaign.status": "ENABLED",
                }
            ]
        }
        mock_groups.return_value = {"asset_groups": []}
        mock_assets.return_value = {"assets": []}
        mock_channels.return_value = {"channel_breakdown": [], "insights": []}

        result = analyze_pmax_performance(
            config, customer_id="1234567890", campaign_id="999"
        )

        assert "error" in result
        assert "999" in result["error"]

    @patch("adloop.ga4.reports.run_ga4_report")
    @patch("adloop.ads.pmax_read.get_pmax_channel_breakdown")
    @patch("adloop.ads.pmax_read.get_asset_group_assets")
    @patch("adloop.ads.pmax_read.get_asset_groups")
    @patch("adloop.ads.pmax_read.get_pmax_campaigns")
    def test_ga4_paid_is_none_when_ga4_fails(
        self, mock_camps, mock_groups, mock_assets, mock_channels, mock_ga4, config
    ):
        """When GA4 returns an error, ga4_paid must be None — not zeros that
        look like real data. Otherwise consumers can't distinguish 'GA4
        unavailable' from 'campaign actually has zero paid sessions'."""
        mock_camps.return_value = {
            "campaigns": [
                {
                    "campaign.id": 111,
                    "campaign.name": "PMax A",
                    "campaign.status": "ENABLED",
                    "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
                    "metrics.clicks": 100,
                    "metrics.cost": 40.0,
                    "metrics.conversions": 4,
                    "metrics.conversions_value": 400.0,
                }
            ]
        }
        mock_groups.return_value = {"asset_groups": []}
        mock_assets.return_value = {"assets": []}
        mock_channels.return_value = {"channel_breakdown": [], "insights": []}
        mock_ga4.return_value = {"error": "GA4 property not configured"}

        result = analyze_pmax_performance(
            config,
            customer_id="1234567890",
            property_id="properties/123456",
        )

        # ga4_paid must be None, NOT a zero-filled dict
        assert result["campaigns"][0]["ga4_paid"] is None
        # The warning should be in insights so the user knows why
        ga4_warnings = [i for i in result["insights"] if "GA4" in i]
        assert len(ga4_warnings) >= 1

    @patch("adloop.ga4.reports.run_ga4_report")
    @patch("adloop.ads.pmax_read.get_pmax_channel_breakdown")
    @patch("adloop.ads.pmax_read.get_asset_group_assets")
    @patch("adloop.ads.pmax_read.get_asset_groups")
    @patch("adloop.ads.pmax_read.get_pmax_campaigns")
    def test_ga4_paid_is_none_when_ga4_raises(
        self, mock_camps, mock_groups, mock_assets, mock_channels, mock_ga4, config
    ):
        """Same guarantee when GA4 raises rather than returning an error dict."""
        mock_camps.return_value = {
            "campaigns": [
                {
                    "campaign.id": 111,
                    "campaign.name": "PMax A",
                    "campaign.status": "ENABLED",
                    "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
                    "metrics.clicks": 100,
                    "metrics.cost": 40.0,
                    "metrics.conversions": 4,
                    "metrics.conversions_value": 400.0,
                }
            ]
        }
        mock_groups.return_value = {"asset_groups": []}
        mock_assets.return_value = {"assets": []}
        mock_channels.return_value = {"channel_breakdown": [], "insights": []}
        mock_ga4.side_effect = RuntimeError("network down")

        result = analyze_pmax_performance(
            config,
            customer_id="1234567890",
            property_id="properties/123456",
        )

        assert result["campaigns"][0]["ga4_paid"] is None

    @patch("adloop.ads.pmax_read.get_pmax_channel_breakdown")
    @patch("adloop.ads.pmax_read.get_asset_group_assets")
    @patch("adloop.ads.pmax_read.get_asset_groups")
    @patch("adloop.ads.pmax_read.get_pmax_campaigns")
    def test_no_pmax_campaigns_in_account(
        self, mock_camps, mock_groups, mock_assets, mock_channels, config
    ):
        mock_camps.return_value = {"campaigns": []}
        mock_groups.return_value = {"asset_groups": []}
        mock_assets.return_value = {"assets": []}
        mock_channels.return_value = {"channel_breakdown": [], "insights": []}

        result = analyze_pmax_performance(config, customer_id="1234567890")

        assert result["total_campaigns"] == 0
        assert any(
            "No Performance Max campaigns found" in i for i in result["insights"]
        )
