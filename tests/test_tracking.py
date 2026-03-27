"""Tests for tracking validation tools."""

from unittest.mock import patch

import pytest

from adloop.config import AdLoopConfig, AdsConfig, GA4Config, SafetyConfig
from adloop.tracking import validate_tracking


@pytest.fixture
def config():
    return AdLoopConfig(
        ads=AdsConfig(customer_id="1234567890", developer_token="test"),
        ga4=GA4Config(property_id="properties/123456"),
        safety=SafetyConfig(max_daily_budget=50.0, require_dry_run=True),
    )


class TestValidateTracking:
    @patch("adloop.ga4.tracking.get_tracking_events")
    def test_without_customer_id(self, mock_ga4, config):
        mock_ga4.return_value = {
            "rows": [
                {"eventName": "purchase", "eventCount": "50"},
                {"eventName": "page_view", "eventCount": "1000"},
            ],
            "date_range": {"start": "28daysAgo", "end": "today"},
        }

        result = validate_tracking(
            config,
            expected_events=["purchase", "sign_up"],
        )

        assert len(result["matched"]) == 1
        assert result["matched"][0]["event_name"] == "purchase"
        assert "sign_up" in result["missing_from_ga4"]
        assert "ads_conversion_actions" not in result

    @patch("adloop.ads.gaql.execute_query")
    @patch("adloop.ga4.tracking.get_tracking_events")
    def test_with_customer_id(self, mock_ga4, mock_gaql, config):
        mock_ga4.return_value = {
            "rows": [
                {"eventName": "purchase", "eventCount": "50"},
            ],
            "date_range": {"start": "28daysAgo", "end": "today"},
        }
        mock_gaql.return_value = [
            {
                "conversion_action.name": "purchase",
                "conversion_action.type": "WEBPAGE",
                "conversion_action.status": "ENABLED",
            },
            {
                "conversion_action.name": "phone_call",
                "conversion_action.type": "PHONE_CALL",
                "conversion_action.status": "ENABLED",
            },
        ]

        result = validate_tracking(
            config,
            expected_events=["purchase", "sign_up"],
            customer_id="1234567890",
        )

        assert "ads_conversion_actions" in result
        assert len(result["ads_conversion_actions"]) == 2
        # sign_up should be flagged as missing from Ads conversion actions
        ads_missing_insight = [
            i for i in result["insights"]
            if "no matching Google Ads conversion action" in i
        ]
        assert len(ads_missing_insight) == 1
        assert "sign_up" in ads_missing_insight[0]

    @patch("adloop.ads.gaql.execute_query")
    @patch("adloop.ga4.tracking.get_tracking_events")
    def test_customer_id_api_error(self, mock_ga4, mock_gaql, config):
        mock_ga4.return_value = {
            "rows": [
                {"eventName": "purchase", "eventCount": "50"},
            ],
            "date_range": {"start": "28daysAgo", "end": "today"},
        }
        mock_gaql.side_effect = Exception("API error")

        result = validate_tracking(
            config,
            expected_events=["purchase"],
            customer_id="1234567890",
        )

        error_insight = [
            i for i in result["insights"]
            if "Could not retrieve Google Ads conversion actions" in i
        ]
        assert len(error_insight) == 1
        assert "ads_conversion_actions" not in result
