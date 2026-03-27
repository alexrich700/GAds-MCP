"""Tests for Google Ads read and insights tools."""

from unittest.mock import patch

import pytest

from adloop.ads.read import (
    get_ad_schedule_performance,
    get_auction_insights,
    get_bid_strategy_status,
    get_budget_pacing,
    get_change_history,
    get_device_performance,
    get_impression_share,
    get_keyword_performance,
    get_location_performance,
    get_quality_score_details,
    get_search_terms,
)
from adloop.config import AdLoopConfig, AdsConfig, SafetyConfig


@pytest.fixture
def config():
    return AdLoopConfig(
        ads=AdsConfig(customer_id="1234567890", developer_token="test"),
        safety=SafetyConfig(max_daily_budget=50.0, require_dry_run=True),
    )


# ---------------------------------------------------------------------------
# get_impression_share
# ---------------------------------------------------------------------------


class TestGetImpressionShare:
    @patch("adloop.ads.gaql.execute_query")
    def test_campaign_level(self, mock_query, config):
        mock_query.return_value = [
            {
                "campaign.id": 111,
                "campaign.name": "Test Campaign",
                "campaign.status": "ENABLED",
                "metrics.impressions": 1000,
                "metrics.clicks": 100,
                "metrics.cost_micros": 50_000_000,
                "metrics.search_impression_share": 0.45,
                "metrics.search_budget_lost_impression_share": 0.20,
                "metrics.search_rank_lost_impression_share": 0.35,
                "metrics.search_exact_match_impression_share": 0.60,
                "metrics.search_top_impression_share": 0.30,
                "metrics.search_absolute_top_impression_share": 0.10,
            }
        ]

        result = get_impression_share(config, customer_id="1234567890")

        assert "impression_share" in result
        assert result["total_rows"] == 1
        assert result["level"] == "campaign"
        row = result["impression_share"][0]
        assert row["metrics.cost"] == 50.0
        assert row["metrics.search_impression_share_pct"] == "45.0%"
        assert row["metrics.search_budget_lost_impression_share_pct"] == "20.0%"
        assert row["metrics.search_rank_lost_impression_share_pct"] == "35.0%"

    @patch("adloop.ads.gaql.execute_query")
    def test_ad_group_level(self, mock_query, config):
        mock_query.return_value = [
            {
                "campaign.name": "Camp",
                "ad_group.id": 222,
                "ad_group.name": "AG1",
                "metrics.impressions": 500,
                "metrics.clicks": 50,
                "metrics.cost_micros": 10_000_000,
                "metrics.search_impression_share": 0.80,
                "metrics.search_budget_lost_impression_share": 0.05,
                "metrics.search_rank_lost_impression_share": 0.15,
                "metrics.search_exact_match_impression_share": 0.90,
                "metrics.search_top_impression_share": 0.50,
                "metrics.search_absolute_top_impression_share": 0.25,
            }
        ]

        result = get_impression_share(
            config, customer_id="1234567890", level="ad_group"
        )

        assert result["level"] == "ad_group"
        assert result["total_rows"] == 1
        # Verify ad_group query was built
        call_query = mock_query.call_args[0][2]
        assert "FROM ad_group" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_keyword_level(self, mock_query, config):
        mock_query.return_value = []

        result = get_impression_share(
            config, customer_id="1234567890", level="keyword"
        )

        assert result["level"] == "keyword"
        assert result["total_rows"] == 0
        call_query = mock_query.call_args[0][2]
        assert "FROM keyword_view" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_empty_results(self, mock_query, config):
        mock_query.return_value = []

        result = get_impression_share(config, customer_id="1234567890")

        assert result["impression_share"] == []
        assert result["total_rows"] == 0


# ---------------------------------------------------------------------------
# get_change_history
# ---------------------------------------------------------------------------


class TestGetChangeHistory:
    @patch("adloop.ads.gaql.execute_query")
    def test_default_query(self, mock_query, config):
        mock_query.return_value = [
            {
                "change_event.change_date_time": "2026-03-25 10:00:00",
                "change_event.user_email": "user@example.com",
                "change_event.change_resource_type": "CAMPAIGN",
                "change_event.resource_change_operation": "UPDATE",
                "change_event.changed_fields": "budget",
                "change_event.old_resource": None,
                "change_event.new_resource": None,
                "change_event.resource_name": "customers/123/campaigns/456",
            }
        ]

        result = get_change_history(config, customer_id="1234567890")

        assert "changes" in result
        assert result["total_changes"] == 1
        assert result["changes"][0]["change_event.user_email"] == "user@example.com"

    @patch("adloop.ads.gaql.execute_query")
    def test_resource_type_filter(self, mock_query, config):
        mock_query.return_value = []

        get_change_history(
            config, customer_id="1234567890", resource_type="CAMPAIGN"
        )

        call_query = mock_query.call_args[0][2]
        assert "change_resource_type = 'CAMPAIGN'" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_operation_type_filter(self, mock_query, config):
        mock_query.return_value = []

        get_change_history(
            config, customer_id="1234567890", operation_type="UPDATE"
        )

        call_query = mock_query.call_args[0][2]
        assert "resource_change_operation = 'UPDATE'" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_date_range_appends_end_of_day(self, mock_query, config):
        mock_query.return_value = []

        get_change_history(
            config,
            customer_id="1234567890",
            date_range_start="2026-03-01",
            date_range_end="2026-03-27",
        )

        call_query = mock_query.call_args[0][2]
        # End date should have 23:59:59 appended for timestamp comparison
        assert "2026-03-27 23:59:59" in call_query
        assert ">= '2026-03-01'" in call_query
        assert "DURING LAST_14_DAYS" not in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_date_range_preserves_explicit_time(self, mock_query, config):
        mock_query.return_value = []

        get_change_history(
            config,
            customer_id="1234567890",
            date_range_start="2026-03-01",
            date_range_end="2026-03-27T15:00:00",
        )

        call_query = mock_query.call_args[0][2]
        # Should NOT append 23:59:59 when caller already provided a time
        assert "2026-03-27T15:00:00" in call_query
        assert "23:59:59" not in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_limit_clamped_to_api_max(self, mock_query, config):
        mock_query.return_value = []

        get_change_history(
            config, customer_id="1234567890", limit=50_000
        )

        call_query = mock_query.call_args[0][2]
        assert "LIMIT 10000" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_limit_clamped_to_minimum(self, mock_query, config):
        mock_query.return_value = []

        get_change_history(
            config, customer_id="1234567890", limit=-5
        )

        call_query = mock_query.call_args[0][2]
        assert "LIMIT 1" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_empty_results(self, mock_query, config):
        mock_query.return_value = []

        result = get_change_history(config, customer_id="1234567890")

        assert result["changes"] == []
        assert result["total_changes"] == 0


# ---------------------------------------------------------------------------
# get_device_performance
# ---------------------------------------------------------------------------


class TestGetDevicePerformance:
    @patch("adloop.ads.gaql.execute_query")
    def test_campaign_level(self, mock_query, config):
        mock_query.return_value = [
            {
                "campaign.id": 111,
                "campaign.name": "Test",
                "segments.device": "MOBILE",
                "metrics.impressions": 800,
                "metrics.clicks": 80,
                "metrics.ctr": 0.10,
                "metrics.cost_micros": 20_000_000,
                "metrics.average_cpc": 250_000,
                "metrics.conversions": 4,
                "metrics.conversions_value": 200.0,
            },
            {
                "campaign.id": 111,
                "campaign.name": "Test",
                "segments.device": "DESKTOP",
                "metrics.impressions": 500,
                "metrics.clicks": 50,
                "metrics.ctr": 0.10,
                "metrics.cost_micros": 15_000_000,
                "metrics.average_cpc": 300_000,
                "metrics.conversions": 3,
                "metrics.conversions_value": 150.0,
            },
        ]

        result = get_device_performance(config, customer_id="1234567890")

        assert result["total_rows"] == 2
        assert result["level"] == "campaign"
        mobile = result["device_performance"][0]
        assert mobile["metrics.cost"] == 20.0
        assert mobile["metrics.conversion_rate"] == 5.0  # 4/80 * 100

    @patch("adloop.ads.gaql.execute_query")
    def test_ad_group_level(self, mock_query, config):
        mock_query.return_value = []

        result = get_device_performance(
            config, customer_id="1234567890", level="ad_group"
        )

        assert result["level"] == "ad_group"
        call_query = mock_query.call_args[0][2]
        assert "ad_group.id" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_empty_results(self, mock_query, config):
        mock_query.return_value = []

        result = get_device_performance(config, customer_id="1234567890")

        assert result["device_performance"] == []
        assert result["total_rows"] == 0


# ---------------------------------------------------------------------------
# get_location_performance
# ---------------------------------------------------------------------------


class TestGetLocationPerformance:
    @patch("adloop.ads.gaql.execute_query")
    def test_default_query(self, mock_query, config):
        mock_query.return_value = [
            {
                "geographic_view.country_criterion_id": 2276,
                "geographic_view.location_type": "LOCATION_OF_PRESENCE",
                "campaign.name": "Germany Campaign",
                "metrics.impressions": 500,
                "metrics.clicks": 50,
                "metrics.ctr": 0.10,
                "metrics.cost_micros": 25_000_000,
                "metrics.conversions": 5,
                "metrics.conversions_value": 250.0,
            }
        ]

        result = get_location_performance(config, customer_id="1234567890")

        assert "locations" in result
        assert result["total_locations"] == 1
        row = result["locations"][0]
        assert row["metrics.cost"] == 25.0
        assert row["metrics.conversion_rate"] == 10.0  # 5/50 * 100

    @patch("adloop.ads.gaql.execute_query")
    def test_with_date_range(self, mock_query, config):
        mock_query.return_value = []

        get_location_performance(
            config,
            customer_id="1234567890",
            date_range_start="2026-03-01",
            date_range_end="2026-03-27",
        )

        call_query = mock_query.call_args[0][2]
        assert "BETWEEN '2026-03-01' AND '2026-03-27'" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_empty_results(self, mock_query, config):
        mock_query.return_value = []

        result = get_location_performance(config, customer_id="1234567890")

        assert result["locations"] == []
        assert result["total_locations"] == 0


# ---------------------------------------------------------------------------
# get_quality_score_details
# ---------------------------------------------------------------------------


class TestGetQualityScoreDetails:
    @patch("adloop.ads.gaql.execute_query")
    def test_default_query(self, mock_query, config):
        mock_query.return_value = [
            {
                "campaign.name": "Test",
                "campaign.id": 111,
                "ad_group.name": "AG1",
                "ad_group_criterion.keyword.text": "test keyword",
                "ad_group_criterion.keyword.match_type": "EXACT",
                "ad_group_criterion.quality_info.quality_score": 7,
                "ad_group_criterion.quality_info.creative_quality_score": "ABOVE_AVERAGE",
                "ad_group_criterion.quality_info.post_click_quality_score": "AVERAGE",
                "ad_group_criterion.quality_info.search_predicted_ctr": "ABOVE_AVERAGE",
                "metrics.impressions": 200,
                "metrics.clicks": 20,
                "metrics.cost_micros": 10_000_000,
                "metrics.conversions": 2,
            }
        ]

        result = get_quality_score_details(config, customer_id="1234567890")

        assert "quality_scores" in result
        assert result["total_keywords"] == 1
        row = result["quality_scores"][0]
        assert row["ad_group_criterion.quality_info.quality_score"] == 7
        assert row["metrics.cost"] == 10.0
        assert row["metrics.cpa"] == 5.0

    @patch("adloop.ads.gaql.execute_query")
    def test_campaign_filter(self, mock_query, config):
        mock_query.return_value = []

        get_quality_score_details(
            config, customer_id="1234567890", campaign_id="999"
        )

        call_query = mock_query.call_args[0][2]
        assert "campaign.id = 999" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_empty_results(self, mock_query, config):
        mock_query.return_value = []

        result = get_quality_score_details(config, customer_id="1234567890")

        assert result["quality_scores"] == []
        assert result["total_keywords"] == 0


# ---------------------------------------------------------------------------
# get_bid_strategy_status
# ---------------------------------------------------------------------------


class TestGetBidStrategyStatus:
    @patch("adloop.ads.gaql.execute_query")
    def test_default_query(self, mock_query, config):
        mock_query.return_value = [
            {
                "campaign.id": 111,
                "campaign.name": "Test Campaign",
                "campaign.status": "ENABLED",
                "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
                "campaign.bidding_strategy_system_status": "LEARNING",
                "campaign_budget.amount_micros": 30_000_000,
                "metrics.conversions": 10,
                "metrics.cost_micros": 100_000_000,
            }
        ]

        result = get_bid_strategy_status(config, customer_id="1234567890")

        assert "strategies" in result
        assert result["total_campaigns"] == 1
        row = result["strategies"][0]
        assert row["campaign.bidding_strategy_system_status"] == "LEARNING"
        assert row["campaign_budget.amount"] == 30.0
        assert row["metrics.cost"] == 100.0

    @patch("adloop.ads.gaql.execute_query")
    def test_campaign_filter(self, mock_query, config):
        mock_query.return_value = []

        get_bid_strategy_status(
            config, customer_id="1234567890", campaign_id="999"
        )

        call_query = mock_query.call_args[0][2]
        assert "campaign.id = 999" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_empty_results(self, mock_query, config):
        mock_query.return_value = []

        result = get_bid_strategy_status(config, customer_id="1234567890")

        assert result["strategies"] == []
        assert result["total_campaigns"] == 0


# ---------------------------------------------------------------------------
# get_budget_pacing
# ---------------------------------------------------------------------------


class TestGetBudgetPacing:
    def _mock_execute(self, budget_rows, spend_rows):
        """Return a side_effect that returns different results per query."""
        def side_effect(config, customer_id, query):
            if "THIS_MONTH" in query:
                return spend_rows
            return budget_rows
        return side_effect

    @patch("adloop.ads.gaql.execute_query")
    def test_basic_pacing(self, mock_query, config):
        budget_rows = [
            {
                "campaign.id": 111,
                "campaign.name": "Test Campaign",
                "campaign.status": "ENABLED",
                "campaign_budget.amount_micros": 10_000_000,  # 10 EUR/day
            }
        ]
        spend_rows = [
            {"campaign.id": 111, "metrics.cost_micros": 5_000_000},
            {"campaign.id": 111, "metrics.cost_micros": 8_000_000},
            {"campaign.id": 111, "metrics.cost_micros": 7_000_000},
        ]
        mock_query.side_effect = self._mock_execute(budget_rows, spend_rows)

        result = get_budget_pacing(config, customer_id="1234567890")

        assert "pacing" in result
        assert result["total_campaigns"] == 1
        row = result["pacing"][0]
        assert row["campaign.id"] == 111
        assert row["daily_budget"] == 10.0
        assert row["month_spend"] == 20.0  # 5 + 8 + 7 = 20 EUR
        assert "days_elapsed" in row
        assert "days_remaining" in row
        assert "projected_month_spend" in row
        assert "pace_pct" in row

    @patch("adloop.ads.gaql.execute_query")
    def test_campaign_filter(self, mock_query, config):
        mock_query.return_value = []

        get_budget_pacing(config, customer_id="1234567890", campaign_id="999")

        # Both queries should have the campaign filter
        for call in mock_query.call_args_list:
            assert "campaign.id = 999" in call[0][2]

    @patch("adloop.ads.gaql.execute_query")
    def test_empty_results(self, mock_query, config):
        mock_query.return_value = []

        result = get_budget_pacing(config, customer_id="1234567890")

        assert result["pacing"] == []
        assert result["total_campaigns"] == 0


# ---------------------------------------------------------------------------
# get_ad_schedule_performance
# ---------------------------------------------------------------------------


class TestGetAdSchedulePerformance:
    @patch("adloop.ads.gaql.execute_query")
    def test_default_query(self, mock_query, config):
        mock_query.return_value = [
            {
                "campaign.name": "Test",
                "campaign.id": 111,
                "segments.day_of_week": "MONDAY",
                "segments.hour": 9,
                "metrics.impressions": 100,
                "metrics.clicks": 10,
                "metrics.ctr": 0.10,
                "metrics.cost_micros": 5_000_000,
                "metrics.conversions": 1,
            }
        ]

        result = get_ad_schedule_performance(config, customer_id="1234567890")

        assert "schedule_performance" in result
        assert result["total_rows"] == 1
        row = result["schedule_performance"][0]
        assert row["segments.day_of_week"] == "MONDAY"
        assert row["segments.hour"] == 9
        assert row["metrics.cost"] == 5.0
        assert row["metrics.conversion_rate"] == 10.0  # 1/10 * 100
        assert row["metrics.cpa"] == 5.0  # 5.0 / 1

    @patch("adloop.ads.gaql.execute_query")
    def test_zero_clicks_conversion_rate(self, mock_query, config):
        mock_query.return_value = [
            {
                "campaign.name": "Test",
                "campaign.id": 111,
                "segments.day_of_week": "SUNDAY",
                "segments.hour": 3,
                "metrics.impressions": 50,
                "metrics.clicks": 0,
                "metrics.ctr": 0.0,
                "metrics.cost_micros": 0,
                "metrics.conversions": 0,
            }
        ]

        result = get_ad_schedule_performance(config, customer_id="1234567890")

        row = result["schedule_performance"][0]
        assert row["metrics.conversion_rate"] == 0.0
        assert "metrics.cpa" not in row  # no conversions = no CPA

    @patch("adloop.ads.gaql.execute_query")
    def test_campaign_filter(self, mock_query, config):
        mock_query.return_value = []

        get_ad_schedule_performance(
            config, customer_id="1234567890", campaign_id="999"
        )

        call_query = mock_query.call_args[0][2]
        assert "campaign.id = 999" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_empty_results(self, mock_query, config):
        mock_query.return_value = []

        result = get_ad_schedule_performance(config, customer_id="1234567890")

        assert result["schedule_performance"] == []
        assert result["total_rows"] == 0


# ---------------------------------------------------------------------------
# get_auction_insights
# ---------------------------------------------------------------------------


class TestGetAuctionInsights:
    @patch("adloop.ads.gaql.execute_query")
    def test_successful_query(self, mock_query, config):
        mock_query.return_value = [
            {
                "campaign.name": "Test",
                "campaign.id": 111,
                "segments.auction_insight_domain": "competitor.com",
                "metrics.auction_insight_search_impression_share": 0.35,
                "metrics.auction_insight_search_overlap_rate": 0.50,
                "metrics.auction_insight_search_outranking_share": 0.40,
                "metrics.auction_insight_search_position_above_rate": 0.20,
                "metrics.auction_insight_search_top_impression_percentage": 0.30,
                "metrics.auction_insight_search_absolute_top_impression_percentage": 0.10,
            }
        ]

        result = get_auction_insights(config, customer_id="1234567890")

        assert "auction_insights" in result
        assert result["total_rows"] == 1

    @patch("adloop.ads.gaql.execute_query")
    def test_not_allowlisted(self, mock_query, config):
        mock_query.side_effect = Exception(
            "QUERY_NOT_ALLOWED: This query type is not supported"
        )

        result = get_auction_insights(config, customer_id="1234567890")

        assert "error" in result
        assert "not available" in result["error"]
        assert "hint" in result

    @patch("adloop.ads.gaql.execute_query")
    def test_other_error_reraises(self, mock_query, config):
        mock_query.side_effect = Exception("NETWORK_ERROR: connection failed")

        with pytest.raises(Exception, match="NETWORK_ERROR"):
            get_auction_insights(config, customer_id="1234567890")

    @patch("adloop.ads.gaql.execute_query")
    def test_campaign_filter(self, mock_query, config):
        mock_query.return_value = []

        get_auction_insights(
            config, customer_id="1234567890", campaign_id="999"
        )

        call_query = mock_query.call_args[0][2]
        assert "campaign.id = 999" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_empty_results(self, mock_query, config):
        mock_query.return_value = []

        result = get_auction_insights(config, customer_id="1234567890")

        assert result["auction_insights"] == []
        assert result["total_rows"] == 0


# ---------------------------------------------------------------------------
# get_keyword_performance
# ---------------------------------------------------------------------------


class TestGetKeywordPerformance:
    @patch("adloop.ads.gaql.execute_query")
    def test_returns_ids(self, mock_query, config):
        mock_query.return_value = [
            {
                "campaign.name": "Campaign A",
                "ad_group.name": "Ad Group 1",
                "ad_group.id": 555,
                "ad_group_criterion.criterion_id": 777,
                "ad_group_criterion.keyword.text": "test keyword",
                "ad_group_criterion.keyword.match_type": "EXACT",
                "ad_group_criterion.quality_info.quality_score": 7,
                "metrics.impressions": 500,
                "metrics.clicks": 50,
                "metrics.ctr": 0.1,
                "metrics.average_cpc": 1_000_000,
                "metrics.cost_micros": 50_000_000,
                "metrics.conversions": 2,
            }
        ]

        result = get_keyword_performance(config, customer_id="1234567890")

        assert result["total_keywords"] == 1
        row = result["keywords"][0]
        assert row["ad_group.id"] == 555
        assert row["ad_group_criterion.criterion_id"] == 777
        assert row["metrics.cost"] == 50.0
        assert row["metrics.cpa"] == 25.0

    @patch("adloop.ads.gaql.execute_query")
    def test_query_includes_id_fields(self, mock_query, config):
        mock_query.return_value = []

        get_keyword_performance(config, customer_id="1234567890")

        call_query = mock_query.call_args[0][2]
        assert "ad_group.id" in call_query
        assert "ad_group_criterion.criterion_id" in call_query


# ---------------------------------------------------------------------------
# get_search_terms
# ---------------------------------------------------------------------------


class TestGetSearchTerms:
    @patch("adloop.ads.gaql.execute_query")
    def test_default_query(self, mock_query, config):
        mock_query.return_value = [
            {
                "search_term_view.search_term": "test query",
                "campaign.name": "Campaign A",
                "ad_group.name": "Ad Group 1",
                "metrics.impressions": 100,
                "metrics.clicks": 10,
                "metrics.cost_micros": 5_000_000,
                "metrics.conversions": 1,
            }
        ]

        result = get_search_terms(config, customer_id="1234567890")

        assert result["total_search_terms"] == 1
        row = result["search_terms"][0]
        assert row["metrics.cost"] == 5.0

    @patch("adloop.ads.gaql.execute_query")
    def test_campaign_filter(self, mock_query, config):
        mock_query.return_value = []

        get_search_terms(
            config, customer_id="1234567890", campaign_id="999"
        )

        call_query = mock_query.call_args[0][2]
        assert "campaign.id = 999" in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_no_campaign_filter_by_default(self, mock_query, config):
        mock_query.return_value = []

        get_search_terms(config, customer_id="1234567890")

        call_query = mock_query.call_args[0][2]
        assert "campaign.id =" not in call_query

    @patch("adloop.ads.gaql.execute_query")
    def test_query_includes_campaign_id_field(self, mock_query, config):
        mock_query.return_value = [
            {
                "search_term_view.search_term": "test",
                "campaign.id": 12345,
                "campaign.name": "Campaign A",
                "ad_group.name": "Ad Group 1",
                "metrics.impressions": 100,
                "metrics.clicks": 10,
                "metrics.cost_micros": 5_000_000,
                "metrics.conversions": 1,
            }
        ]

        result = get_search_terms(config, customer_id="1234567890")

        row = result["search_terms"][0]
        assert row["campaign.id"] == 12345
        # Verify campaign.id is in the SELECT clause
        call_query = mock_query.call_args[0][2]
        assert "campaign.id" in call_query

    def test_invalid_campaign_id_raises(self, config):
        with pytest.raises(ValueError, match="must be numeric"):
            get_search_terms(
                config, customer_id="1234567890", campaign_id="DROP TABLE"
            )
