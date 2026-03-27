"""Tests for GA4 report tools."""

from unittest.mock import MagicMock, patch

import pytest

from adloop.config import AdLoopConfig, GA4Config, SafetyConfig
from adloop.ga4.reports import run_ga4_report


@pytest.fixture
def config():
    return AdLoopConfig(
        ga4=GA4Config(property_id="properties/123456"),
        safety=SafetyConfig(max_daily_budget=50.0, require_dry_run=True),
    )


def _mock_response(rows=None):
    """Build a mock RunReportResponse."""
    response = MagicMock()
    response.dimension_headers = [MagicMock(name="sessionSource")]
    response.metric_headers = [MagicMock(name="sessions")]
    response.row_count = len(rows or [])
    response.rows = rows or []
    # Fix the .name property on headers (MagicMock name= is special)
    response.dimension_headers[0].name = "sessionSource"
    response.metric_headers[0].name = "sessions"
    return response


class TestRunGa4Report:
    @patch("adloop.ga4.client.get_data_client")
    def test_no_filter(self, mock_client_fn, config):
        mock_client = MagicMock()
        mock_client.run_report.return_value = _mock_response()
        mock_client_fn.return_value = mock_client

        run_ga4_report(
            config,
            property_id="properties/123456",
            dimensions=["sessionSource"],
            metrics=["sessions"],
        )

        request = mock_client.run_report.call_args[0][0]
        # No dimension_filter should be set
        assert not request.dimension_filter.filter.field_name

    @patch("adloop.ga4.client.get_data_client")
    def test_single_filter(self, mock_client_fn, config):
        mock_client = MagicMock()
        mock_client.run_report.return_value = _mock_response()
        mock_client_fn.return_value = mock_client

        run_ga4_report(
            config,
            property_id="properties/123456",
            dimensions=["sessionSource"],
            metrics=["sessions"],
            dimension_filter={"sessionSource": "google"},
        )

        request = mock_client.run_report.call_args[0][0]
        dim_filter = request.dimension_filter
        assert dim_filter.filter.field_name == "sessionSource"
        assert dim_filter.filter.string_filter.value == "google"

    @patch("adloop.ga4.client.get_data_client")
    def test_multiple_filters(self, mock_client_fn, config):
        mock_client = MagicMock()
        mock_client.run_report.return_value = _mock_response()
        mock_client_fn.return_value = mock_client

        run_ga4_report(
            config,
            property_id="properties/123456",
            dimensions=["sessionSource", "sessionMedium"],
            metrics=["sessions"],
            dimension_filter={"sessionSource": "google", "sessionMedium": "cpc"},
        )

        request = mock_client.run_report.call_args[0][0]
        dim_filter = request.dimension_filter
        # Multiple filters should be wrapped in and_group
        assert len(dim_filter.and_group.expressions) == 2
        field_names = {
            expr.filter.field_name
            for expr in dim_filter.and_group.expressions
        }
        assert field_names == {"sessionSource", "sessionMedium"}

    def test_no_dimensions_or_metrics(self, config):
        result = run_ga4_report(config, property_id="properties/123456")
        assert result == {"error": "At least one dimension or metric must be specified."}
