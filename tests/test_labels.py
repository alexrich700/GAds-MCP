"""Tests for label list / draft / apply / unapply tools."""

from unittest.mock import patch

import pytest

from adloop.ads.labels import (
    draft_apply_label,
    draft_label,
    draft_unapply_label,
    list_labels,
)
from adloop.config import AdLoopConfig, AdsConfig, GA4Config, SafetyConfig


@pytest.fixture
def config():
    return AdLoopConfig(
        ads=AdsConfig(customer_id="1234567890", developer_token="test"),
        ga4=GA4Config(property_id="properties/123456"),
        safety=SafetyConfig(),
    )


# ---------------------------------------------------------------------------
# list_labels
# ---------------------------------------------------------------------------


class TestListLabels:
    @patch("adloop.ads.gaql.execute_query")
    def test_returns_labels(self, mock_query, config):
        mock_query.return_value = [
            {
                "label.id": 1,
                "label.name": "Test Run",
                "label.status": "ENABLED",
                "label.text_label.description": "",
                "label.text_label.background_color": "#FF0000",
            }
        ]

        result = list_labels(config, customer_id="1234567890")

        assert result["total_labels"] == 1
        assert result["labels"][0]["label.name"] == "Test Run"

    @patch("adloop.ads.gaql.execute_query")
    def test_filters_removed(self, mock_query, config):
        mock_query.return_value = []

        list_labels(config, customer_id="1234567890")

        call_query = mock_query.call_args[0][2]
        assert "label.status != 'REMOVED'" in call_query


# ---------------------------------------------------------------------------
# draft_label
# ---------------------------------------------------------------------------


class TestDraftLabel:
    def test_accepts_valid(self, config):
        result = draft_label(config, customer_id="1234567890", name="Q2 Tests")

        assert "error" not in result
        assert result["operation"] == "create_label"

    def test_requires_name(self, config):
        result = draft_label(config, customer_id="1234567890", name="")

        assert "error" in result
        details = " ".join(result["details"])
        assert "name is required" in details

    def test_validates_hex_color(self, config):
        result = draft_label(
            config,
            customer_id="1234567890",
            name="x",
            background_color="not-hex",
        )

        assert "error" in result

    def test_accepts_valid_hex_color(self, config):
        result = draft_label(
            config,
            customer_id="1234567890",
            name="x",
            background_color="#FF5733",
        )

        assert "error" not in result


# ---------------------------------------------------------------------------
# draft_apply_label
# ---------------------------------------------------------------------------


class TestDraftApplyLabel:
    def test_accepts_campaign(self, config):
        result = draft_apply_label(
            config,
            customer_id="1234567890",
            entity_type="campaign",
            entity_id="22488112473",
            label_id="42",
        )

        assert "error" not in result
        assert result["operation"] == "apply_label"

    def test_rejects_unknown_entity_type(self, config):
        result = draft_apply_label(
            config,
            customer_id="1234567890",
            entity_type="asset_group",
            entity_id="6572147947",
            label_id="42",
        )

        assert "error" in result

    def test_requires_label_id(self, config):
        result = draft_apply_label(
            config,
            customer_id="1234567890",
            entity_type="campaign",
            entity_id="22488112473",
            label_id="",
        )

        assert "error" in result


# ---------------------------------------------------------------------------
# draft_unapply_label
# ---------------------------------------------------------------------------


class TestDraftUnapplyLabel:
    def test_accepts_keyword(self, config):
        result = draft_unapply_label(
            config,
            customer_id="1234567890",
            entity_type="keyword",
            entity_id="111~222",
            label_id="42",
        )

        assert "error" not in result
        assert result["operation"] == "unapply_label"

    def test_rejects_unknown_entity_type(self, config):
        result = draft_unapply_label(
            config,
            customer_id="1234567890",
            entity_type="campaign_asset",
            entity_id="x",
            label_id="42",
        )

        assert "error" in result
