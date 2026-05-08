"""Google Ads label tools — list, create, apply, unapply.

Labels are tags you attach to campaigns, ad groups, ads, and keywords for
filtering, reporting, and bulk operations. The API splits them across:

- ``Label`` — the label definition itself (LabelService).
- ``CampaignLabel`` / ``AdGroupLabel`` / ``AdGroupAdLabel`` /
  ``AdGroupCriterionLabel`` — assignments of a label to an entity.

The draft tools here follow the same draft -> preview -> confirm_and_apply
flow as the rest of the write tools.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adloop.config import AdLoopConfig


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def list_labels(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
) -> dict:
    """List all labels in the account."""
    from adloop.ads.gaql import execute_query

    query = """
        SELECT label.id, label.name, label.status,
               label.text_label.description,
               label.text_label.background_color
        FROM label
        WHERE label.status != 'REMOVED'
        ORDER BY label.name
    """

    rows = execute_query(config, customer_id, query)
    return {"labels": rows, "total_labels": len(rows)}


# ---------------------------------------------------------------------------
# Draft tools
# ---------------------------------------------------------------------------


def draft_label(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    name: str = "",
    description: str = "",
    background_color: str = "",
) -> dict:
    """Draft creating a new Label — returns preview, does NOT execute.

    background_color: hex color string like "#FF5733" (optional).
    """
    from adloop.safety.guards import SafetyViolation, check_blocked_operation
    from adloop.safety.preview import ChangePlan, store_plan

    try:
        check_blocked_operation("create_label", config.safety)
    except SafetyViolation as e:
        return {"error": str(e)}

    errors: list[str] = []
    if not name or not name.strip():
        errors.append("name is required")
    if background_color and not _is_hex_color(background_color):
        errors.append(
            f"background_color must be a hex string like '#FF5733', "
            f"got '{background_color}'"
        )
    if errors:
        return {"error": "Validation failed", "details": errors}

    plan = ChangePlan(
        operation="create_label",
        entity_type="label",
        customer_id=customer_id,
        changes={
            "name": name,
            "description": description,
            "background_color": background_color,
        },
    )
    store_plan(plan)
    return plan.to_preview()


def draft_apply_label(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    entity_type: str = "",
    entity_id: str = "",
    label_id: str = "",
) -> dict:
    """Draft attaching a label to a campaign, ad group, ad, or keyword.

    entity_type: "campaign", "ad_group", "ad", or "keyword".
    entity_id: bare ID for campaign/ad_group; "adGroupId~adId" for ad;
        "adGroupId~criterionId" for keyword.
    """
    from adloop.safety.guards import SafetyViolation, check_blocked_operation
    from adloop.safety.preview import ChangePlan, store_plan

    try:
        check_blocked_operation("apply_label", config.safety)
    except SafetyViolation as e:
        return {"error": str(e)}

    errors = _validate_label_assignment_inputs(entity_type, entity_id, label_id)
    if errors:
        return {"error": "Validation failed", "details": errors}

    plan = ChangePlan(
        operation="apply_label",
        entity_type=entity_type,
        entity_id=entity_id,
        customer_id=customer_id,
        changes={
            "entity_type": entity_type,
            "entity_id": entity_id,
            "label_id": label_id,
        },
    )
    store_plan(plan)
    return plan.to_preview()


def draft_unapply_label(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    entity_type: str = "",
    entity_id: str = "",
    label_id: str = "",
) -> dict:
    """Draft removing a label assignment (does NOT delete the Label itself)."""
    from adloop.safety.guards import SafetyViolation, check_blocked_operation
    from adloop.safety.preview import ChangePlan, store_plan

    try:
        check_blocked_operation("unapply_label", config.safety)
    except SafetyViolation as e:
        return {"error": str(e)}

    errors = _validate_label_assignment_inputs(entity_type, entity_id, label_id)
    if errors:
        return {"error": "Validation failed", "details": errors}

    plan = ChangePlan(
        operation="unapply_label",
        entity_type=entity_type,
        entity_id=entity_id,
        customer_id=customer_id,
        changes={
            "entity_type": entity_type,
            "entity_id": entity_id,
            "label_id": label_id,
        },
    )
    store_plan(plan)
    return plan.to_preview()


# ---------------------------------------------------------------------------
# Apply helpers — wired into _execute_plan via LABEL_OPERATIONS
# ---------------------------------------------------------------------------


def _apply_create_label(
    client: object,
    cid: str,
    changes: dict,
    *,
    validate_only: bool = False,
) -> dict:
    service = client.get_service("LabelService")
    operation = client.get_type("LabelOperation")
    label = operation.create
    label.name = changes["name"]
    if changes.get("description"):
        label.text_label.description = changes["description"]
    if changes.get("background_color"):
        label.text_label.background_color = changes["background_color"]

    response = service.mutate_labels(
        customer_id=cid, operations=[operation], validate_only=validate_only
    )
    if validate_only:
        return {"status": "validated"}
    return {"resource_name": response.results[0].resource_name}


def _apply_apply_label(
    client: object,
    cid: str,
    changes: dict,
    *,
    validate_only: bool = False,
) -> dict:
    """Attach an existing label to a campaign/ad_group/ad/keyword."""
    entity_type = changes["entity_type"]
    entity_id = changes["entity_id"]
    label_id = changes["label_id"]
    label_resource = f"customers/{cid}/labels/{label_id}"

    if entity_type == "campaign":
        service = client.get_service("CampaignLabelService")
        operation = client.get_type("CampaignLabelOperation")
        link = operation.create
        link.campaign = client.get_service("CampaignService").campaign_path(
            cid, entity_id
        )
        link.label = label_resource
        response = service.mutate_campaign_labels(
            customer_id=cid, operations=[operation], validate_only=validate_only
        )

    elif entity_type == "ad_group":
        service = client.get_service("AdGroupLabelService")
        operation = client.get_type("AdGroupLabelOperation")
        link = operation.create
        link.ad_group = client.get_service("AdGroupService").ad_group_path(
            cid, entity_id
        )
        link.label = label_resource
        response = service.mutate_ad_group_labels(
            customer_id=cid, operations=[operation], validate_only=validate_only
        )

    elif entity_type == "ad":
        from adloop.ads.write import _resolve_ad_entity_id

        resolved_id = _resolve_ad_entity_id(client, cid, entity_id)
        service = client.get_service("AdGroupAdLabelService")
        operation = client.get_type("AdGroupAdLabelOperation")
        link = operation.create
        link.ad_group_ad = f"customers/{cid}/adGroupAds/{resolved_id}"
        link.label = label_resource
        response = service.mutate_ad_group_ad_labels(
            customer_id=cid, operations=[operation], validate_only=validate_only
        )

    elif entity_type == "keyword":
        service = client.get_service("AdGroupCriterionLabelService")
        operation = client.get_type("AdGroupCriterionLabelOperation")
        link = operation.create
        link.ad_group_criterion = f"customers/{cid}/adGroupCriteria/{entity_id}"
        link.label = label_resource
        response = service.mutate_ad_group_criterion_labels(
            customer_id=cid, operations=[operation], validate_only=validate_only
        )

    else:
        raise ValueError(
            f"apply_label does not support entity_type '{entity_type}'. "
            f"Supported: campaign, ad_group, ad, keyword."
        )

    if validate_only:
        return {"status": "validated"}
    return {"resource_name": response.results[0].resource_name}


def _apply_unapply_label(
    client: object,
    cid: str,
    changes: dict,
    *,
    validate_only: bool = False,
) -> dict:
    """Detach a label from an entity by removing the *Label resource."""
    entity_type = changes["entity_type"]
    entity_id = changes["entity_id"]
    label_id = changes["label_id"]

    if entity_type == "campaign":
        service = client.get_service("CampaignLabelService")
        operation = client.get_type("CampaignLabelOperation")
        operation.remove = f"customers/{cid}/campaignLabels/{entity_id}~{label_id}"
        response = service.mutate_campaign_labels(
            customer_id=cid, operations=[operation], validate_only=validate_only
        )

    elif entity_type == "ad_group":
        service = client.get_service("AdGroupLabelService")
        operation = client.get_type("AdGroupLabelOperation")
        operation.remove = f"customers/{cid}/adGroupLabels/{entity_id}~{label_id}"
        response = service.mutate_ad_group_labels(
            customer_id=cid, operations=[operation], validate_only=validate_only
        )

    elif entity_type == "ad":
        from adloop.ads.write import _resolve_ad_entity_id

        resolved_id = _resolve_ad_entity_id(client, cid, entity_id)
        service = client.get_service("AdGroupAdLabelService")
        operation = client.get_type("AdGroupAdLabelOperation")
        operation.remove = (
            f"customers/{cid}/adGroupAdLabels/{resolved_id}~{label_id}"
        )
        response = service.mutate_ad_group_ad_labels(
            customer_id=cid, operations=[operation], validate_only=validate_only
        )

    elif entity_type == "keyword":
        service = client.get_service("AdGroupCriterionLabelService")
        operation = client.get_type("AdGroupCriterionLabelOperation")
        operation.remove = (
            f"customers/{cid}/adGroupCriterionLabels/{entity_id}~{label_id}"
        )
        response = service.mutate_ad_group_criterion_labels(
            customer_id=cid, operations=[operation], validate_only=validate_only
        )

    else:
        raise ValueError(
            f"unapply_label does not support entity_type '{entity_type}'. "
            f"Supported: campaign, ad_group, ad, keyword."
        )

    if validate_only:
        return {"status": "validated"}
    return {"resource_name": response.results[0].resource_name}


def _apply_remove_label(
    client: object,
    cid: str,
    entity_id: str,
    *,
    validate_only: bool = False,
) -> dict:
    """Remove a Label resource itself (NOT just an assignment)."""
    service = client.get_service("LabelService")
    operation = client.get_type("LabelOperation")
    operation.remove = f"customers/{cid}/labels/{entity_id}"
    response = service.mutate_labels(
        customer_id=cid, operations=[operation], validate_only=validate_only
    )
    if validate_only:
        return {"status": "validated"}
    return {"resource_name": response.results[0].resource_name}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_LABEL_VALID_ENTITY_TYPES = {"campaign", "ad_group", "ad", "keyword"}


def _validate_label_assignment_inputs(
    entity_type: str, entity_id: str, label_id: str
) -> list[str]:
    errors: list[str] = []
    if entity_type not in _LABEL_VALID_ENTITY_TYPES:
        errors.append(
            f"entity_type must be one of {sorted(_LABEL_VALID_ENTITY_TYPES)}, "
            f"got '{entity_type}'"
        )
    if not entity_id:
        errors.append("entity_id is required")
    if not label_id:
        errors.append("label_id is required")
    return errors


def _is_hex_color(value: str) -> bool:
    if not value.startswith("#"):
        return False
    hex_part = value[1:]
    if len(hex_part) not in (3, 6):
        return False
    return all(c in "0123456789abcdefABCDEF" for c in hex_part)


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------


LABEL_OPERATIONS = {
    "create_label": _apply_create_label,
    "apply_label": _apply_apply_label,
    "unapply_label": _apply_unapply_label,
}
