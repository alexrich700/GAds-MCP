"""Tests for the PageSpeed Insights tool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from adloop import pagespeed
from adloop.config import AdLoopConfig, PageSpeedConfig


@pytest.fixture
def config() -> AdLoopConfig:
    return AdLoopConfig(pagespeed=PageSpeedConfig(api_key="test-key"))


def _psi_payload(score=0.42, lcp_ms=4200.0, with_field=True):
    payload = {
        "lighthouseResult": {
            "categories": {"performance": {"score": score}},
            "audits": {
                "largest-contentful-paint": {"numericValue": lcp_ms},
                "cumulative-layout-shift": {"numericValue": 0.21},
                "total-blocking-time": {"numericValue": 890.5},
                "first-contentful-paint": {"numericValue": 1900.0},
                "render-blocking-resources": {
                    "title": "Eliminate render-blocking resources",
                    "details": {"type": "opportunity", "overallSavingsMs": 1200},
                },
                "unused-javascript": {
                    "title": "Reduce unused JavaScript",
                    "details": {"type": "opportunity", "overallSavingsMs": 450},
                },
                "tiny-win": {
                    "title": "Too small to mention",
                    "details": {"type": "opportunity", "overallSavingsMs": 40},
                },
            },
        },
    }
    if with_field:
        payload["loadingExperience"] = {
            "overall_category": "SLOW",
            "metrics": {
                "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 3900, "category": "SLOW"},
                "INTERACTION_TO_NEXT_PAINT": {"percentile": 310, "category": "AVERAGE"},
                "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 19, "category": "AVERAGE"},
            },
        }
    return payload


def _fake_get(payload, status=200):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    return MagicMock(return_value=response)


class TestValidation:
    def test_rejects_non_http_urls(self, config):
        result = pagespeed.analyze_page_speed(config, url="ftp://example.com")
        assert "http(s)" in result["error"]

    def test_rejects_unknown_strategy(self, config):
        result = pagespeed.analyze_page_speed(
            config, url="https://example.com", strategy="tablet"
        )
        assert "strategy" in result["error"]


class TestAnalysis:
    def test_parses_score_lab_field_and_opportunities(self, config):
        with patch("requests.get", _fake_get(_psi_payload())) as fake:
            result = pagespeed.analyze_page_speed(config, url="https://example.com/lp")

        assert result["performance_score"] == 42
        assert result["lab"]["lcp_seconds"] == 4.2
        assert result["lab"]["cls"] == 0.21
        assert result["field"]["lcp_p75_seconds"] == 3.9
        assert result["field"]["overall_rating"] == "SLOW"
        # opportunities sorted by savings, sub-100ms noise dropped
        titles = [o["title"] for o in result["top_opportunities"]]
        assert titles == [
            "Eliminate render-blocking resources",
            "Reduce unused JavaScript",
        ]
        # api key + strategy forwarded
        params = fake.call_args.kwargs["params"]
        assert params["key"] == "test-key"
        assert params["strategy"] == "MOBILE"

    def test_insights_flag_slow_page_and_real_user_reality(self, config):
        with patch("requests.get", _fake_get(_psi_payload())):
            result = pagespeed.analyze_page_speed(config, url="https://example.com")

        joined = " ".join(result["insights"])
        assert "Quality Score" in joined
        assert "SLOW" in joined
        assert "2.5s" in joined

    def test_missing_field_data_is_called_out(self, config):
        with patch("requests.get", _fake_get(_psi_payload(with_field=False))):
            result = pagespeed.analyze_page_speed(config, url="https://example.com")

        assert result["field"] == {}
        assert any("CrUX" in i for i in result["insights"])

    def test_keyless_rate_limit_suggests_api_key(self):
        with patch("requests.get", _fake_get({"error": {"message": "quota"}}, status=429)):
            result = pagespeed.analyze_page_speed(
                AdLoopConfig(), url="https://example.com"
            )

        assert "429" in result["error"]
        assert "pagespeed.api_key" in result["error"]
