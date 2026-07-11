"""Tests for the Google Search Console read tools (PR #20 rework)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from adloop.config import AdLoopConfig, GscConfig
from adloop.gsc import reports


@pytest.fixture
def config() -> AdLoopConfig:
    return AdLoopConfig(gsc=GscConfig(site_url="sc-domain:example.com"))


def _fake_client(query_response=None, sites_response=None):
    client = MagicMock()
    client.sites.return_value.list.return_value.execute.return_value = (
        sites_response or {}
    )
    client.searchanalytics.return_value.query.return_value.execute.return_value = (
        query_response or {}
    )
    return client


class TestListSites:
    def test_lists_sites_with_permission_levels(self, config):
        client = _fake_client(sites_response={"siteEntry": [
            {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"},
            {"siteUrl": "https://shop.example.com/"},
        ]})
        with patch("adloop.gsc.client.get_gsc_client", return_value=client):
            result = reports.list_gsc_sites(config)

        assert result["total"] == 2
        assert result["sites"][0]["permission_level"] == "siteOwner"
        assert result["sites"][1]["permission_level"] == "unknown"


class TestRunReport:
    def test_uses_configured_site_when_omitted(self, config):
        client = _fake_client(query_response={"rows": []})
        with patch("adloop.gsc.client.get_gsc_client", return_value=client):
            reports.run_gsc_report(config, dimensions=["query"])

        call = client.searchanalytics.return_value.query.call_args
        assert call.kwargs["siteUrl"] == "sc-domain:example.com"

    def test_requires_a_site_when_none_configured(self):
        result = reports.run_gsc_report(AdLoopConfig(), dimensions=["query"])
        assert "site_url is required" in result["error"]

    def test_passes_dimensions_filters_and_caps_limit(self, config):
        client = _fake_client(query_response={"rows": []})
        filters = [{"filters": [{"dimension": "query", "operator": "contains",
                                 "expression": "adloop"}]}]
        with patch("adloop.gsc.client.get_gsc_client", return_value=client):
            reports.run_gsc_report(
                config,
                dimensions=["query", "page"],
                dimension_filter_groups=filters,
                limit=99_999,
            )

        body = client.searchanalytics.return_value.query.call_args.kwargs["body"]
        assert body["dimensions"] == ["query", "page"]
        assert body["dimensionFilterGroups"] == filters
        assert body["rowLimit"] == 25_000

    def test_rows_are_flattened_with_metrics(self, config):
        client = _fake_client(query_response={"rows": [
            {"keys": ["adloop mcp"], "clicks": 12, "impressions": 340,
             "ctr": 0.0353, "position": 4.2},
        ]})
        with patch("adloop.gsc.client.get_gsc_client", return_value=client):
            result = reports.run_gsc_report(config, dimensions=["query"])

        row = result["rows"][0]
        assert row["query"] == "adloop mcp"
        assert row["clicks"] == 12
        assert row["position"] == 4.2
