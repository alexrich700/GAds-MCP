"""Tests for draft_rsa_replacement validation and plan creation."""

from unittest.mock import patch

import pytest

from adloop.ads.write import draft_rsa_replacement
from adloop.config import AdLoopConfig, AdsConfig, SafetyConfig
from adloop.safety.preview import get_plan, remove_plan


@pytest.fixture
def config():
    return AdLoopConfig(
        ads=AdsConfig(customer_id="1234567890", developer_token="test"),
        safety=SafetyConfig(max_daily_budget=50.0, require_dry_run=True),
    )


VALID_HEADLINES = [
    "Headline One Here",
    "Headline Two Here",
    "Headline Three Here",
    "Headline Four Here",
    "Headline Five Here",
    "Headline Six Here",
    "Headline Seven Here",
    "Headline Eight Here",
]

VALID_DESCRIPTIONS = [
    "This is a valid description that fits within the ninety character limit easily.",
    "Second description for testing purposes, also well within the character limit.",
    "Third description here for completeness and to meet the recommended minimum.",
]

EXISTING_RSA = {
    "ad_group.id": 777,
    "ad_group_ad.ad.id": 12345,
    "ad_group_ad.ad.type": "RESPONSIVE_SEARCH_AD",
    "ad_group_ad.ad.responsive_search_ad.headlines": [
        "Old Headline One",
        "Old Headline Two",
        "Old Headline Three",
    ],
    "ad_group_ad.ad.responsive_search_ad.descriptions": [
        "Old description one that is short enough.",
        "Old description two that is also fine.",
    ],
    "ad_group_ad.ad.final_urls": ["https://example.com/landing"],
    "ad_group_ad.ad.responsive_search_ad.path1": "old",
    "ad_group_ad.ad.responsive_search_ad.path2": "path",
    "ad_group_ad.status": "ENABLED",
}


class TestDraftRsaReplacement:
    @patch("adloop.ads.write._validate_urls", return_value={})
    @patch("adloop.ads.write._fetch_existing_rsa")
    def test_happy_path_returns_preview_with_diff(
        self, mock_fetch, mock_urls, config
    ):
        mock_fetch.return_value = EXISTING_RSA
        result = draft_rsa_replacement(
            config,
            customer_id="1234567890",
            ad_id="12345",
            headlines=VALID_HEADLINES,
            descriptions=VALID_DESCRIPTIONS,
            final_url="https://example.com/new",
            path1="new",
            path2="page",
        )
        assert "plan_id" in result
        assert result["operation"] == "replace_responsive_search_ad"
        assert "diff" in result
        assert result["diff"]["old"]["headlines"] == [
            {"text": h, "pinned_to": None}
            for h in EXISTING_RSA["ad_group_ad.ad.responsive_search_ad.headlines"]
        ]
        assert result["diff"]["new"]["headlines"] == [
            {"text": h, "pinned_to": None} for h in VALID_HEADLINES
        ]
        assert result["diff"]["old_ad_action"] == "REMOVE"

        # Verify plan stored correctly
        plan = get_plan(result["plan_id"])
        assert plan is not None
        assert plan.operation == "replace_responsive_search_ad"
        assert plan.entity_id == "12345"
        assert plan.changes["ad_group_id"] == "777"
        assert plan.changes["old_ad_id"] == "12345"
        assert plan.requires_double_confirm  # default is remove
        remove_plan(result["plan_id"])

    def test_missing_ad_id(self, config):
        result = draft_rsa_replacement(
            config,
            customer_id="1234567890",
            ad_id="",
            headlines=VALID_HEADLINES,
            descriptions=VALID_DESCRIPTIONS,
        )
        assert "error" in result
        details = result.get("details", [])
        error_text = result.get("error", "") + " ".join(details)
        assert "ad_id" in error_text.lower()

    @patch("adloop.ads.write._fetch_existing_rsa", return_value=None)
    def test_ad_not_found(self, mock_fetch, config):
        result = draft_rsa_replacement(
            config,
            customer_id="1234567890",
            ad_id="99999",
            headlines=VALID_HEADLINES,
            descriptions=VALID_DESCRIPTIONS,
        )
        assert "error" in result
        assert "99999" in result["error"]

    @patch("adloop.ads.write._fetch_existing_rsa")
    def test_ad_already_removed(self, mock_fetch, config):
        removed_rsa = {**EXISTING_RSA, "ad_group_ad.status": "REMOVED"}
        mock_fetch.return_value = removed_rsa
        result = draft_rsa_replacement(
            config,
            customer_id="1234567890",
            ad_id="12345",
            headlines=VALID_HEADLINES,
            descriptions=VALID_DESCRIPTIONS,
        )
        assert "error" in result
        assert "removed" in result["error"].lower()

    @patch("adloop.ads.write._validate_urls", return_value={})
    @patch("adloop.ads.write._fetch_existing_rsa")
    def test_too_few_headlines(self, mock_fetch, mock_urls, config):
        mock_fetch.return_value = EXISTING_RSA
        result = draft_rsa_replacement(
            config,
            customer_id="1234567890",
            ad_id="12345",
            headlines=["H1", "H2"],  # Need at least 3
            descriptions=VALID_DESCRIPTIONS,
            final_url="https://example.com",
        )
        assert "error" in result

    @patch("adloop.ads.write._validate_urls", return_value={})
    @patch("adloop.ads.write._fetch_existing_rsa")
    def test_headline_over_30_chars(self, mock_fetch, mock_urls, config):
        mock_fetch.return_value = EXISTING_RSA
        long_headlines = [
            "This headline is way over the thirty character maximum allowed",
            "Headline Two",
            "Headline Three",
        ]
        result = draft_rsa_replacement(
            config,
            customer_id="1234567890",
            ad_id="12345",
            headlines=long_headlines,
            descriptions=VALID_DESCRIPTIONS,
            final_url="https://example.com",
        )
        assert "error" in result

    @patch("adloop.ads.write._validate_urls", return_value={})
    @patch("adloop.ads.write._fetch_existing_rsa")
    def test_inherits_final_url_from_old_ad(self, mock_fetch, mock_urls, config):
        mock_fetch.return_value = EXISTING_RSA
        result = draft_rsa_replacement(
            config,
            customer_id="1234567890",
            ad_id="12345",
            headlines=VALID_HEADLINES,
            descriptions=VALID_DESCRIPTIONS,
            # no final_url provided — should inherit from old ad
        )
        assert "plan_id" in result
        assert result["diff"]["new"]["final_url"] == "https://example.com/landing"

        remove_plan(result["plan_id"])

    @patch("adloop.ads.write._validate_urls", return_value={})
    @patch("adloop.ads.write._fetch_existing_rsa")
    def test_default_removes_old_with_double_confirm(
        self, mock_fetch, mock_urls, config
    ):
        """Default behavior removes old ad, which requires double confirmation."""
        mock_fetch.return_value = EXISTING_RSA
        result = draft_rsa_replacement(
            config,
            customer_id="1234567890",
            ad_id="12345",
            headlines=VALID_HEADLINES,
            descriptions=VALID_DESCRIPTIONS,
        )
        assert "plan_id" in result
        assert result["diff"]["old_ad_action"] == "REMOVE"

        plan = get_plan(result["plan_id"])
        assert plan.requires_double_confirm is True
        remove_plan(result["plan_id"])

    @patch("adloop.ads.write._validate_urls", return_value={})
    @patch("adloop.ads.write._fetch_existing_rsa")
    def test_keep_old_paused_no_double_confirm(
        self, mock_fetch, mock_urls, config
    ):
        """When remove_old=False, old ad is paused (no double confirm needed)."""
        mock_fetch.return_value = EXISTING_RSA
        result = draft_rsa_replacement(
            config,
            customer_id="1234567890",
            ad_id="12345",
            headlines=VALID_HEADLINES,
            descriptions=VALID_DESCRIPTIONS,
            remove_old=False,
        )
        assert result["diff"]["old_ad_action"] == "PAUSE"
        plan = get_plan(result["plan_id"])
        assert plan.requires_double_confirm is not True
        remove_plan(result["plan_id"])

    def test_blocked_operation(self, config):
        config.safety.blocked_operations = ["replace_responsive_search_ad"]
        result = draft_rsa_replacement(
            config,
            customer_id="1234567890",
            ad_id="12345",
            headlines=VALID_HEADLINES,
            descriptions=VALID_DESCRIPTIONS,
        )
        assert "error" in result
        config.safety.blocked_operations = []

    @patch(
        "adloop.ads.write._validate_urls",
        return_value={"https://broken.example.com": "Connection refused"},
    )
    @patch("adloop.ads.write._fetch_existing_rsa")
    def test_url_validation_failure(self, mock_fetch, mock_urls, config):
        mock_fetch.return_value = EXISTING_RSA
        result = draft_rsa_replacement(
            config,
            customer_id="1234567890",
            ad_id="12345",
            headlines=VALID_HEADLINES,
            descriptions=VALID_DESCRIPTIONS,
            final_url="https://broken.example.com",
        )
        assert "error" in result
        assert "not reachable" in result.get("details", [""])[0]

    @patch("adloop.ads.write._validate_urls", return_value={})
    @patch("adloop.ads.write._fetch_existing_rsa")
    def test_old_copy_in_changes(self, mock_fetch, mock_urls, config):
        """The plan changes should include old_copy for audit/diff purposes."""
        mock_fetch.return_value = EXISTING_RSA
        result = draft_rsa_replacement(
            config,
            customer_id="1234567890",
            ad_id="12345",
            headlines=VALID_HEADLINES,
            descriptions=VALID_DESCRIPTIONS,
        )
        plan = get_plan(result["plan_id"])
        assert "old_copy" in plan.changes
        assert plan.changes["old_copy"]["headlines"] == [
            {"text": h, "pinned_to": None}
            for h in EXISTING_RSA["ad_group_ad.ad.responsive_search_ad.headlines"]
        ]
        remove_plan(result["plan_id"])
