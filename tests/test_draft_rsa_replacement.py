"""Tests for draft_rsa_replacement validation, plan creation, and pinning."""

from unittest.mock import patch

import pytest

from adloop.ads.write import (
    _normalize_assets,
    _validate_rsa,
    draft_responsive_search_ad,
    draft_rsa_replacement,
)
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


class TestNormalizeAssets:
    """Tests for the _normalize_assets helper."""

    def test_plain_strings(self):
        result = _normalize_assets(["Hello", "World"])
        assert result == [
            {"text": "Hello", "pinned_to": None},
            {"text": "World", "pinned_to": None},
        ]

    def test_dicts_with_pinning(self):
        result = _normalize_assets([
            {"text": "Pinned One", "pinned_to": "HEADLINE_1"},
            {"text": "Unpinned"},
        ])
        assert result == [
            {"text": "Pinned One", "pinned_to": "HEADLINE_1"},
            {"text": "Unpinned", "pinned_to": None},
        ]

    def test_mixed_str_and_dict(self):
        result = _normalize_assets([
            "Plain string",
            {"text": "Pinned", "pinned_to": "HEADLINE_2"},
            {"text": "Dict no pin"},
        ])
        assert len(result) == 3
        assert result[0] == {"text": "Plain string", "pinned_to": None}
        assert result[1] == {"text": "Pinned", "pinned_to": "HEADLINE_2"}
        assert result[2] == {"text": "Dict no pin", "pinned_to": None}

    def test_empty_list(self):
        assert _normalize_assets([]) == []


class TestValidateRsaPinning:
    """Tests for pinning validation in _validate_rsa."""

    def test_valid_headline_pins(self):
        headlines = [
            {"text": "H1", "pinned_to": "HEADLINE_1"},
            {"text": "H2", "pinned_to": "HEADLINE_2"},
            {"text": "H3", "pinned_to": "HEADLINE_3"},
        ]
        descs = [
            {"text": "D1 description that is long enough.", "pinned_to": None},
            {"text": "D2 description that is also fine.", "pinned_to": None},
        ]
        errors = _validate_rsa("ag123", headlines, descs, "https://example.com")
        assert errors == []

    def test_valid_description_pins(self):
        headlines = [
            {"text": "H1", "pinned_to": None},
            {"text": "H2", "pinned_to": None},
            {"text": "H3", "pinned_to": None},
        ]
        descs = [
            {"text": "D1 description pinned to slot.", "pinned_to": "DESCRIPTION_1"},
            {"text": "D2 description pinned too.", "pinned_to": "DESCRIPTION_2"},
        ]
        errors = _validate_rsa("ag123", headlines, descs, "https://example.com")
        assert errors == []

    def test_invalid_headline_pin_rejected(self):
        headlines = [
            {"text": "H1", "pinned_to": "HEADLINE_4"},
            {"text": "H2", "pinned_to": None},
            {"text": "H3", "pinned_to": None},
        ]
        descs = [
            {"text": "D1 description text here.", "pinned_to": None},
            {"text": "D2 description text here.", "pinned_to": None},
        ]
        errors = _validate_rsa("ag123", headlines, descs, "https://example.com")
        assert any("HEADLINE_4" in e for e in errors)

    def test_description_pin_on_headline_rejected(self):
        headlines = [
            {"text": "H1", "pinned_to": "DESCRIPTION_1"},
            {"text": "H2", "pinned_to": None},
            {"text": "H3", "pinned_to": None},
        ]
        descs = [
            {"text": "D1 description text here.", "pinned_to": None},
            {"text": "D2 description text here.", "pinned_to": None},
        ]
        errors = _validate_rsa("ag123", headlines, descs, "https://example.com")
        assert any("DESCRIPTION_1" in e for e in errors)

    def test_headline_pin_on_description_rejected(self):
        headlines = [
            {"text": "H1", "pinned_to": None},
            {"text": "H2", "pinned_to": None},
            {"text": "H3", "pinned_to": None},
        ]
        descs = [
            {"text": "D1 description text here.", "pinned_to": "HEADLINE_1"},
            {"text": "D2 description text here.", "pinned_to": None},
        ]
        errors = _validate_rsa("ag123", headlines, descs, "https://example.com")
        assert any("HEADLINE_1" in e for e in errors)

    def test_missing_text_rejected(self):
        headlines = [
            {"text": "", "pinned_to": None},
            {"text": "H2", "pinned_to": None},
            {"text": "H3", "pinned_to": None},
        ]
        descs = [
            {"text": "D1 description text here.", "pinned_to": None},
            {"text": "D2 description text here.", "pinned_to": None},
        ]
        errors = _validate_rsa("ag123", headlines, descs, "https://example.com")
        assert any("missing" in e.lower() for e in errors)


class TestDraftRsaWithPinning:
    """Tests for pinning in draft_responsive_search_ad."""

    @patch("adloop.ads.write._validate_urls", return_value={})
    def test_pinned_headlines_stored_in_plan(self, mock_urls, config):
        headlines = [
            {"text": "Pinned Headline", "pinned_to": "HEADLINE_1"},
            "Unpinned Headline Two",
            "Unpinned Headline Three",
        ]
        descs = VALID_DESCRIPTIONS
        result = draft_responsive_search_ad(
            config,
            customer_id="1234567890",
            ad_group_id="ag123",
            headlines=headlines,
            descriptions=descs,
            final_url="https://example.com",
        )
        assert "plan_id" in result
        plan = get_plan(result["plan_id"])
        stored = plan.changes["headlines"]
        assert stored[0] == {"text": "Pinned Headline", "pinned_to": "HEADLINE_1"}
        assert stored[1] == {"text": "Unpinned Headline Two", "pinned_to": None}
        remove_plan(result["plan_id"])

    @patch("adloop.ads.write._validate_urls", return_value={})
    def test_pinned_descriptions_stored_in_plan(self, mock_urls, config):
        headlines = VALID_HEADLINES
        descs = [
            {"text": "Pinned desc stored in plan changes.", "pinned_to": "DESCRIPTION_1"},
            "Unpinned description number two for testing.",
        ]
        result = draft_responsive_search_ad(
            config,
            customer_id="1234567890",
            ad_group_id="ag123",
            headlines=headlines,
            descriptions=descs,
            final_url="https://example.com",
        )
        assert "plan_id" in result
        plan = get_plan(result["plan_id"])
        stored = plan.changes["descriptions"]
        assert stored[0]["pinned_to"] == "DESCRIPTION_1"
        assert stored[1]["pinned_to"] is None
        remove_plan(result["plan_id"])

    @patch("adloop.ads.write._validate_urls", return_value={})
    def test_invalid_pin_rejected(self, mock_urls, config):
        headlines = [
            {"text": "Bad Pin", "pinned_to": "HEADLINE_99"},
            "H2",
            "H3",
        ]
        descs = VALID_DESCRIPTIONS
        result = draft_responsive_search_ad(
            config,
            customer_id="1234567890",
            ad_group_id="ag123",
            headlines=headlines,
            descriptions=descs,
            final_url="https://example.com",
        )
        assert "error" in result
        assert "HEADLINE_99" in str(result["details"])


class TestDraftRsaReplacementWithPinning:
    """Tests for pinning in draft_rsa_replacement."""

    @patch("adloop.ads.write._validate_urls", return_value={})
    @patch("adloop.ads.write._fetch_existing_rsa")
    def test_pinned_new_headlines_in_diff(self, mock_fetch, mock_urls, config):
        mock_fetch.return_value = EXISTING_RSA
        headlines = [
            {"text": "Pinned Replacement", "pinned_to": "HEADLINE_1"},
            "Replacement Two",
            "Replacement Three",
        ]
        result = draft_rsa_replacement(
            config,
            customer_id="1234567890",
            ad_id="12345",
            headlines=headlines,
            descriptions=VALID_DESCRIPTIONS,
        )
        assert "plan_id" in result
        new_h = result["diff"]["new"]["headlines"]
        assert new_h[0] == {"text": "Pinned Replacement", "pinned_to": "HEADLINE_1"}
        assert new_h[1] == {"text": "Replacement Two", "pinned_to": None}
        remove_plan(result["plan_id"])

    @patch("adloop.ads.write._validate_urls", return_value={})
    @patch("adloop.ads.write._fetch_existing_rsa")
    def test_pinned_old_headlines_preserved(self, mock_fetch, mock_urls, config):
        """When the existing RSA has pinned assets (returned as dicts from GAQL),
        the old_copy in the diff should preserve the pinning info."""
        pinned_existing = dict(EXISTING_RSA)
        pinned_existing["ad_group_ad.ad.responsive_search_ad.headlines"] = [
            {"text": "Pinned Old", "pinned_to": "HEADLINE_1"},
            "Unpinned Old Two",
            "Unpinned Old Three",
        ]
        mock_fetch.return_value = pinned_existing
        result = draft_rsa_replacement(
            config,
            customer_id="1234567890",
            ad_id="12345",
            headlines=VALID_HEADLINES,
            descriptions=VALID_DESCRIPTIONS,
        )
        assert "plan_id" in result
        old_h = result["diff"]["old"]["headlines"]
        assert old_h[0] == {"text": "Pinned Old", "pinned_to": "HEADLINE_1"}
        assert old_h[1] == {"text": "Unpinned Old Two", "pinned_to": None}
        remove_plan(result["plan_id"])
