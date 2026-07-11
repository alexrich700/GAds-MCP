"""Tests for the Merchant Center read tools (Merchant API v1)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from adloop.config import AdLoopConfig
from adloop.merchant import read


@pytest.fixture
def config() -> AdLoopConfig:
    return AdLoopConfig()


def _patch_get(responses: dict):
    """Map path-prefix → payload for adloop.merchant.client.merchant_get."""
    calls: list[tuple[str, dict]] = []

    def fake(config, path, params=None):
        calls.append((path, params or {}))
        for prefix, payload in responses.items():
            if path.endswith(prefix):
                return payload
        raise AssertionError(f"unexpected Merchant API path: {path}")

    return calls, patch("adloop.merchant.client.merchant_get", side_effect=fake)


class TestListAccounts:
    def test_lists_accounts_across_pages(self, config):
        page1 = {"accounts": [
            {"name": "accounts/111", "accountName": "Main Shop"},
        ], "nextPageToken": "t2"}
        page2 = {"accounts": [
            {"name": "accounts/222", "accountName": "Test", "testAccount": True},
        ]}
        pages = iter([page1, page2])
        with patch("adloop.merchant.client.merchant_get",
                   side_effect=lambda c, p, q=None: next(pages)):
            result = read.list_merchant_accounts(config)

        assert result["total"] == 2
        assert result["accounts"][0] == {
            "account_id": "111", "name": "Main Shop", "test_account": False,
        }
        assert result["accounts"][1]["test_account"] is True

    def test_no_accounts_yields_guidance(self, config):
        with patch("adloop.merchant.client.merchant_get", return_value={}):
            result = read.list_merchant_accounts(config)

        assert result["total"] == 0
        assert any("People and access" in i for i in result["insights"])


class TestFeedHealth:
    def _responses(self):
        return {
            "aggregateProductStatuses": {"aggregateProductStatuses": [
                {
                    "reportingContext": "SHOPPING_ADS",
                    "countryCode": "DE",
                    "statistics": {"approvedCount": "900", "pendingCount": "20",
                                   "disapprovedCount": "80"},
                    "issues": [
                        {"issueType": "image_link_broken", "severity": "ERROR",
                         "numProducts": "60",
                         "documentationUri": "https://support.google.com/y"},
                        {"issueType": "price_mismatch", "severity": "ERROR",
                         "numProducts": "20",
                         "documentationUri": "https://support.google.com/z"},
                    ],
                },
            ]},
            "issues": {"accountIssues": [
                {"title": "Missing return policy", "severity": "CRITICAL",
                 "detail": "Add a return policy",
                 "impactedDestinations": [
                     {"reportingContext": "SHOPPING_ADS"},
                 ],
                 "documentationUri": "https://support.google.com/x"},
            ]},
        }

    def test_requires_numeric_account_id(self, config):
        result = read.get_merchant_feed_health(config, account_id="my-shop")
        assert "numeric" in result["error"]

    def test_summarizes_disapprovals_and_ranks_issues(self, config):
        calls, patcher = _patch_get(self._responses())
        with patcher:
            result = read.get_merchant_feed_health(config, account_id="111")

        assert [c[0] for c in calls] == [
            "accounts/111/aggregateProductStatuses",
            "accounts/111/issues",
        ]
        assert result["totals"] == {"approved": 900, "pending": 20,
                                    "disapproved": 80}
        assert result["top_item_issues"][0]["issue"] == "image_link_broken"
        assert result["top_item_issues"][0]["affected_products"] == 60
        assert result["account_issues"][0]["severity"] == "CRITICAL"
        assert result["account_issues"][0]["reporting_contexts"] == ["SHOPPING_ADS"]
        joined = " ".join(result["insights"])
        assert "DISAPPROVED" in joined and "8.0%" in joined
        assert "CRITICAL" in joined

    def test_healthy_feed_says_so(self, config):
        _, patcher = _patch_get({
            "aggregateProductStatuses": {"aggregateProductStatuses": [
                {"reportingContext": "SHOPPING_ADS",
                 "statistics": {"approvedCount": "500", "pendingCount": "0",
                                "disapprovedCount": "0"}},
            ]},
            "issues": {},
        })
        with patcher:
            result = read.get_merchant_feed_health(config, account_id="111")

        assert any("healthy" in i for i in result["insights"])
