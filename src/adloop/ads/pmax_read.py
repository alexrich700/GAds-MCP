"""Google Ads Performance Max read tools.

Performance Max is structurally different from Search:
- No ad_groups — instead, a campaign contains asset_groups
- No keywords — instead, signals (search themes + audiences) hint at intent
- No ads — instead, asset groups bundle assets that Google assembles dynamically
- Channel mix (Search/Display/YouTube/Shopping/Maps/Discover/Gmail) is decided
  by Google at serve time, surfaced via segments.asset_interaction_target

These tools focus on the things you CAN inspect: campaign performance, asset
group structure, individual asset performance ratings, search themes, and
the post-2025-06-01 channel breakdown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adloop.config import AdLoopConfig

# Channel breakdown is only reliable after this date — earlier dates return MIXED.
_CHANNEL_BREAKDOWN_AVAILABLE_FROM = "2025-06-01"


def get_pmax_campaigns(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """Get Performance Max campaigns with PMax-specific settings + metrics."""
    from adloop.ads.gaql import execute_query

    date_clause = _date_clause(date_range_start, date_range_end)

    query = f"""
        SELECT campaign.id, campaign.name, campaign.status,
               campaign.advertising_channel_type,
               campaign.bidding_strategy_type,
               campaign.url_expansion_opt_out,
               campaign.brand_guidelines_enabled,
               campaign_budget.amount_micros,
               metrics.impressions, metrics.clicks, metrics.cost_micros,
               metrics.conversions, metrics.conversions_value,
               metrics.ctr, metrics.average_cpc
        FROM campaign
        WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
          AND campaign.status != 'REMOVED'
          {date_clause}
        ORDER BY metrics.cost_micros DESC
    """

    rows = execute_query(config, customer_id, query)
    _enrich_cost_fields(rows)
    _enrich_budget_fields(rows)
    _enrich_roas(rows)

    return {"campaigns": rows, "total_campaigns": len(rows)}


def get_pmax_channel_breakdown(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    campaign_id: str = "",
) -> dict:
    """Get spend/conversions per serving surface (Search, Display, YouTube, Shopping, etc.).

    Segments the campaign-level metrics by `segments.ad_network_type`. Channel
    attribution for PMax is only reliable from 2025-06-01 onwards — earlier
    rows return MIXED because Google could not attribute a specific channel.
    The tool emits a warning in `insights[]` when the date range overlaps that
    period or when MIXED rows are present in the result.
    """
    from adloop.ads.gaql import execute_query

    date_clause = _date_clause(date_range_start, date_range_end)

    campaign_filter = ""
    if campaign_id:
        cid = _validate_numeric_id(campaign_id, "campaign_id")
        campaign_filter = f"AND campaign.id = {cid}"

    query = f"""
        SELECT campaign.id, campaign.name,
               segments.ad_network_type,
               metrics.impressions, metrics.clicks, metrics.cost_micros,
               metrics.conversions, metrics.conversions_value
        FROM campaign
        WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
          AND campaign.status != 'REMOVED'
          {campaign_filter}
          {date_clause}
        ORDER BY campaign.id, metrics.cost_micros DESC
    """

    rows = execute_query(config, customer_id, query)
    _enrich_cost_fields(rows)
    _enrich_roas(rows)

    insights = []
    if date_range_start and date_range_start < _CHANNEL_BREAKDOWN_AVAILABLE_FROM:
        insights.append(
            f"Channel breakdown is only available from {_CHANNEL_BREAKDOWN_AVAILABLE_FROM} "
            f"onwards. Earlier rows will appear as MIXED."
        )

    if any(r.get("segments.ad_network_type") == "MIXED" for r in rows):
        insights.append(
            "Some rows show MIXED ad_network_type — Google could not attribute "
            "the spend to a specific channel (often historical pre-June-2025 data)."
        )

    return {
        "channel_breakdown": rows,
        "total_rows": len(rows),
        "insights": insights,
    }


def get_asset_groups(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    campaign_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """List asset groups for a Performance Max campaign with their metrics and ad strength."""
    from adloop.ads.gaql import execute_query

    date_clause = _date_clause(date_range_start, date_range_end)

    campaign_filter = ""
    if campaign_id:
        cid = _validate_numeric_id(campaign_id, "campaign_id")
        campaign_filter = f"AND campaign.id = {cid}"

    query = f"""
        SELECT asset_group.id, asset_group.name, asset_group.status,
               asset_group.final_urls, asset_group.path1, asset_group.path2,
               asset_group.ad_strength,
               campaign.id, campaign.name,
               metrics.impressions, metrics.clicks, metrics.cost_micros,
               metrics.conversions, metrics.conversions_value
        FROM asset_group
        WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
          AND asset_group.status != 'REMOVED'
          {campaign_filter}
          {date_clause}
        ORDER BY metrics.cost_micros DESC
    """

    rows = execute_query(config, customer_id, query)
    _enrich_cost_fields(rows)
    _enrich_roas(rows)

    return {"asset_groups": rows, "total_asset_groups": len(rows)}


def get_asset_group_assets(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    asset_group_id: str = "",
    campaign_id: str = "",
) -> dict:
    """List individual assets in PMax asset groups with field type and performance label.

    Returns asset text/url, the field_type (HEADLINE, DESCRIPTION, MARKETING_IMAGE,
    LOGO, YOUTUBE_VIDEO, etc.), and performance_label (LOW, GOOD, BEST, PENDING)
    that Google assigns based on actual serving data.
    """
    from adloop.ads.gaql import execute_query

    filters = []
    if asset_group_id:
        ag = _validate_numeric_id(asset_group_id, "asset_group_id")
        filters.append(f"asset_group.id = {ag}")
    if campaign_id:
        cid = _validate_numeric_id(campaign_id, "campaign_id")
        filters.append(f"campaign.id = {cid}")
    extra_filter = ("AND " + " AND ".join(filters)) if filters else ""

    query = f"""
        SELECT asset_group.id, asset_group.name,
               asset_group_asset.field_type,
               asset_group_asset.performance_label,
               asset_group_asset.status,
               asset.id, asset.type,
               asset.text_asset.text,
               asset.image_asset.full_size.url,
               asset.youtube_video_asset.youtube_video_id,
               asset.youtube_video_asset.youtube_video_title,
               campaign.id, campaign.name
        FROM asset_group_asset
        WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
          AND asset_group_asset.status != 'REMOVED'
          {extra_filter}
        ORDER BY asset_group.id, asset_group_asset.field_type
    """

    rows = execute_query(config, customer_id, query)
    _enrich_youtube_url(rows)

    return {"assets": rows, "total_assets": len(rows)}


def get_asset_group_signals(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    asset_group_id: str = "",
    campaign_id: str = "",
) -> dict:
    """List audience and search-theme signals attached to PMax asset groups."""
    from adloop.ads.gaql import execute_query

    filters = []
    if asset_group_id:
        ag = _validate_numeric_id(asset_group_id, "asset_group_id")
        filters.append(f"asset_group.id = {ag}")
    if campaign_id:
        cid = _validate_numeric_id(campaign_id, "campaign_id")
        filters.append(f"campaign.id = {cid}")
    extra_filter = ("AND " + " AND ".join(filters)) if filters else ""

    query = f"""
        SELECT asset_group.id, asset_group.name,
               asset_group_signal.resource_name,
               asset_group_signal.audience.audience,
               asset_group_signal.search_theme.text,
               campaign.id, campaign.name
        FROM asset_group_signal
        WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
          {extra_filter}
        ORDER BY asset_group.id
    """

    rows = execute_query(config, customer_id, query)

    for row in rows:
        if row.get("asset_group_signal.search_theme.text"):
            row["signal_type"] = "SEARCH_THEME"
        elif row.get("asset_group_signal.audience.audience"):
            row["signal_type"] = "AUDIENCE"
        else:
            row["signal_type"] = "UNKNOWN"

    return {"signals": rows, "total_signals": len(rows)}


def get_asset_group_top_combinations(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    asset_group_id: str = "",
    campaign_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """Get top-performing asset combinations Google has assembled at serve time.

    Each row represents a unique combination of headline + description + image +
    (optional) video that has actually served, with its impression count.
    """
    from adloop.ads.gaql import execute_query

    date_clause = _date_clause(date_range_start, date_range_end)

    filters = []
    if asset_group_id:
        ag = _validate_numeric_id(asset_group_id, "asset_group_id")
        filters.append(f"asset_group.id = {ag}")
    if campaign_id:
        cid = _validate_numeric_id(campaign_id, "campaign_id")
        filters.append(f"campaign.id = {cid}")
    extra_filter = ("AND " + " AND ".join(filters)) if filters else ""

    query = f"""
        SELECT asset_group.id, asset_group.name,
               asset_group_top_combination_view.asset_group_top_combinations,
               metrics.impressions,
               campaign.id, campaign.name
        FROM asset_group_top_combination_view
        WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
          {extra_filter}
          {date_clause}
        ORDER BY metrics.impressions DESC
        LIMIT 50
    """

    rows = execute_query(config, customer_id, query)

    return {"combinations": rows, "total_rows": len(rows)}


def get_pmax_search_terms(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    campaign_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """Get aggregated search-category insights for PMax (post-v23.2 surface).

    Queries `campaign_search_term_insight` (the v23.2+ resource). Returns
    category labels and metrics — individual search terms are NOT exposed for
    PMax campaigns by Google's design. Returns a structured error with a hint
    when the resource isn't available on the configured API version.
    """
    from adloop.ads.gaql import execute_query

    date_clause = _date_clause(date_range_start, date_range_end)

    if not campaign_id:
        return {
            "error": "campaign_id is required for PMax search terms.",
            "hint": (
                "PMax search-term insights are queried per-campaign. "
                "Get a Performance Max campaign id from get_pmax_campaigns first."
            ),
        }
    cid = _validate_numeric_id(campaign_id, "campaign_id")

    query = f"""
        SELECT campaign_search_term_insight.id,
               campaign_search_term_insight.category_label,
               metrics.impressions, metrics.clicks, metrics.cost_micros,
               metrics.conversions, metrics.conversions_value
        FROM campaign_search_term_insight
        WHERE campaign_search_term_insight.campaign_id = {cid}
          {date_clause}
        ORDER BY metrics.impressions DESC
        LIMIT 200
    """

    try:
        rows = execute_query(config, customer_id, query)
    except Exception as exc:
        err = str(exc)
        if "UNRECOGNIZED_FIELD" in err or "INVALID_RESOURCE_NAME" in err:
            return {
                "error": "PMax search term insights are not available on this API version.",
                "hint": (
                    "campaign_search_term_insight requires Google Ads API v23.2 or "
                    "later. Bump GOOGLE_ADS_API_VERSION in ads/client.py if needed."
                ),
            }
        raise

    _enrich_cost_fields(rows)
    _enrich_roas(rows)

    return {"search_term_categories": rows, "total_rows": len(rows)}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _date_clause(start: str, end: str) -> str:
    """Build a GAQL date WHERE fragment."""
    if start and end:
        return f"AND segments.date BETWEEN '{start}' AND '{end}'"
    return "AND segments.date DURING LAST_30_DAYS"


def _validate_numeric_id(value: str, name: str) -> str:
    """Reject non-numeric IDs to prevent GAQL injection."""
    stripped = value.replace("-", "").strip()
    if not stripped.isdigit():
        raise ValueError(f"Invalid {name}: {value!r} — must be numeric")
    return stripped


def _enrich_cost_fields(rows: list[dict]) -> None:
    """Add metrics.cost (EUR) and metrics.cpa from cost_micros."""
    for row in rows:
        cost_micros = row.get("metrics.cost_micros", 0) or 0
        row["metrics.cost"] = round(cost_micros / 1_000_000, 2)

        conversions = row.get("metrics.conversions", 0) or 0
        if conversions > 0:
            row["metrics.cpa"] = round(cost_micros / 1_000_000 / conversions, 2)

        avg_cpc_micros = row.get("metrics.average_cpc", 0) or 0
        if avg_cpc_micros:
            row["metrics.average_cpc_eur"] = round(avg_cpc_micros / 1_000_000, 2)


def _enrich_budget_fields(rows: list[dict]) -> None:
    """Compute human-readable daily budget from budget_micros."""
    for row in rows:
        budget_micros = row.get("campaign_budget.amount_micros", 0) or 0
        if budget_micros:
            row["campaign_budget.amount"] = round(budget_micros / 1_000_000, 2)


def _enrich_roas(rows: list[dict]) -> None:
    """Compute ROAS = conversions_value / cost from cost_micros."""
    for row in rows:
        cost_micros = row.get("metrics.cost_micros", 0) or 0
        value = row.get("metrics.conversions_value", 0) or 0
        if cost_micros > 0 and value:
            row["metrics.roas"] = round(value / (cost_micros / 1_000_000), 2)


def _enrich_youtube_url(rows: list[dict]) -> None:
    """Build a youtube_url shortcut from the youtube_video_id when present."""
    for row in rows:
        vid = row.get("asset.youtube_video_asset.youtube_video_id")
        if vid:
            row["asset.youtube_video_asset.youtube_url"] = (
                f"https://www.youtube.com/watch?v={vid}"
            )
