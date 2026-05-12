"""Google Ads Performance Max write tools — behind the safety layer.

Performance Max is structurally different from Search:
- No ad groups, no keywords, no individual ads.
- An ``asset_group`` bundles assets (headlines, descriptions, images, logos,
  videos) that Google assembles dynamically per impression.
- Channel mix (Search/Display/YouTube/Shopping/Maps/Discover/Gmail) is decided
  by Google at serve time. The API REJECTS any ``Campaign.network_settings``
  on PMax — both ``target_search_network`` and ``target_content_network``.
- For non-retail PMax, asset groups + linked assets must be created together
  in the SAME bulk mutate as the campaign.

These write tools follow the same draft -> preview -> confirm_and_apply flow
as the Search tools in ads/write.py. Each draft_* tool creates a ChangePlan
that ``confirm_and_apply`` (in ads/write.py) executes.

Apply helpers live here too and are wired into ``_execute_plan``'s dispatch
table via ``PMAX_OPERATIONS``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adloop.config import AdLoopConfig


# ---------------------------------------------------------------------------
# Constants — character limits, field-type minimums, allowed enum values
# ---------------------------------------------------------------------------

# Character limits per Google Ads documentation
_LIMITS = {
    "HEADLINE": 30,
    "LONG_HEADLINE": 90,
    "DESCRIPTION": 90,
    "BUSINESS_NAME": 25,
}

# Image upload constraints (Google Ads ImageAsset).
_IMAGE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_IMAGE_EXT_TO_MIME = {
    ".jpg": "IMAGE_JPEG",
    ".jpeg": "IMAGE_JPEG",
    ".png": "IMAGE_PNG",
    ".gif": "IMAGE_GIF",
}
_IMAGE_MAGIC_BYTES = {
    "IMAGE_JPEG": (b"\xff\xd8\xff",),
    "IMAGE_PNG": (b"\x89PNG\r\n\x1a\n",),
    "IMAGE_GIF": (b"GIF87a", b"GIF89a"),
}

# Per-field-type minimums for non-retail PMax asset groups (Google's minimums).
# An asset group below ANY minimum will fail Google's "minimum requirements"
# check at serve time even if the API accepts the create.
ASSET_MINIMUMS = {
    "HEADLINE": 3,
    "LONG_HEADLINE": 1,
    "DESCRIPTION": 2,
    "BUSINESS_NAME": 1,
    "MARKETING_IMAGE": 1,
    "SQUARE_MARKETING_IMAGE": 1,
    "LOGO": 1,
}

# Per-field-type maximums for the inputs this module accepts.
ASSET_MAXIMUMS = {
    "HEADLINE": 5,
    "LONG_HEADLINE": 5,
    "DESCRIPTION": 5,
    "BUSINESS_NAME": 1,
    "MARKETING_IMAGE": 20,
    "SQUARE_MARKETING_IMAGE": 20,
    "LOGO": 5,
}

# PMax is Smart Bidding only — MANUAL_CPC / TARGET_SPEND are rejected.
_PMAX_VALID_BIDDING = {
    "MAXIMIZE_CONVERSIONS",
    "MAXIMIZE_CONVERSION_VALUE",
    "TARGET_CPA",
    "TARGET_ROAS",
}


# ---------------------------------------------------------------------------
# Draft tools — return a preview with a plan_id, do NOT execute
# ---------------------------------------------------------------------------


def draft_pmax_campaign(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    campaign_name: str = "",
    daily_budget: float = 0,
    bidding_strategy: str = "",
    target_cpa: float = 0,
    target_roas: float = 0,
    geo_target_ids: list[str] | None = None,
    language_ids: list[str] | None = None,
    final_url_suffix: str | None = None,
    asset_group: dict | None = None,
) -> dict:
    """Draft a Performance Max campaign with its first asset group.

    Per Google's PMax structure rules, the campaign and its first asset group
    + assets MUST be created in the same API call. This tool produces a
    single ChangePlan that, on confirm_and_apply, issues one bulk mutate
    containing: CampaignBudget + Campaign (PAUSED) + geo/language targeting +
    AssetGroup (PAUSED) + every Asset + every AssetGroupAsset link + every
    AssetGroupSignal.

    bidding_strategy: PMax accepts only Smart Bidding strategies —
        ``MAXIMIZE_CONVERSIONS``, ``MAXIMIZE_CONVERSION_VALUE``, ``TARGET_CPA``,
        ``TARGET_ROAS``. ``MANUAL_CPC`` and ``TARGET_SPEND`` are rejected.

    asset_group: dict with these fields (required unless marked optional):
        - ``name`` (str): asset group name.
        - ``final_urls`` (list[str]): at least one. These are where ads send users.
        - ``path1`` (str, optional, <=15 chars): display URL path component.
        - ``path2`` (str, optional, <=15 chars): second display URL path component.
        - ``headlines`` (list[str]): 3-5 short headlines, each <=30 chars.
        - ``long_headlines`` (list[str]): 1-5 long headlines, each <=90 chars.
        - ``descriptions`` (list[str]): 2-5 descriptions, each <=90 chars.
        - ``business_name`` (str): your business name, <=25 chars.
        - ``marketing_image_assets`` (list[str], optional): resource_names of
          existing 1.91:1 marketing image Assets. PMax requires at least one
          marketing image — if you have none uploaded yet, do that in Google
          Ads UI first and pass the resource names here.
        - ``square_marketing_image_assets`` (list[str], optional): resource_names
          of existing 1:1 square marketing image Assets.
        - ``logo_assets`` (list[str], optional): resource_names of existing logo
          Assets.
        - ``youtube_video_ids`` (list[str], optional): YouTube video IDs to add
          as YOUTUBE_VIDEO assets (these are inline-creatable).
        - ``search_themes`` (list[str], optional): search-theme signal phrases.
        - ``audience_resource_names`` (list[str], optional): resource_names of
          existing Audience resources to use as audience signals.

    NOTE: image and logo Assets are not inline-creatable inside this mutate.
    Upload them first via ``draft_image_asset`` (point at local JPG/PNG/GIF
    paths), then pass the returned resource_names here. Resource_names from
    assets you've already uploaded via the Google Ads UI work too.
    """
    from adloop.safety.guards import (
        SafetyViolation,
        check_blocked_operation,
        check_budget_cap,
    )
    from adloop.safety.preview import ChangePlan, store_plan

    try:
        check_blocked_operation("create_pmax_campaign", config.safety)
    except SafetyViolation as e:
        return {"error": str(e)}

    errors, warnings = _validate_pmax_campaign(
        campaign_name=campaign_name,
        daily_budget=daily_budget,
        bidding_strategy=bidding_strategy,
        target_cpa=target_cpa,
        target_roas=target_roas,
        geo_target_ids=geo_target_ids,
        language_ids=language_ids,
        asset_group=asset_group,
    )
    if errors:
        return {"error": "Validation failed", "details": errors}

    try:
        check_budget_cap(daily_budget, config.safety)
    except SafetyViolation as e:
        return {"error": str(e)}

    plan = ChangePlan(
        operation="create_pmax_campaign",
        entity_type="campaign",
        customer_id=customer_id,
        changes={
            "campaign_name": campaign_name,
            "daily_budget": daily_budget,
            "bidding_strategy": bidding_strategy.upper(),
            "target_cpa": target_cpa or None,
            "target_roas": target_roas or None,
            "geo_target_ids": geo_target_ids or [],
            "language_ids": language_ids or [],
            "final_url_suffix": final_url_suffix or "",
            "asset_group": asset_group,
        },
    )
    store_plan(plan)
    preview = plan.to_preview()
    if warnings:
        preview["warnings"] = warnings
    return preview


def draft_asset_group(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    campaign_id: str = "",
    asset_group: dict | None = None,
) -> dict:
    """Draft a new asset group inside an existing PMax campaign.

    Creates: AssetGroup (PAUSED) + every text Asset + every YouTube video
    Asset + every AssetGroupAsset link + every AssetGroupSignal in one
    bulk mutate.

    asset_group: same dict shape as ``draft_pmax_campaign``'s asset_group —
    see that tool's docstring for field descriptions.
    """
    from adloop.safety.guards import SafetyViolation, check_blocked_operation
    from adloop.safety.preview import ChangePlan, store_plan

    try:
        check_blocked_operation("create_asset_group", config.safety)
    except SafetyViolation as e:
        return {"error": str(e)}

    errors: list[str] = []
    if not campaign_id:
        errors.append("campaign_id is required")
    if not asset_group:
        errors.append("asset_group is required")
    else:
        errors.extend(_validate_asset_group(asset_group))

    if errors:
        return {"error": "Validation failed", "details": errors}

    plan = ChangePlan(
        operation="create_asset_group",
        entity_type="asset_group",
        customer_id=customer_id,
        changes={"campaign_id": campaign_id, "asset_group": asset_group},
    )
    store_plan(plan)
    return plan.to_preview()


def draft_asset_group_assets(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    asset_group_id: str = "",
    headlines: list[str] | None = None,
    long_headlines: list[str] | None = None,
    descriptions: list[str] | None = None,
    business_name: str = "",
    marketing_image_assets: list[str] | None = None,
    square_marketing_image_assets: list[str] | None = None,
    logo_assets: list[str] | None = None,
    youtube_video_ids: list[str] | None = None,
) -> dict:
    """Draft adding assets to an existing asset group.

    Use this to extend an asset group with more headlines, descriptions,
    images, etc. Each asset gets created (text/video assets inline; image/logo
    by resource_name reference) and linked to the asset group via
    AssetGroupAsset operations in one bulk mutate.

    Image and logo Assets are not inline-creatable. Upload local files first
    via ``draft_image_asset`` and pass the resulting resource_names here, or
    paste resource_names of assets already uploaded via the Google Ads UI.
    """
    from adloop.safety.guards import SafetyViolation, check_blocked_operation
    from adloop.safety.preview import ChangePlan, store_plan

    try:
        check_blocked_operation("create_asset_group_assets", config.safety)
    except SafetyViolation as e:
        return {"error": str(e)}

    errors: list[str] = []
    if not asset_group_id:
        errors.append("asset_group_id is required")

    new_text = {
        "HEADLINE": list(headlines or []),
        "LONG_HEADLINE": list(long_headlines or []),
        "DESCRIPTION": list(descriptions or []),
    }
    if business_name:
        new_text["BUSINESS_NAME"] = [business_name]

    for ftype, items in new_text.items():
        for i, text in enumerate(items, start=1):
            errors.extend(_validate_asset_text(ftype, text, i))

    new_resource_assets = {
        "MARKETING_IMAGE": list(marketing_image_assets or []),
        "SQUARE_MARKETING_IMAGE": list(square_marketing_image_assets or []),
        "LOGO": list(logo_assets or []),
    }
    for ftype, items in new_resource_assets.items():
        for rn in items:
            if not rn or not rn.startswith("customers/"):
                errors.append(
                    f"{ftype} entries must be Asset resource_names "
                    f"like 'customers/123/assets/456' — got '{rn}'. Upload "
                    f"local files via draft_image_asset to obtain resource_names."
                )

    new_video_ids = list(youtube_video_ids or [])

    has_any = any(new_text.values()) or any(new_resource_assets.values()) or new_video_ids
    if not has_any:
        errors.append(
            "At least one asset must be provided — pass headlines, "
            "long_headlines, descriptions, business_name, "
            "marketing_image_assets, square_marketing_image_assets, "
            "logo_assets, or youtube_video_ids."
        )

    if errors:
        return {"error": "Validation failed", "details": errors}

    plan = ChangePlan(
        operation="create_asset_group_assets",
        entity_type="asset_group",
        entity_id=asset_group_id,
        customer_id=customer_id,
        changes={
            "asset_group_id": asset_group_id,
            "text_assets_by_type": new_text,
            "resource_assets_by_type": new_resource_assets,
            "youtube_video_ids": new_video_ids,
        },
    )
    store_plan(plan)
    return plan.to_preview()


def draft_image_asset(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    images: list[dict] | None = None,
) -> dict:
    """Draft uploading one or more local image files as Google Ads Assets.

    PMax campaigns require pre-uploaded MARKETING_IMAGE, SQUARE_MARKETING_IMAGE,
    and LOGO assets that are referenced by resource_name. This tool reads local
    image files, validates extension / file size / magic bytes, and produces a
    ChangePlan that uploads the bytes via ``AssetService.MutateAssets`` when
    ``confirm_and_apply`` is called. On apply, returns the new Asset
    resource_names so they can be passed to ``draft_pmax_campaign``,
    ``draft_asset_group``, or ``draft_asset_group_assets``.

    The same uploaded Asset can be linked as MARKETING_IMAGE,
    SQUARE_MARKETING_IMAGE, or LOGO at link time — Google checks the pixel
    dimensions against the slot's aspect-ratio requirement when the
    AssetGroupAsset link is created (MARKETING_IMAGE: 1.91:1, min 600x314;
    SQUARE_MARKETING_IMAGE: 1:1, min 300x300; LOGO: 1:1, min 128x128).

    Accepted formats: JPG (.jpg/.jpeg), PNG (.png), static GIF (.gif).
    Max file size: 5 MB per image. Bytes are read once at apply time, so the
    file must still exist at its path when confirm_and_apply runs.

    images: list of dicts, each with:
        - ``file_path`` (str, REQUIRED): absolute path to a local image file
        - ``name`` (str, REQUIRED): the Asset display name in Google Ads

    Example: ``images=[{"file_path": "/abs/logo.png", "name": "Acme Logo"}]``
    """
    from adloop.safety.guards import SafetyViolation, check_blocked_operation
    from adloop.safety.preview import ChangePlan, store_plan

    try:
        check_blocked_operation("upload_image_asset", config.safety)
    except SafetyViolation as e:
        return {"error": str(e)}

    errors: list[str] = []
    if not images:
        errors.append(
            "images is required — pass a list of "
            "{file_path, name} dicts."
        )
        return {"error": "Validation failed", "details": errors}

    validated: list[dict] = []
    for i, spec in enumerate(images, start=1):
        if not isinstance(spec, dict):
            errors.append(f"images[{i}] must be a dict with file_path and name")
            continue

        file_path = str(spec.get("file_path") or "").strip()
        name = str(spec.get("name") or "").strip()

        item_errors, item_meta = _validate_image_file(file_path, name, i)
        if item_errors:
            errors.extend(item_errors)
            continue
        validated.append(item_meta)

    if errors:
        return {"error": "Validation failed", "details": errors}

    plan = ChangePlan(
        operation="upload_image_asset",
        entity_type="asset",
        customer_id=customer_id,
        changes={"images": validated},
    )
    store_plan(plan)
    return plan.to_preview()


def draft_asset_group_signal(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    asset_group_id: str = "",
    search_theme: str = "",
    audience_resource_name: str = "",
) -> dict:
    """Draft a single new signal (search theme OR audience) on an asset group.

    Search themes are immutable once created — to "edit" one, remove the old
    signal and add a new one. Audiences must already exist as Audience
    resources; pass the resource_name (``customers/.../audiences/...``).
    """
    from adloop.safety.guards import SafetyViolation, check_blocked_operation
    from adloop.safety.preview import ChangePlan, store_plan

    try:
        check_blocked_operation("create_asset_group_signal", config.safety)
    except SafetyViolation as e:
        return {"error": str(e)}

    errors: list[str] = []
    if not asset_group_id:
        errors.append("asset_group_id is required")
    if not search_theme and not audience_resource_name:
        errors.append("Either search_theme or audience_resource_name is required")
    if search_theme and audience_resource_name:
        errors.append(
            "Pass only one of search_theme or audience_resource_name "
            "per call — Google creates one signal per AssetGroupSignal."
        )
    if audience_resource_name and not audience_resource_name.startswith("customers/"):
        errors.append(
            f"audience_resource_name must look like "
            f"'customers/.../audiences/...', got '{audience_resource_name}'"
        )

    if errors:
        return {"error": "Validation failed", "details": errors}

    plan = ChangePlan(
        operation="create_asset_group_signal",
        entity_type="asset_group",
        entity_id=asset_group_id,
        customer_id=customer_id,
        changes={
            "asset_group_id": asset_group_id,
            "search_theme": search_theme,
            "audience_resource_name": audience_resource_name,
        },
    )
    store_plan(plan)
    return plan.to_preview()


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_pmax_campaign(
    *,
    campaign_name: str,
    daily_budget: float,
    bidding_strategy: str,
    target_cpa: float,
    target_roas: float,
    geo_target_ids: list[str] | None,
    language_ids: list[str] | None,
    asset_group: dict | None,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not campaign_name or not campaign_name.strip():
        errors.append("campaign_name is required")
    if daily_budget <= 0:
        errors.append("daily_budget must be greater than 0")
    if not geo_target_ids:
        errors.append(
            "geo_target_ids is required — PMax campaigns must target at "
            "least one country/region"
        )
    if not language_ids:
        errors.append(
            "language_ids is required — PMax campaigns must target at "
            "least one language"
        )

    bs = bidding_strategy.upper()
    if bs not in _PMAX_VALID_BIDDING:
        errors.append(
            f"bidding_strategy must be one of {sorted(_PMAX_VALID_BIDDING)} "
            f"for PMax (no MANUAL_CPC or TARGET_SPEND), got '{bidding_strategy}'"
        )
    if bs == "TARGET_CPA" and not target_cpa:
        errors.append("target_cpa is required when bidding_strategy is TARGET_CPA")
    if bs == "TARGET_ROAS" and not target_roas:
        errors.append("target_roas is required when bidding_strategy is TARGET_ROAS")

    if target_cpa > 0 and daily_budget < 5 * target_cpa:
        warnings.append(
            f"Daily budget {daily_budget:.2f} is less than 5x target CPA "
            f"{target_cpa:.2f}. Google recommends at least 5x for PMax "
            f"learning to converge."
        )

    if not asset_group:
        errors.append("asset_group is required for PMax campaign creation")
    else:
        errors.extend(_validate_asset_group(asset_group))

    return errors, warnings


def _validate_asset_group(asset_group: dict) -> list[str]:
    """Validate the asset_group dict against PMax minimums and char limits."""
    errors: list[str] = []

    name = (asset_group.get("name") or "").strip()
    if not name:
        errors.append("asset_group.name is required")

    final_urls = asset_group.get("final_urls") or []
    if not final_urls:
        errors.append("asset_group.final_urls must contain at least one URL")
    for url in final_urls:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            errors.append(
                f"asset_group.final_urls must be http(s) URLs — got '{url}'"
            )

    path1 = asset_group.get("path1") or ""
    if path1 and len(path1) > 15:
        errors.append(f"asset_group.path1 exceeds 15 chars ({len(path1)}): '{path1}'")
    path2 = asset_group.get("path2") or ""
    if path2 and len(path2) > 15:
        errors.append(f"asset_group.path2 exceeds 15 chars ({len(path2)}): '{path2}'")

    business_name = asset_group.get("business_name") or ""
    # Always include BUSINESS_NAME — even when omitted — so the minimum check
    # fires. Otherwise an empty business_name skips validation and the
    # asset group fails apply-time API validation instead of draft-time.
    text_groups = {
        "HEADLINE": asset_group.get("headlines") or [],
        "LONG_HEADLINE": asset_group.get("long_headlines") or [],
        "DESCRIPTION": asset_group.get("descriptions") or [],
        "BUSINESS_NAME": [business_name] if business_name else [],
    }

    for ftype, items in text_groups.items():
        for i, text in enumerate(items, start=1):
            errors.extend(_validate_asset_text(ftype, text, i))

        minimum = ASSET_MINIMUMS.get(ftype, 0)
        if len(items) < minimum:
            errors.append(
                f"asset_group needs at least {minimum} {ftype} asset(s), "
                f"got {len(items)}"
            )
        maximum = ASSET_MAXIMUMS.get(ftype, 999)
        if len(items) > maximum:
            errors.append(
                f"asset_group accepts at most {maximum} {ftype} asset(s), "
                f"got {len(items)}"
            )

    image_keys = {
        "MARKETING_IMAGE": asset_group.get("marketing_image_assets") or [],
        "SQUARE_MARKETING_IMAGE": asset_group.get("square_marketing_image_assets") or [],
        "LOGO": asset_group.get("logo_assets") or [],
    }
    for ftype, items in image_keys.items():
        for rn in items:
            if not isinstance(rn, str) or not rn.startswith("customers/"):
                errors.append(
                    f"{ftype.lower()}_assets entries must be Asset resource_names "
                    f"like 'customers/123/assets/456' — got '{rn}'. Upload local "
                    f"files via draft_image_asset to obtain resource_names."
                )
        minimum = ASSET_MINIMUMS.get(ftype, 0)
        if len(items) < minimum:
            errors.append(
                f"asset_group requires at least {minimum} pre-uploaded "
                f"{ftype} asset resource_name(s) — call draft_image_asset to "
                f"upload local files, or paste resource_names of assets already "
                f"in the account. Got {len(items)}."
            )
        maximum = ASSET_MAXIMUMS.get(ftype, 999)
        if len(items) > maximum:
            errors.append(
                f"asset_group accepts at most {maximum} {ftype} "
                f"asset resource_name(s), got {len(items)}."
            )

    return errors


def _validate_asset_text(field_type: str, text: str, index: int) -> list[str]:
    """Validate a single text asset's char limit and non-emptiness."""
    errors: list[str] = []
    if not text or not str(text).strip():
        errors.append(f"{field_type} #{index} is empty")
        return errors
    limit = _LIMITS.get(field_type)
    if limit is not None and len(text) > limit:
        errors.append(
            f"{field_type} #{index} exceeds {limit} chars ({len(text)}): '{text}'"
        )
    return errors


def _validate_image_file(
    file_path: str, name: str, index: int
) -> tuple[list[str], dict]:
    """Validate a local image file path for upload.

    Returns (errors, metadata). Metadata is empty when errors are present.
    Validates path exists, extension is JPG/PNG/GIF, file size is within
    Google Ads's 5 MB limit, and the magic bytes match the declared format.
    """
    errors: list[str] = []
    if not file_path:
        errors.append(f"images[{index}].file_path is required")
    if not name:
        errors.append(f"images[{index}].name is required")
    if errors:
        return errors, {}

    if not os.path.isabs(file_path):
        errors.append(
            f"images[{index}].file_path must be an absolute path, got '{file_path}'"
        )
        return errors, {}

    if not os.path.isfile(file_path):
        errors.append(f"images[{index}].file_path does not exist: '{file_path}'")
        return errors, {}

    ext = os.path.splitext(file_path)[1].lower()
    mime_type = _IMAGE_EXT_TO_MIME.get(ext)
    if mime_type is None:
        errors.append(
            f"images[{index}].file_path has unsupported extension '{ext}' — "
            f"Google Ads accepts {sorted(_IMAGE_EXT_TO_MIME)}"
        )
        return errors, {}

    file_size = os.path.getsize(file_path)
    if file_size == 0:
        errors.append(f"images[{index}].file_path is empty: '{file_path}'")
        return errors, {}
    if file_size > _IMAGE_MAX_BYTES:
        errors.append(
            f"images[{index}].file_path is {file_size / 1024 / 1024:.2f} MB — "
            f"Google Ads max is 5 MB"
        )
        return errors, {}

    with open(file_path, "rb") as f:
        head = f.read(16)
    if not any(head.startswith(sig) for sig in _IMAGE_MAGIC_BYTES[mime_type]):
        errors.append(
            f"images[{index}].file_path extension '{ext}' does not match the "
            f"actual file content — magic bytes mismatch"
        )
        return errors, {}

    return [], {
        "file_path": file_path,
        "name": name,
        "mime_type": mime_type,
        "file_size": file_size,
    }


# ---------------------------------------------------------------------------
# Apply helpers — wired into _execute_plan via PMAX_OPERATIONS
# ---------------------------------------------------------------------------


def _apply_create_pmax_campaign(
    client: object,
    cid: str,
    changes: dict,
    *,
    validate_only: bool = False,
) -> dict:
    """Create a full PMax campaign in one bulk mutate.

    Order of operations (Google Ads docs say AssetOperations must be
    consecutive and precede their AssetGroupAsset links):

      1. CampaignBudgetOperation.create (temp -1)
      2. CampaignOperation.create (temp -2, references budget -1)
         - PMax: NO network_settings, no advertising_channel_sub_type
      3. CampaignCriterionOperation.create x N (geo + language, references -2)
      4. AssetOperation.create x N (text + youtube_video assets, temp -10..)
      5. AssetGroupOperation.create (temp -100, references campaign -2)
      6. AssetGroupAssetOperation.create x N (links assets to asset group)
      7. AssetGroupSignalOperation.create x N (search themes + audiences)
    """
    service = client.get_service("GoogleAdsService")
    campaign_service = client.get_service("CampaignService")
    budget_service = client.get_service("CampaignBudgetService")

    operations: list = []
    asset_group_data = changes["asset_group"]

    # --- 1. CampaignBudget (temp -1) ---
    budget_op = client.get_type("MutateOperation")
    budget = budget_op.campaign_budget_operation.create
    budget.resource_name = budget_service.campaign_budget_path(cid, "-1")
    budget.name = f"Budget - {changes['campaign_name']}"
    budget.amount_micros = int(changes["daily_budget"] * 1_000_000)
    budget.delivery_method = client.enums.BudgetDeliveryMethodEnum.STANDARD
    budget.explicitly_shared = False
    operations.append(budget_op)

    # --- 2. Campaign (temp -2) — PMax: omit network_settings entirely ---
    campaign_op = client.get_type("MutateOperation")
    campaign = campaign_op.campaign_operation.create
    campaign.resource_name = campaign_service.campaign_path(cid, "-2")
    campaign.name = changes["campaign_name"]
    campaign.campaign_budget = budget_service.campaign_budget_path(cid, "-1")
    campaign.status = client.enums.CampaignStatusEnum.PAUSED
    campaign.advertising_channel_type = (
        client.enums.AdvertisingChannelTypeEnum.PERFORMANCE_MAX
    )

    bs = changes["bidding_strategy"]
    if bs == "MAXIMIZE_CONVERSIONS":
        campaign.maximize_conversions.target_cpa_micros = 0
        if changes.get("target_cpa"):
            campaign.maximize_conversions.target_cpa_micros = int(
                changes["target_cpa"] * 1_000_000
            )
    elif bs == "TARGET_CPA":
        campaign.maximize_conversions.target_cpa_micros = int(
            changes["target_cpa"] * 1_000_000
        )
    elif bs == "MAXIMIZE_CONVERSION_VALUE":
        campaign.maximize_conversion_value.target_roas = 0
        if changes.get("target_roas"):
            campaign.maximize_conversion_value.target_roas = changes["target_roas"]
    elif bs == "TARGET_ROAS":
        campaign.maximize_conversion_value.target_roas = changes["target_roas"]

    # PMax does NOT accept network_settings — omitted intentionally.

    campaign.contains_eu_political_advertising = (
        client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    )

    if changes.get("final_url_suffix"):
        campaign.final_url_suffix = changes["final_url_suffix"]

    operations.append(campaign_op)

    # --- 3. Geo/language targeting (CampaignCriterion, references campaign -2) ---
    campaign_path = campaign_service.campaign_path(cid, "-2")
    for geo_id in changes.get("geo_target_ids") or []:
        geo_op = client.get_type("MutateOperation")
        geo = geo_op.campaign_criterion_operation.create
        geo.campaign = campaign_path
        geo.location.geo_target_constant = f"geoTargetConstants/{geo_id}"
        operations.append(geo_op)

    for lang_id in changes.get("language_ids") or []:
        lang_op = client.get_type("MutateOperation")
        lang = lang_op.campaign_criterion_operation.create
        lang.campaign = campaign_path
        lang.language.language_constant = f"languageConstants/{lang_id}"
        operations.append(lang_op)

    # --- 4-7. Asset group + assets + signals ---
    operations.extend(
        _build_asset_group_operations(
            client=client,
            cid=cid,
            campaign_resource_name=campaign_path,
            asset_group_data=asset_group_data,
            asset_temp_id_start=-10,
            asset_group_temp_id="-100",
        )
    )

    response = service.mutate(
        request={
            "customer_id": cid,
            "mutate_operations": operations,
            "validate_only": validate_only,
        }
    )

    if validate_only:
        return {"status": "validated", "operation_count": len(operations)}

    results: dict = {
        "campaign_budget": None,
        "campaign": None,
        "asset_group": None,
        "asset_count": 0,
        "asset_group_assets": [],
        "asset_group_signals": [],
    }
    for resp in response.mutate_operation_responses:
        resp_type = resp.WhichOneof("response")
        if not resp_type:
            continue
        rn = getattr(getattr(resp, resp_type), "resource_name", None)
        if not rn:
            continue
        if resp_type == "campaign_budget_result":
            results["campaign_budget"] = rn
        elif resp_type == "campaign_result":
            results["campaign"] = rn
        elif resp_type == "asset_group_result":
            results["asset_group"] = rn
        elif resp_type == "asset_result":
            results["asset_count"] += 1
        elif resp_type == "asset_group_asset_result":
            results["asset_group_assets"].append(rn)
        elif resp_type == "asset_group_signal_result":
            results["asset_group_signals"].append(rn)

    return results


def _apply_create_asset_group(
    client: object,
    cid: str,
    changes: dict,
    *,
    validate_only: bool = False,
) -> dict:
    """Add an asset group (with assets + signals) to an existing PMax campaign."""
    service = client.get_service("GoogleAdsService")
    campaign_service = client.get_service("CampaignService")

    campaign_path = campaign_service.campaign_path(cid, changes["campaign_id"])
    operations = _build_asset_group_operations(
        client=client,
        cid=cid,
        campaign_resource_name=campaign_path,
        asset_group_data=changes["asset_group"],
        asset_temp_id_start=-10,
        asset_group_temp_id="-100",
    )

    response = service.mutate(
        request={
            "customer_id": cid,
            "mutate_operations": operations,
            "validate_only": validate_only,
        }
    )

    if validate_only:
        return {"status": "validated", "operation_count": len(operations)}

    results: dict = {"asset_group": None, "asset_count": 0, "links": [], "signals": []}
    for resp in response.mutate_operation_responses:
        resp_type = resp.WhichOneof("response")
        if not resp_type:
            continue
        rn = getattr(getattr(resp, resp_type), "resource_name", None)
        if not rn:
            continue
        if resp_type == "asset_group_result":
            results["asset_group"] = rn
        elif resp_type == "asset_result":
            results["asset_count"] += 1
        elif resp_type == "asset_group_asset_result":
            results["links"].append(rn)
        elif resp_type == "asset_group_signal_result":
            results["signals"].append(rn)
    return results


def _apply_create_asset_group_assets(
    client: object,
    cid: str,
    changes: dict,
    *,
    validate_only: bool = False,
) -> dict:
    """Add assets (text/video inline + image refs) to an existing asset group.

    Per Google's bulk-mutate ordering rules, all Asset.create operations are
    emitted first (consecutive), then all AssetGroupAsset.create links.
    """
    service = client.get_service("GoogleAdsService")
    asset_service = client.get_service("AssetService")
    asset_group_service = client.get_service("AssetGroupService")

    asset_group_path = asset_group_service.asset_group_path(
        cid, changes["asset_group_id"]
    )
    field_type_enum = client.enums.AssetFieldTypeEnum

    # Plan all the new Asset.create operations first; record the resulting
    # temp resource_names so AssetGroupAsset.create operations can reference
    # them after all Asset operations are emitted.
    asset_ops: list = []
    link_specs: list[tuple[str, str]] = []  # (asset_resource_name, field_type)
    next_temp_id = -1

    # Inline text assets.
    for ftype, texts in (changes.get("text_assets_by_type") or {}).items():
        for text in texts:
            op = client.get_type("MutateOperation")
            asset = op.asset_operation.create
            asset.resource_name = asset_service.asset_path(cid, str(next_temp_id))
            asset.text_asset.text = text
            asset_ops.append(op)
            link_specs.append((asset.resource_name, ftype))
            next_temp_id -= 1

    # Inline YouTube video assets.
    for video_id in changes.get("youtube_video_ids") or []:
        op = client.get_type("MutateOperation")
        asset = op.asset_operation.create
        asset.resource_name = asset_service.asset_path(cid, str(next_temp_id))
        asset.youtube_video_asset.youtube_video_id = video_id
        asset_ops.append(op)
        link_specs.append((asset.resource_name, "YOUTUBE_VIDEO"))
        next_temp_id -= 1

    # Pre-uploaded image/logo assets — link-only.
    for ftype, resource_names in (changes.get("resource_assets_by_type") or {}).items():
        for rn in resource_names:
            link_specs.append((rn, ftype))

    if not asset_ops and not link_specs:
        return {"message": "No assets to add"}

    operations: list = list(asset_ops)
    for asset_rn, ftype in link_specs:
        op = client.get_type("MutateOperation")
        link = op.asset_group_asset_operation.create
        link.asset = asset_rn
        link.asset_group = asset_group_path
        link.field_type = getattr(field_type_enum, ftype)
        operations.append(op)

    response = service.mutate(
        request={
            "customer_id": cid,
            "mutate_operations": operations,
            "validate_only": validate_only,
        }
    )

    if validate_only:
        return {"status": "validated", "operation_count": len(operations)}

    results: dict = {"assets": [], "links": []}
    for resp in response.mutate_operation_responses:
        resp_type = resp.WhichOneof("response")
        if not resp_type:
            continue
        rn = getattr(getattr(resp, resp_type), "resource_name", None)
        if not rn:
            continue
        if resp_type == "asset_result":
            results["assets"].append(rn)
        elif resp_type == "asset_group_asset_result":
            results["links"].append(rn)
    return results


def _apply_upload_image_asset(
    client: object,
    cid: str,
    changes: dict,
    *,
    validate_only: bool = False,
) -> dict:
    """Upload one or more local image files as Google Ads Assets.

    File bytes are read at apply time (not at draft time) so that large
    images do not bloat the in-memory plan. If a file was modified or moved
    between draft and apply, the apply fails with a clear error before any
    Google Ads mutate runs.
    """
    service = client.get_service("AssetService")
    mime_type_enum = client.enums.MimeTypeEnum

    operations: list = []
    image_names: list[str] = []
    for spec in changes.get("images") or []:
        path = spec["file_path"]
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"Image '{spec['name']}' is no longer at '{path}'. The file was "
                f"removed or moved between draft and confirm_and_apply. Re-draft "
                f"with the current path."
            )
        size_now = os.path.getsize(path)
        if size_now != spec["file_size"]:
            raise ValueError(
                f"Image '{spec['name']}' at '{path}' changed size between draft "
                f"({spec['file_size']} bytes) and confirm_and_apply ({size_now} "
                f"bytes). Re-draft to upload the current bytes."
            )
        with open(path, "rb") as f:
            data = f.read()

        op = client.get_type("AssetOperation")
        asset = op.create
        asset.name = spec["name"]
        asset.type_ = client.enums.AssetTypeEnum.IMAGE
        asset.image_asset.data = data
        asset.image_asset.file_size = size_now
        asset.image_asset.mime_type = getattr(mime_type_enum, spec["mime_type"])
        operations.append(op)
        image_names.append(spec["name"])

    response = service.mutate_assets(
        request={
            "customer_id": cid,
            "operations": operations,
            "validate_only": validate_only,
        }
    )

    if validate_only:
        return {"status": "validated", "image_count": len(operations)}

    uploaded = [
        {"name": image_names[i], "resource_name": r.resource_name}
        for i, r in enumerate(response.results)
    ]
    return {"uploaded": uploaded, "image_count": len(uploaded)}


def _apply_create_asset_group_signal(
    client: object,
    cid: str,
    changes: dict,
    *,
    validate_only: bool = False,
) -> dict:
    """Add a single signal (search theme or audience) to an asset group."""
    service = client.get_service("AssetGroupSignalService")
    asset_group_service = client.get_service("AssetGroupService")

    operation = client.get_type("AssetGroupSignalOperation")
    signal = operation.create
    signal.asset_group = asset_group_service.asset_group_path(
        cid, changes["asset_group_id"]
    )

    if changes.get("search_theme"):
        signal.search_theme.text = changes["search_theme"]
    elif changes.get("audience_resource_name"):
        signal.audience.audience = changes["audience_resource_name"]

    response = service.mutate_asset_group_signals(
        request={
            "customer_id": cid,
            "operations": [operation],
            "validate_only": validate_only,
        }
    )
    if validate_only:
        return {"status": "validated"}
    return {"resource_name": response.results[0].resource_name}


# ---------------------------------------------------------------------------
# Internal: build the AssetGroup + Asset + AssetGroupAsset + Signal operations
# ---------------------------------------------------------------------------


def _build_asset_group_operations(
    *,
    client: object,
    cid: str,
    campaign_resource_name: str,
    asset_group_data: dict,
    asset_temp_id_start: int,
    asset_group_temp_id: str,
) -> list:
    """Build the slice of MutateOperations that creates an asset group.

    The order follows Google's "AssetOperations consecutive, before
    AssetGroupAssets" requirement: all Asset.create ops first, then
    AssetGroup.create, then AssetGroupAsset.create links, then
    AssetGroupSignal.create.
    """
    asset_service = client.get_service("AssetService")
    asset_group_service = client.get_service("AssetGroupService")

    operations: list = []
    field_type_enum = client.enums.AssetFieldTypeEnum

    # Track temp resource names by the field_type they're linked to so we can
    # build AssetGroupAsset links after the AssetGroup itself is created.
    text_assets: list[tuple[str, str]] = []  # (resource_name, field_type)
    video_assets: list[str] = []  # resource_names
    next_temp = asset_temp_id_start

    text_groups = {
        "HEADLINE": asset_group_data.get("headlines") or [],
        "LONG_HEADLINE": asset_group_data.get("long_headlines") or [],
        "DESCRIPTION": asset_group_data.get("descriptions") or [],
    }
    if asset_group_data.get("business_name"):
        text_groups["BUSINESS_NAME"] = [asset_group_data["business_name"]]

    # --- 4a. Text Asset operations (one per text) ---
    for field_type, texts in text_groups.items():
        for text in texts:
            asset_op = client.get_type("MutateOperation")
            asset = asset_op.asset_operation.create
            asset.resource_name = asset_service.asset_path(cid, str(next_temp))
            asset.text_asset.text = text
            operations.append(asset_op)
            text_assets.append((asset.resource_name, field_type))
            next_temp -= 1

    # --- 4b. YouTube video Asset operations ---
    for video_id in asset_group_data.get("youtube_video_ids") or []:
        asset_op = client.get_type("MutateOperation")
        asset = asset_op.asset_operation.create
        asset.resource_name = asset_service.asset_path(cid, str(next_temp))
        asset.youtube_video_asset.youtube_video_id = video_id
        operations.append(asset_op)
        video_assets.append(asset.resource_name)
        next_temp -= 1

    # --- 5. AssetGroup operation ---
    ag_resource_name = asset_group_service.asset_group_path(cid, asset_group_temp_id)
    ag_op = client.get_type("MutateOperation")
    ag = ag_op.asset_group_operation.create
    ag.resource_name = ag_resource_name
    ag.name = asset_group_data["name"]
    ag.campaign = campaign_resource_name
    for url in asset_group_data["final_urls"]:
        ag.final_urls.append(url)
    if asset_group_data.get("path1"):
        ag.path1 = asset_group_data["path1"]
    if asset_group_data.get("path2"):
        ag.path2 = asset_group_data["path2"]
    ag.status = client.enums.AssetGroupStatusEnum.PAUSED
    operations.append(ag_op)

    # --- 6. AssetGroupAsset link operations ---
    for asset_rn, field_type in text_assets:
        link_op = client.get_type("MutateOperation")
        link = link_op.asset_group_asset_operation.create
        link.asset = asset_rn
        link.asset_group = ag_resource_name
        link.field_type = getattr(field_type_enum, field_type)
        operations.append(link_op)

    for video_rn in video_assets:
        link_op = client.get_type("MutateOperation")
        link = link_op.asset_group_asset_operation.create
        link.asset = video_rn
        link.asset_group = ag_resource_name
        link.field_type = field_type_enum.YOUTUBE_VIDEO
        operations.append(link_op)

    image_keys = {
        "MARKETING_IMAGE": asset_group_data.get("marketing_image_assets") or [],
        "SQUARE_MARKETING_IMAGE": asset_group_data.get("square_marketing_image_assets") or [],
        "LOGO": asset_group_data.get("logo_assets") or [],
    }
    for field_type, resource_names in image_keys.items():
        for rn in resource_names:
            link_op = client.get_type("MutateOperation")
            link = link_op.asset_group_asset_operation.create
            link.asset = rn
            link.asset_group = ag_resource_name
            link.field_type = getattr(field_type_enum, field_type)
            operations.append(link_op)

    # --- 7. AssetGroupSignal operations ---
    for theme in asset_group_data.get("search_themes") or []:
        sig_op = client.get_type("MutateOperation")
        signal = sig_op.asset_group_signal_operation.create
        signal.asset_group = ag_resource_name
        signal.search_theme.text = theme
        operations.append(sig_op)

    for audience_rn in asset_group_data.get("audience_resource_names") or []:
        sig_op = client.get_type("MutateOperation")
        signal = sig_op.asset_group_signal_operation.create
        signal.asset_group = ag_resource_name
        signal.audience.audience = audience_rn
        operations.append(sig_op)

    return operations


# ---------------------------------------------------------------------------
# Dispatch table — imported by ads/write.py's _execute_plan
# ---------------------------------------------------------------------------


PMAX_OPERATIONS = {
    "create_pmax_campaign": _apply_create_pmax_campaign,
    "create_asset_group": _apply_create_asset_group,
    "create_asset_group_assets": _apply_create_asset_group_assets,
    "create_asset_group_signal": _apply_create_asset_group_signal,
    "upload_image_asset": _apply_upload_image_asset,
}
