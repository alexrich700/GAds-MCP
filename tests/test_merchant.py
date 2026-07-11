"""Tests for the Merchant Center read tools."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from adloop.config import AdLoopConfig
from adloop.merchant import read


@pytest.fixture
def config() -> AdLoopConfig:
    return AdLoopConfig()


def _client(authinfo=None, accountstatus=None):
    client = MagicMock()
    client.accounts.return_value.authinfo.return_value.execute.return_value = (
        authinfo or {}
    )
    client.accountstatuses.return_value.get.return_value.execute.return_value = (
        accountstatus or {}
    )
    return client


class TestListAccounts:
    def test_distinguishes_standalone_and_mca(self, config):
        client = _client(authinfo={"accountIdentifiers": [
            {"merchantId": "111"},
            {"aggregatorId": "222"},
        ]})
        with patch("adloop.merchant.client.get_merchant_client", return_value=client):
            result = read.list_merchant_accounts(config)

        assert result["total"] == 2
        assert result["accounts"][0] == {"merchant_id": "111", "type": "standalone"}
        assert "MCA" in result["accounts"][1]["type"]

    def test_no_accounts_yields_guidance(self, config):
        with patch("adloop.merchant.client.get_merchant_client", return_value=_client()):
            result = read.list_merchant_accounts(config)

        assert result["total"] == 0
        assert any("People and access" in i for i in result["insights"])


class TestFeedHealth:
    def _status(self):
        return {
            "accountLevelIssues": [
                {"title": "Missing return policy", "severity": "error",
                 "country": "DE", "documentation": "https://support.google.com/x"},
            ],
            "products": [
                {
                    "destination": "Shopping", "channel": "online", "country": "DE",
                    "statistics": {"active": "900", "pending": "20",
                                   "disapproved": "80", "expiring": "5"},
                    "itemLevelIssues": [
                        {"code": "image_link_broken", "servability": "disapproved",
                         "description": "Broken image link", "numItems": "60",
                         "resolution": "merchant_action",
                         "documentation": "https://support.google.com/y"},
                        {"code": "price_mismatch", "servability": "disapproved",
                         "description": "Price mismatch", "numItems": "20",
                         "resolution": "merchant_action",
                         "documentation": "https://support.google.com/z"},
                    ],
                },
            ],
        }

    def test_requires_numeric_merchant_id(self, config):
        result = read.get_merchant_feed_health(config, merchant_id="my-shop")
        assert "numeric" in result["error"]

    def test_account_id_defaults_to_merchant_id(self, config):
        client = _client(accountstatus=self._status())
        with patch("adloop.merchant.client.get_merchant_client", return_value=client):
            result = read.get_merchant_feed_health(config, merchant_id="111")

        call = client.accountstatuses.return_value.get.call_args
        assert call.kwargs == {"merchantId": "111", "accountId": "111"}
        assert result["account_id"] == "111"

    def test_summarizes_disapprovals_and_ranks_issues(self, config):
        client = _client(accountstatus=self._status())
        with patch("adloop.merchant.client.get_merchant_client", return_value=client):
            result = read.get_merchant_feed_health(config, merchant_id="111")

        assert result["totals"]["disapproved"] == 80
        assert result["top_item_issues"][0]["code"] == "image_link_broken"
        assert result["top_item_issues"][0]["affected_items"] == 60
        joined = " ".join(result["insights"])
        assert "DISAPPROVED" in joined and "8.0%" in joined
        assert "suspend" in joined  # account-level error surfaced

    def test_healthy_feed_says_so(self, config):
        client = _client(accountstatus={"products": [{
            "destination": "Shopping",
            "statistics": {"active": "500", "pending": "0",
                           "disapproved": "0", "expiring": "0"},
        }]})
        with patch("adloop.merchant.client.get_merchant_client", return_value=client):
            result = read.get_merchant_feed_health(config, merchant_id="111")

        assert any("healthy" in i for i in result["insights"])
