"""Tests for cross-reference tools (GA4 + Ads combined)."""

from unittest.mock import patch

import pytest

from adloop.config import AdLoopConfig, AdsConfig, GA4Config, SafetyConfig
from adloop.crossref import analyze_campaign_conversions


@pytest.fixture
def config():
    return AdLoopConfig(
        ads=AdsConfig(customer_id="1234567890", developer_token="test"),
        ga4=GA4Config(property_id="properties/123456"),
        safety=SafetyConfig(max_daily_budget=50.0, require_dry_run=True),
    )


class TestAnalyzeCampaignConversions:
    @patch("adloop.ga4.reports.run_ga4_report")
    @patch("adloop.ads.read.get_campaign_performance")
    def test_returns_per_campaign_with_id(self, mock_ads, mock_ga4, config):
        mock_ads.return_value = {
            "campaigns": [
                {
                    "campaign.id": 111,
                    "campaign.name": "Campaign A",
                    "campaign.status": "ENABLED",
                    "metrics.clicks": 100,
                    "metrics.cost": 50.0,
                    "metrics.conversions": 10,
                },
                {
                    "campaign.id": 222,
                    "campaign.name": "Campaign B",
                    "campaign.status": "ENABLED",
                    "metrics.clicks": 200,
                    "metrics.cost": 100.0,
                    "metrics.conversions": 5,
                },
            ],
        }
        mock_ga4.return_value = {
            "rows": [
                {
                    "sessionCampaignName": "Campaign A",
                    "sessionSource": "google",
                    "sessionMedium": "cpc",
                    "sessions": "80",
                    "conversions": "8",
                    "engagedSessions": "60",
                    "totalUsers": "75",
                },
                {
                    "sessionCampaignName": "Campaign B",
                    "sessionSource": "google",
                    "sessionMedium": "cpc",
                    "sessions": "150",
                    "conversions": "3",
                    "engagedSessions": "120",
                    "totalUsers": "140",
                },
                {
                    "sessionCampaignName": "(not set)",
                    "sessionSource": "organic",
                    "sessionMedium": "search",
                    "sessions": "500",
                    "conversions": "20",
                    "engagedSessions": "400",
                    "totalUsers": "480",
                },
            ],
        }

        result = analyze_campaign_conversions(
            config, customer_id="1234567890", property_id="properties/123456"
        )

        assert len(result["campaigns"]) == 2

        camp_a = result["campaigns"][0]
        assert camp_a["campaign_id"] == "111"
        assert camp_a["campaign_name"] == "Campaign A"
        assert camp_a["ads_clicks"] == 100
        assert camp_a["ga4_paid_sessions"] == 80
        assert camp_a["ga4_paid_conversions"] == 8
        assert "conversion_discrepancy_pct" in camp_a

        camp_b = result["campaigns"][1]
        assert camp_b["campaign_id"] == "222"
        assert camp_b["campaign_name"] == "Campaign B"

        # Non-paid channels should be present
        assert len(result["non_paid_channels"]) >= 1

    @patch("adloop.ga4.reports.run_ga4_report")
    @patch("adloop.ads.read.get_campaign_performance")
    def test_conversion_discrepancy_pct(self, mock_ads, mock_ga4, config):
        mock_ads.return_value = {
            "campaigns": [
                {
                    "campaign.id": 111,
                    "campaign.name": "Test",
                    "campaign.status": "ENABLED",
                    "metrics.clicks": 100,
                    "metrics.cost": 50.0,
                    "metrics.conversions": 10,
                },
            ],
        }
        mock_ga4.return_value = {
            "rows": [
                {
                    "sessionCampaignName": "Test",
                    "sessionSource": "google",
                    "sessionMedium": "cpc",
                    "sessions": "80",
                    "conversions": "6",
                    "engagedSessions": "60",
                    "totalUsers": "75",
                },
            ],
        }

        result = analyze_campaign_conversions(
            config, customer_id="1234567890", property_id="properties/123456"
        )

        camp = result["campaigns"][0]
        # Ads: 10, GA4: 6 -> discrepancy = |10-6|/10 * 100 = 40%
        assert camp["conversion_discrepancy_pct"] == 40.0

    @patch("adloop.ga4.reports.run_ga4_report")
    @patch("adloop.ads.read.get_campaign_performance")
    def test_campaign_name_filter(self, mock_ads, mock_ga4, config):
        mock_ads.return_value = {
            "campaigns": [
                {
                    "campaign.id": 111,
                    "campaign.name": "Campaign A",
                    "campaign.status": "ENABLED",
                    "metrics.clicks": 100,
                    "metrics.cost": 50.0,
                    "metrics.conversions": 10,
                },
                {
                    "campaign.id": 222,
                    "campaign.name": "Campaign B",
                    "campaign.status": "ENABLED",
                    "metrics.clicks": 200,
                    "metrics.cost": 100.0,
                    "metrics.conversions": 5,
                },
            ],
        }
        mock_ga4.return_value = {"rows": []}

        result = analyze_campaign_conversions(
            config,
            customer_id="1234567890",
            property_id="properties/123456",
            campaign_name="Campaign A",
        )

        assert len(result["campaigns"]) == 1
        assert result["campaigns"][0]["campaign_name"] == "Campaign A"
