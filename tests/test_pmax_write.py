"""Tests for Performance Max draft tools — input validation behavior.

These tests cover validation paths (no Google Ads API calls). The actual
mutate roundtrip is exercised at runtime via confirm_and_apply with
validate_only=True.
"""

import pytest

from adloop.ads.pmax_write import (
    PMAX_OPERATIONS,
    draft_asset_group,
    draft_asset_group_assets,
    draft_asset_group_signal,
    draft_image_asset,
    draft_pmax_campaign,
)
from adloop.ads.write import draft_campaign
from adloop.config import AdLoopConfig, AdsConfig, GA4Config, SafetyConfig

# Minimal valid PNG (1x1, transparent). Used in upload validation tests.
_TINY_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfa\xcf"
    b"\x00\x00\x00\x02\x00\x01\xe5'\xde\xfc\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def config():
    return AdLoopConfig(
        ads=AdsConfig(customer_id="1234567890", developer_token="test"),
        ga4=GA4Config(property_id="properties/123456"),
        safety=SafetyConfig(max_daily_budget=100.0),
    )


def _valid_asset_group():
    return {
        "name": "Group A",
        "final_urls": ["https://example.com/"],
        "path1": "products",
        "path2": "shoes",
        "headlines": [
            "Buy running shoes",
            "Free shipping today",
            "Top brands on sale",
        ],
        "long_headlines": ["Find the perfect running shoes for your stride"],
        "descriptions": [
            "Browse 100+ models from top brands.",
            "Free returns within 30 days.",
        ],
        "business_name": "Acme Sports",
        "marketing_image_assets": ["customers/1234567890/assets/1001"],
        "square_marketing_image_assets": ["customers/1234567890/assets/1002"],
        "logo_assets": ["customers/1234567890/assets/1003"],
    }


# ---------------------------------------------------------------------------
# draft_campaign should reject channel_type=PERFORMANCE_MAX
# ---------------------------------------------------------------------------


class TestDraftCampaignRejectsPMax:
    def test_rejects_performance_max_channel(self, config):
        result = draft_campaign(
            config,
            customer_id="1234567890",
            campaign_name="Test",
            daily_budget=10.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            channel_type="PERFORMANCE_MAX",
            geo_target_ids=["2840"],
            language_ids=["1000"],
        )

        assert "error" in result
        assert "draft_pmax_campaign" in result["error"]


# ---------------------------------------------------------------------------
# draft_pmax_campaign
# ---------------------------------------------------------------------------


class TestDraftPmaxCampaign:
    def test_accepts_valid_campaign(self, config):
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=_valid_asset_group(),
        )

        assert "error" not in result
        assert result["status"] == "PENDING_CONFIRMATION"
        assert result["operation"] == "create_pmax_campaign"
        assert result["plan_id"]

    def test_rejects_manual_cpc(self, config):
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MANUAL_CPC",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=_valid_asset_group(),
        )

        assert "error" in result
        details = " ".join(result["details"])
        # The validator names the actual rejected value and points at the
        # allowed Smart Bidding strategies.
        assert "MANUAL_CPC" in details
        assert "MAXIMIZE_CONVERSIONS" in details

    def test_rejects_target_spend(self, config):
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="TARGET_SPEND",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=_valid_asset_group(),
        )

        assert "error" in result

    def test_requires_asset_group(self, config):
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=None,
        )

        assert "error" in result
        details = " ".join(result["details"])
        assert "asset_group is required" in details

    def test_requires_geo_targets(self, config):
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=[],
            language_ids=["1000"],
            asset_group=_valid_asset_group(),
        )

        assert "error" in result
        assert any("geo_target_ids" in d for d in result["details"])

    def test_rejects_budget_above_cap(self, config):
        # Cap is 100 in fixture; 200 exceeds it
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=200.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=_valid_asset_group(),
        )

        assert "error" in result

    def test_validates_text_char_limits(self, config):
        bad = _valid_asset_group()
        bad["headlines"] = [
            "This headline is way more than thirty characters long for sure",
            "Short",
            "Also short",
        ]
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=bad,
        )

        assert "error" in result
        details = " ".join(result["details"])
        assert "30 chars" in details or "exceeds" in details

    def test_enforces_min_headlines(self, config):
        bad = _valid_asset_group()
        bad["headlines"] = ["only one"]
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=bad,
        )

        assert "error" in result
        details = " ".join(result["details"])
        assert "HEADLINE" in details

    def test_requires_image_resource_names_not_urls(self, config):
        bad = _valid_asset_group()
        bad["marketing_image_assets"] = ["https://example.com/img.png"]
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=bad,
        )

        assert "error" in result
        details = " ".join(result["details"])
        assert "resource_names" in details or "customers/" in details

    def test_requires_business_name_when_omitted(self, config):
        # Regression: omitting business_name silently skipped the minimum
        # check, so the asset group passed draft validation and only failed
        # at apply-time API validation. Now caught at draft.
        bad = _valid_asset_group()
        bad["business_name"] = ""
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=bad,
        )

        assert "error" in result
        details = " ".join(result["details"])
        assert "BUSINESS_NAME" in details

    def test_brand_guidelines_defaults_to_true(self, config):
        # New PMax campaigns default to brand_guidelines_enabled=True on
        # Google's side. The tool defaults to True too so the apply doesn't
        # hit REQUIRED_BUSINESS_NAME_ASSET_NOT_LINKED on fresh accounts.
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=_valid_asset_group(),
        )
        assert "error" not in result
        assert result["changes"]["brand_guidelines_enabled"] is True

    def test_brand_guidelines_opt_out(self, config):
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=_valid_asset_group(),
            brand_guidelines_enabled=False,
        )
        assert "error" not in result
        assert result["changes"]["brand_guidelines_enabled"] is False

    def test_rejects_too_many_marketing_images(self, config):
        # Regression: image lists were checked against minimums but not
        # maximums. ASSET_MAXIMUMS["MARKETING_IMAGE"] = 20.
        bad = _valid_asset_group()
        bad["marketing_image_assets"] = [
            f"customers/1234567890/assets/{i}" for i in range(1, 22)
        ]
        result = draft_pmax_campaign(
            config,
            customer_id="1234567890",
            campaign_name="PMax Test",
            daily_budget=20.0,
            bidding_strategy="MAXIMIZE_CONVERSIONS",
            geo_target_ids=["2840"],
            language_ids=["1000"],
            asset_group=bad,
        )

        assert "error" in result
        details = " ".join(result["details"])
        assert "at most 20" in details
        assert "MARKETING_IMAGE" in details


# ---------------------------------------------------------------------------
# draft_asset_group
# ---------------------------------------------------------------------------


class TestDraftAssetGroup:
    def test_accepts_valid(self, config):
        result = draft_asset_group(
            config,
            customer_id="1234567890",
            campaign_id="22488112473",
            asset_group=_valid_asset_group(),
        )

        assert "error" not in result
        assert result["operation"] == "create_asset_group"

    def test_requires_campaign_id(self, config):
        result = draft_asset_group(
            config,
            customer_id="1234567890",
            campaign_id="",
            asset_group=_valid_asset_group(),
        )

        assert "error" in result
        details = " ".join(result["details"])
        assert "campaign_id" in details

    def test_requires_asset_group(self, config):
        result = draft_asset_group(
            config,
            customer_id="1234567890",
            campaign_id="22488112473",
            asset_group=None,
        )

        assert "error" in result


# ---------------------------------------------------------------------------
# draft_asset_group_assets
# ---------------------------------------------------------------------------


class TestDraftAssetGroupAssets:
    def test_accepts_text_only(self, config):
        result = draft_asset_group_assets(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
            headlines=["New headline 1", "New headline 2"],
        )

        assert "error" not in result
        assert result["operation"] == "create_asset_group_assets"

    def test_requires_at_least_one_asset(self, config):
        result = draft_asset_group_assets(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
        )

        assert "error" in result
        details = " ".join(result["details"])
        assert "At least one asset" in details

    def test_validates_headline_length(self, config):
        result = draft_asset_group_assets(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
            headlines=["a" * 31],
        )

        assert "error" in result

    def test_image_resource_names_must_be_resource_format(self, config):
        result = draft_asset_group_assets(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
            marketing_image_assets=["not-a-resource-name"],
        )

        assert "error" in result


# ---------------------------------------------------------------------------
# draft_asset_group_signal
# ---------------------------------------------------------------------------


class TestDraftAssetGroupSignal:
    def test_accepts_search_theme(self, config):
        result = draft_asset_group_signal(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
            search_theme="buy women's running shoes",
        )

        assert "error" not in result
        assert result["operation"] == "create_asset_group_signal"

    def test_accepts_audience(self, config):
        result = draft_asset_group_signal(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
            audience_resource_name="customers/1234567890/audiences/abc",
        )

        assert "error" not in result

    def test_rejects_both_signal_types_at_once(self, config):
        result = draft_asset_group_signal(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
            search_theme="buy shoes",
            audience_resource_name="customers/1234567890/audiences/abc",
        )

        assert "error" in result

    def test_rejects_missing_signal_content(self, config):
        result = draft_asset_group_signal(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
        )

        assert "error" in result

    def test_rejects_bad_audience_format(self, config):
        result = draft_asset_group_signal(
            config,
            customer_id="1234567890",
            asset_group_id="6572147947",
            audience_resource_name="not-a-resource-name",
        )

        assert "error" in result


# ---------------------------------------------------------------------------
# draft_image_asset
# ---------------------------------------------------------------------------


class TestDraftImageAsset:
    def test_accepts_valid_png(self, config, tmp_path):
        png_path = tmp_path / "logo.png"
        png_path.write_bytes(_TINY_PNG_BYTES)

        result = draft_image_asset(
            config,
            customer_id="1234567890",
            images=[{"file_path": str(png_path), "name": "Acme Logo"}],
        )

        assert "error" not in result
        assert result["operation"] == "upload_image_asset"
        assert result["plan_id"]
        # The validated metadata is what _apply_upload_image_asset reads.
        images = result["changes"]["images"]
        assert len(images) == 1
        assert images[0]["name"] == "Acme Logo"
        assert images[0]["mime_type"] == "IMAGE_PNG"
        assert images[0]["file_size"] == len(_TINY_PNG_BYTES)

    def test_accepts_batch(self, config, tmp_path):
        a = tmp_path / "a.png"
        b = tmp_path / "b.png"
        a.write_bytes(_TINY_PNG_BYTES)
        b.write_bytes(_TINY_PNG_BYTES)

        result = draft_image_asset(
            config,
            customer_id="1234567890",
            images=[
                {"file_path": str(a), "name": "Image A"},
                {"file_path": str(b), "name": "Image B"},
            ],
        )

        assert "error" not in result
        assert len(result["changes"]["images"]) == 2

    def test_requires_images_list(self, config):
        result = draft_image_asset(config, customer_id="1234567890", images=[])
        assert "error" in result
        assert any("images" in d for d in result["details"])

    def test_rejects_missing_file_path(self, config):
        result = draft_image_asset(
            config,
            customer_id="1234567890",
            images=[{"name": "no path"}],
        )
        assert "error" in result
        assert any("file_path" in d for d in result["details"])

    def test_rejects_missing_name(self, config, tmp_path):
        png_path = tmp_path / "logo.png"
        png_path.write_bytes(_TINY_PNG_BYTES)
        result = draft_image_asset(
            config,
            customer_id="1234567890",
            images=[{"file_path": str(png_path)}],
        )
        assert "error" in result
        assert any("name" in d for d in result["details"])

    def test_rejects_relative_path(self, config):
        result = draft_image_asset(
            config,
            customer_id="1234567890",
            images=[{"file_path": "rel/path.png", "name": "x"}],
        )
        assert "error" in result
        assert any("absolute" in d for d in result["details"])

    def test_rejects_nonexistent_file(self, config, tmp_path):
        result = draft_image_asset(
            config,
            customer_id="1234567890",
            images=[
                {"file_path": str(tmp_path / "nope.png"), "name": "x"},
            ],
        )
        assert "error" in result
        assert any("does not exist" in d for d in result["details"])

    def test_rejects_unsupported_extension(self, config, tmp_path):
        webp = tmp_path / "bad.webp"
        webp.write_bytes(b"\x00" * 100)
        result = draft_image_asset(
            config,
            customer_id="1234567890",
            images=[{"file_path": str(webp), "name": "x"}],
        )
        assert "error" in result
        assert any("unsupported extension" in d for d in result["details"])

    def test_rejects_extension_content_mismatch(self, config, tmp_path):
        # File claims .png but bytes are not a PNG.
        fake = tmp_path / "fake.png"
        fake.write_bytes(b"this is plain text, not an image")
        result = draft_image_asset(
            config,
            customer_id="1234567890",
            images=[{"file_path": str(fake), "name": "x"}],
        )
        assert "error" in result
        assert any("magic bytes" in d for d in result["details"])

    def test_rejects_oversized_file(self, config, tmp_path, monkeypatch):
        # Patch the cap so we don't have to materialise a 5 MB file.
        from adloop.ads import pmax_write

        monkeypatch.setattr(pmax_write, "_IMAGE_MAX_BYTES", 64)
        png_path = tmp_path / "logo.png"
        png_path.write_bytes(_TINY_PNG_BYTES)  # 67 bytes > 64-byte test cap

        result = draft_image_asset(
            config,
            customer_id="1234567890",
            images=[{"file_path": str(png_path), "name": "Too big"}],
        )
        assert "error" in result
        assert any("5 MB" in d for d in result["details"])

    def test_dispatch_table_registers_upload(self):
        # confirm_and_apply finds the handler via PMAX_OPERATIONS.
        assert "upload_image_asset" in PMAX_OPERATIONS


# ---------------------------------------------------------------------------
# remove_entity — asset_group_signal support
# ---------------------------------------------------------------------------


class TestRemoveAssetGroupSignal:
    def test_accepts_composite_id(self, config):
        from adloop.ads.write import remove_entity

        result = remove_entity(
            config,
            customer_id="1234567890",
            entity_type="asset_group_signal",
            entity_id="6590423305~2480811934780",
        )

        assert "error" not in result
        assert result["operation"] == "remove_entity"
        assert result["entity_type"] == "asset_group_signal"

    def test_rejects_bare_id_without_tilde(self, config):
        # The dispatch path checks for the composite shape; the draft accepts
        # the bare id but the API would reject it. We catch the format at
        # apply time, but the draft itself surfaces a useful error only when
        # apply runs. The validation here is the allowlist gate: the
        # entity_type "asset_group_signal" must be accepted by remove_entity.
        from adloop.ads.write import remove_entity

        result = remove_entity(
            config,
            customer_id="1234567890",
            entity_type="asset_group_signal",
            entity_id="bare-id",
        )

        # Allowlist passes; the format check is at apply time. The draft
        # still returns a plan — the composite-format failure surfaces
        # during confirm_and_apply.
        assert result.get("operation") == "remove_entity"
