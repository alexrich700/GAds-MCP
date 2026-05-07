"""AdLoop MCP server — FastMCP instance with all tool registrations."""

from __future__ import annotations

import functools
from typing import Callable

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from adloop.config import load_config

_READONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

mcp = FastMCP(
    "AdLoop",
    instructions=(
        "AdLoop connects Google Ads and Google Analytics (GA4) data to your "
        "codebase. Use the read tools to analyze performance, and the write "
        "tools (with safety confirmation) to manage campaigns."
    ),
)

_config = load_config()


def _safe(fn: Callable) -> Callable:
    """Wrap a tool function so exceptions return structured error dicts."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except RuntimeError as e:
            return {"error": str(e)}
        except Exception as e:
            err = str(e).lower()
            if "invalid_grant" in err or "revoked" in err:
                return {
                    "error": "Authentication failed — OAuth token expired or revoked.",
                    "hint": (
                        "Delete ~/.adloop/token.json and re-run any tool to "
                        "trigger re-authorization. If this keeps happening, "
                        "publish the GCP consent screen to 'In production'."
                    ),
                }
            return {"error": str(e), "tool": fn.__name__}

    return wrapper

# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READONLY)
@_safe
def health_check() -> dict:
    """Test AdLoop connectivity — checks OAuth token, GA4 API, and Google Ads API.

    Run this first if other tools are failing. Returns status for each service
    and actionable guidance if something is broken.
    """
    from adloop.ads.client import GOOGLE_ADS_API_VERSION

    status = {
        "ga4": "unknown",
        "ads": "unknown",
        "config": "ok",
        "google_ads_api_version": GOOGLE_ADS_API_VERSION,
    }

    try:
        from google.ads.googleads.client import _DEFAULT_VERSION
        if _DEFAULT_VERSION != GOOGLE_ADS_API_VERSION:
            status["ads_version_note"] = (
                f"AdLoop is pinned to {GOOGLE_ADS_API_VERSION} but the "
                f"google-ads library defaults to {_DEFAULT_VERSION}. "
                f"A newer API version is available — update "
                f"GOOGLE_ADS_API_VERSION in ads/client.py when ready to migrate."
            )
    except ImportError:
        pass

    try:
        from adloop.ga4.reports import get_account_summaries as _ga4_test

        result = _ga4_test(_config)
        status["ga4"] = "ok"
        status["ga4_properties"] = result.get("total_properties", 0)
    except Exception as e:
        status["ga4"] = "error"
        status["ga4_error"] = str(e)

    try:
        from adloop.ads.read import list_accounts as _ads_test

        result = _ads_test(_config)
        status["ads"] = "ok"
        status["ads_accounts"] = result.get("total_accounts", 0)
    except Exception as e:
        status["ads"] = "error"
        status["ads_error"] = str(e)

    if status["ga4"] == "error" or status["ads"] == "error":
        any_error = status.get("ga4_error", "") + status.get("ads_error", "")
        if "invalid_grant" in any_error.lower() or "revoked" in any_error.lower():
            status["hint"] = (
                "OAuth token expired or revoked. Delete ~/.adloop/token.json "
                "and re-run health_check to trigger re-authorization. "
                "To prevent recurring expiry, publish the GCP consent screen "
                "from 'Testing' to 'In production'."
            )

    return status


# ---------------------------------------------------------------------------
# GA4 Read Tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READONLY)
@_safe
def get_account_summaries() -> dict:
    """List all GA4 accounts and properties accessible by the authenticated user.

    Use this as the first step to discover which GA4 properties are available.
    Returns account names, property names, and property IDs.
    """
    from adloop.ga4.reports import get_account_summaries as _impl

    return _impl(_config)


@mcp.tool(annotations=_READONLY)
@_safe
def run_ga4_report(
    dimensions: list[str] = [],
    metrics: list[str] = [],
    date_range_start: str = "7daysAgo",
    date_range_end: str = "today",
    property_id: str = "",
    limit: int = 100,
    dimension_filter: dict[str, str] = {},
) -> dict:
    """Run a custom GA4 report with specified dimensions, metrics, and date range.

    Common dimensions: date, pagePath, sessionSource, sessionMedium, country, deviceCategory, eventName
    Common metrics: sessions, totalUsers, newUsers, screenPageViews, conversions, eventCount, bounceRate

    Date formats: "today", "yesterday", "7daysAgo", "28daysAgo", "90daysAgo", or "YYYY-MM-DD".
    If property_id is empty, uses the default from config.

    dimension_filter: optional dict of dimension_name -> exact match value to
    filter results server-side. Multiple entries are combined with AND logic.
    Example: {"sessionSource": "google", "sessionMedium": "cpc"} returns only
    paid search traffic.
    """
    from adloop.ga4.reports import run_ga4_report as _impl

    return _impl(
        _config,
        property_id=property_id or _config.ga4.property_id,
        dimensions=dimensions,
        metrics=metrics,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        limit=limit,
        dimension_filter=dimension_filter,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def run_realtime_report(
    dimensions: list[str] = [],
    metrics: list[str] = [],
    property_id: str = "",
) -> dict:
    """Run a GA4 realtime report showing current active users and events.

    Useful for checking if tracking is firing correctly after code changes.
    Common dimensions: unifiedScreenName, eventName, country, deviceCategory
    Common metrics: activeUsers, eventCount
    """
    from adloop.ga4.reports import run_realtime_report as _impl

    return _impl(
        _config,
        property_id=property_id or _config.ga4.property_id,
        dimensions=dimensions,
        metrics=metrics,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_tracking_events(
    date_range_start: str = "28daysAgo",
    date_range_end: str = "today",
    property_id: str = "",
) -> dict:
    """List all GA4 events and their volume for the given date range.

    Returns every distinct event name with its total event count.
    Use this to understand what tracking is configured and active.
    """
    from adloop.ga4.tracking import get_tracking_events as _impl

    return _impl(
        _config,
        property_id=property_id or _config.ga4.property_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
    )


# ---------------------------------------------------------------------------
# Google Ads Read Tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READONLY)
@_safe
def list_accounts() -> dict:
    """List all accessible Google Ads accounts.

    Returns account names, IDs, and status. Use this to discover
    which accounts are available before running performance queries.
    """
    from adloop.ads.read import list_accounts as _impl

    return _impl(_config)


@mcp.tool(annotations=_READONLY)
@_safe
def get_campaign_performance(
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """Get campaign-level performance metrics for a date range.

    Returns: campaign name, status, type, impressions, clicks, cost,
    conversions, CPA, ROAS, CTR for each campaign.
    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.ads.read import get_campaign_performance as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_ad_performance(
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """Get ad-level performance data including headlines, descriptions, and metrics.

    Returns: ad type, headlines, descriptions, final URL, impressions,
    clicks, CTR, conversions, cost for each ad.
    """
    from adloop.ads.read import get_ad_performance as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_keyword_performance(
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """Get keyword metrics including quality scores and competitive data.

    Returns: keyword text, match type, quality score, ad_group.id, ad_group.name,
    ad_group_criterion.criterion_id, impressions, clicks, CTR, CPC, cost,
    conversions for each keyword. Use ad_group.id and criterion_id to
    construct entity_id strings (e.g. "adGroupId~criterionId") for pause_entity.
    """
    from adloop.ads.read import get_keyword_performance as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_search_terms(
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    campaign_id: str = "",
) -> dict:
    """Get search terms report — what users actually typed before clicking your ads.

    Critical for finding negative keyword opportunities and understanding user intent.
    Returns: search term, campaign_id, campaign_name, ad group, impressions,
    clicks, cost, conversions. Each row includes campaign.id so you can pass
    it directly to add_negative_keywords.

    campaign_id: optional filter to a specific campaign. When omitted, returns
    search terms across all campaigns.
    """
    from adloop.ads.read import get_search_terms as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        campaign_id=campaign_id,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_negative_keywords(
    customer_id: str = "",
    campaign_id: str = "",
) -> dict:
    """List existing negative keywords for a campaign or all campaigns.

    Use this before adding negative keywords to check for duplicates.
    If campaign_id is empty, returns negatives across all campaigns.
    """
    from adloop.ads.read import get_negative_keywords as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        campaign_id=campaign_id,
    )


# ---------------------------------------------------------------------------
# Google Ads Insights Tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READONLY)
@_safe
def get_impression_share(
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    level: str = "campaign",
) -> dict:
    """Get impression share metrics — how much of available search traffic you're capturing.

    Shows search impression share, budget-lost share, rank-lost share,
    top impression share, and absolute top impression share.

    level: "campaign" (default), "ad_group", or "keyword"
    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.ads.read import get_impression_share as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        level=level,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_change_history(
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    resource_type: str = "",
    operation_type: str = "",
    limit: int = 100,
) -> dict:
    """Get recent account change history — who changed what and when.

    Critical for correlating performance shifts with account changes.
    Goes back up to 30 days (API limit). Default shows last 14 days.

    resource_type: filter by type — "CAMPAIGN", "AD_GROUP", "AD",
        "AD_GROUP_CRITERION", "CAMPAIGN_BUDGET", "BIDDING_STRATEGY"
    operation_type: filter by action — "CREATE", "UPDATE", "REMOVE"
    Date format: "YYYY-MM-DD". Empty = last 14 days.
    """
    from adloop.ads.read import get_change_history as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        resource_type=resource_type,
        operation_type=operation_type,
        limit=limit,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_device_performance(
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    level: str = "campaign",
) -> dict:
    """Get performance segmented by device — MOBILE, DESKTOP, TABLET.

    Essential for businesses where mobile intent differs
    dramatically from desktop. Shows clicks, cost, conversions, and
    conversion rate per device.

    level: "campaign" (default) or "ad_group"
    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.ads.read import get_device_performance as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        level=level,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_location_performance(
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """Get performance segmented by geographic location.

    Shows impressions, clicks, cost, and conversions per location.
    Useful for identifying underperforming service areas or wasted spend
    outside the target service radius.

    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.ads.read import get_location_performance as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_quality_score_details(
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    campaign_id: str = "",
) -> dict:
    """Get keyword Quality Score with component breakdowns.

    Returns quality_score (1-10), creative_quality_score (ad relevance),
    post_click_quality_score (landing page), and search_predicted_ctr
    (expected CTR). Sorted by spend so high-cost low-QS keywords surface first.

    campaign_id: optional filter to a specific campaign.
    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.ads.read import get_quality_score_details as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        campaign_id=campaign_id,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_bid_strategy_status(
    customer_id: str = "",
    campaign_id: str = "",
) -> dict:
    """Get bid strategy type, system status, and learning state per campaign.

    Shows bidding_strategy_type (MAXIMIZE_CONVERSIONS, TARGET_CPA, etc.),
    bidding_strategy_system_status (LEARNING, ELIGIBLE, LIMITED, etc.),
    daily budget, and last-30-day metrics.

    Use this before recommending changes — don't edit campaigns in a learning phase.
    campaign_id: optional filter to a specific campaign.
    """
    from adloop.ads.read import get_bid_strategy_status as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        campaign_id=campaign_id,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_budget_pacing(
    customer_id: str = "",
    campaign_id: str = "",
) -> dict:
    """Get monthly budget pacing — spend-to-date, projected spend, pace percentage.

    Shows daily budget, month-to-date spend, daily average spend,
    projected month-end spend, and whether each campaign is over or under pace.

    campaign_id: optional filter to a specific campaign.
    """
    from adloop.ads.read import get_budget_pacing as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        campaign_id=campaign_id,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_ad_schedule_performance(
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    campaign_id: str = "",
) -> dict:
    """Get performance by hour of day and day of week.

    Identifies peak and off-peak patterns. Important for service
    businesses (e.g. emergency plumber at 2am vs 2pm).

    Returns: campaign, day_of_week, hour, impressions, clicks, CTR, cost,
    conversions, conversion_rate, CPA for each time slot.

    campaign_id: optional filter to a specific campaign.
    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.ads.read import get_ad_schedule_performance as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        campaign_id=campaign_id,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_auction_insights(
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    campaign_id: str = "",
) -> dict:
    """Get auction insights — competitor overlap rate, outranking share, position data.

    Shows which competitors appear alongside your ads and how often you
    outrank them. Requires an allowlisted Google Ads account — returns a
    helpful error if access is not available.

    campaign_id: optional filter to a specific campaign.
    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.ads.read import get_auction_insights as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        campaign_id=campaign_id,
    )


# ---------------------------------------------------------------------------
# Cross-Reference Tools (GA4 + Ads combined)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READONLY)
@_safe
def analyze_campaign_conversions(
    date_range_start: str = "",
    date_range_end: str = "",
    customer_id: str = "",
    property_id: str = "",
    campaign_name: str = "",
) -> dict:
    """Campaign clicks → GA4 conversions mapping — the real cost-per-conversion.

    Combines Google Ads campaign metrics with GA4 session/conversion data to
    reveal click-to-session ratios (GDPR indicator), compare Ads-reported vs
    GA4-reported conversions, and compute cost-per-GA4-conversion.

    Returns one row per campaign (with campaign_id) including
    conversion_discrepancy_pct between Ads and GA4. When campaign_name is
    provided, filters to matching campaigns.

    Also returns non-paid channel conversion rates for comparison context.
    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.crossref import analyze_campaign_conversions as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        property_id=property_id or _config.ga4.property_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        campaign_name=campaign_name,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def landing_page_analysis(
    date_range_start: str = "",
    date_range_end: str = "",
    customer_id: str = "",
    property_id: str = "",
) -> dict:
    """Analyze which landing pages convert and which don't.

    Combines ad final URLs with GA4 page-level data to show paid traffic
    sessions, conversion rates, bounce rates, and engagement per landing page.
    Identifies pages that get ad clicks but zero conversions and orphaned URLs.
    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.crossref import landing_page_analysis as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        property_id=property_id or _config.ga4.property_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def attribution_check(
    date_range_start: str = "",
    date_range_end: str = "",
    customer_id: str = "",
    property_id: str = "",
    conversion_events: list[str] = [],
) -> dict:
    """Compare Ads-reported conversions vs GA4 — find tracking discrepancies.

    Checks whether conversions reported by Google Ads match what GA4 records,
    diagnoses GDPR consent gaps, attribution model differences, and missing
    conversion event configuration.

    conversion_events: optional list of GA4 event names to specifically check
    (e.g. ["sign_up", "purchase"]). If omitted, compares aggregate totals only.
    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.crossref import attribution_check as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        property_id=property_id or _config.ga4.property_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        conversion_events=conversion_events,
    )


# ---------------------------------------------------------------------------
# Performance Max Read Tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READONLY)
@_safe
def get_pmax_campaigns(
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """Get Performance Max campaigns with PMax-specific settings and metrics.

    Returns: campaign id/name/status, bidding strategy, brand guidelines flag,
    daily budget, impressions, clicks, cost, conversions, conversions_value,
    CPA, and ROAS for each PMax campaign.

    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.ads.pmax_read import get_pmax_campaigns as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_pmax_channel_breakdown(
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    campaign_id: str = "",
) -> dict:
    """Get PMax spend/clicks/conversions per serving surface (Search/Display/YouTube/etc.).

    Uses segments.ad_network_type to break down where PMax actually served.
    Channel-level data is only reliable from 2025-06-01 onwards — earlier
    rows return MIXED. The tool emits a warning in insights when the date
    range overlaps that period.

    campaign_id: optional filter to a single campaign.
    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.ads.pmax_read import get_pmax_channel_breakdown as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        campaign_id=campaign_id,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_asset_groups(
    customer_id: str = "",
    campaign_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """List PMax asset groups with their final URLs, paths, ad strength, and metrics.

    Asset groups are the PMax equivalent of ad groups — each contains a bundle
    of assets (headlines, descriptions, images, logos, videos) that Google
    assembles dynamically. Ad strength values: POOR | AVERAGE | GOOD | EXCELLENT.

    campaign_id: optional filter to a single campaign.
    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.ads.pmax_read import get_asset_groups as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        campaign_id=campaign_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_asset_group_assets(
    customer_id: str = "",
    asset_group_id: str = "",
    campaign_id: str = "",
) -> dict:
    """List individual assets in PMax asset groups with field type and policy review.

    Returns asset id/type, field_type (HEADLINE, DESCRIPTION, MARKETING_IMAGE,
    LOGO, YOUTUBE_VIDEO, etc.), status, policy_summary.review_status, and the
    text content, image URL, or YouTube video id/title/url depending on type.

    Note: the LOW/GOOD/BEST/PENDING performance_label was removed from
    asset_group_asset in Google Ads API v24. To judge per-asset performance
    now, query metrics directly via asset_field_type_view, or use
    get_asset_group_top_combinations to see which combinations actually serve.

    Provide either asset_group_id (single group) or campaign_id (all groups in
    the campaign). With both empty, returns all assets across all PMax campaigns.
    """
    from adloop.ads.pmax_read import get_asset_group_assets as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        asset_group_id=asset_group_id,
        campaign_id=campaign_id,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_asset_group_signals(
    customer_id: str = "",
    asset_group_id: str = "",
    campaign_id: str = "",
) -> dict:
    """List audience and search-theme signals attached to PMax asset groups.

    Signals are not hard targeting — they are hints to Google's algorithm about
    who and what kind of search intent the asset group should match. Each row
    has signal_type = SEARCH_THEME or AUDIENCE.

    Provide either asset_group_id or campaign_id. Both empty returns all signals
    across all PMax campaigns.
    """
    from adloop.ads.pmax_read import get_asset_group_signals as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        asset_group_id=asset_group_id,
        campaign_id=campaign_id,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_asset_group_top_combinations(
    customer_id: str = "",
    asset_group_id: str = "",
    campaign_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """Get the asset combinations Google has assembled at serve time for PMax.

    Each row's asset_group_top_combinations field is a repeated message of
    the assets that served together (headlines, descriptions, images, optional
    video). The view does NOT expose metrics in v24 — the API rejects any
    metrics.* field on this resource. Combinations come pre-ordered by Google
    by serving frequency.

    Provide either asset_group_id or campaign_id. Returns up to 50 rows.
    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.ads.pmax_read import get_asset_group_top_combinations as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        asset_group_id=asset_group_id,
        campaign_id=campaign_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def get_pmax_search_terms(
    campaign_id: str,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """Get aggregated search-term category insights for a Performance Max campaign.

    Note: PMax does NOT expose individual search terms (Google's design choice).
    This returns category labels (e.g. "Buy women's running shoes") aggregated
    across many real queries, with impression and click counts. The Google Ads
    API does NOT expose cost, conversions, or conversions_value on
    campaign_search_term_insight (PROHIBITED_METRIC_IN_SELECT_OR_WHERE_CLAUSE),
    so per-category cost is not available.

    campaign_id is REQUIRED — these insights are queried per-campaign.
    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.ads.pmax_read import get_pmax_search_terms as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        campaign_id=campaign_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def analyze_pmax_performance(
    date_range_start: str = "",
    date_range_end: str = "",
    customer_id: str = "",
    property_id: str = "",
    campaign_id: str = "",
) -> dict:
    """Comprehensive PMax diagnostic — campaign + asset groups + assets + channels + GA4.

    Pulls everything you can inspect about Performance Max in one call:
    campaign metrics + bidding/brand-guidelines settings, every asset group
    with its ad strength and asset counts, channel-mix breakdown, and (when
    property_id is configured) GA4 paid sessions/conversions per campaign.

    Returns auto-generated insights[] flagging:
    - Asset groups with POOR or AVERAGE ad strength
    - Asset groups below the documented PMax asset-type minimums
    - Channel skew (e.g. >90% of spend on a single surface)
    - Zero-conversion campaigns despite spend
    - GDPR consent gaps (click-to-session ratio > 2:1)
    - Pre-2025-06-01 channel breakdown caveats

    campaign_id: optional filter — when provided, returns only that PMax campaign.
    Date format: "YYYY-MM-DD". Empty = last 30 days.
    """
    from adloop.crossref import analyze_pmax_performance as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        property_id=property_id or _config.ga4.property_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        campaign_id=campaign_id,
    )


# ---------------------------------------------------------------------------
# Performance Max Write Tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_WRITE)
@_safe
def draft_pmax_campaign(
    campaign_name: str,
    daily_budget: float,
    bidding_strategy: str,
    geo_target_ids: list[str],
    language_ids: list[str],
    asset_group: dict,
    customer_id: str = "",
    target_cpa: float = 0,
    target_roas: float = 0,
    final_url_suffix: str | None = None,
) -> dict:
    """Draft a Performance Max campaign with its first asset group — returns PREVIEW.

    Creates: CampaignBudget + Campaign (PAUSED, no network_settings) + geo +
    language + AssetGroup (PAUSED) + Assets + AssetGroupAsset links + Signals
    in one atomic mutate. PMax requires this all-in-one shape.

    bidding_strategy: PMax accepts only Smart Bidding —
        MAXIMIZE_CONVERSIONS | MAXIMIZE_CONVERSION_VALUE | TARGET_CPA | TARGET_ROAS
    target_cpa / target_roas: required when bidding_strategy is the matching name.
    geo_target_ids / language_ids: REQUIRED — same constants as draft_campaign.

    asset_group dict: see draft_pmax_campaign in pmax_write.py. Keys:
        - name (str): asset group name
        - final_urls (list[str]): at least one
        - path1, path2 (str, optional, <=15 chars)
        - headlines (list[str], 3-5, <=30 chars)
        - long_headlines (list[str], 1-5, <=90 chars)
        - descriptions (list[str], 2-5, <=90 chars)
        - business_name (str, <=25 chars)
        - marketing_image_assets (list[str]): resource_names of pre-uploaded
          1.91:1 images. PMax requires at least one.
        - square_marketing_image_assets (list[str]): resource_names of pre-
          uploaded 1:1 images. At least one required.
        - logo_assets (list[str]): resource_names of pre-uploaded logos. At
          least one required.
        - youtube_video_ids (list[str], optional)
        - search_themes (list[str], optional): SearchTheme signal phrases
        - audience_resource_names (list[str], optional): Audience resource_names

    NOTE: image/logo assets cannot be created inline through this MCP — pre-
    upload via Google Ads UI or AssetService.MutateAssets, then pass the
    resource_name strings.

    Call confirm_and_apply with the returned plan_id to execute. The new
    campaign is created as PAUSED — enable_entity it after review.
    """
    from adloop.ads.pmax_write import draft_pmax_campaign as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        campaign_name=campaign_name,
        daily_budget=daily_budget,
        bidding_strategy=bidding_strategy,
        target_cpa=target_cpa,
        target_roas=target_roas,
        geo_target_ids=geo_target_ids,
        language_ids=language_ids,
        final_url_suffix=final_url_suffix,
        asset_group=asset_group,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def draft_asset_group(
    campaign_id: str,
    asset_group: dict,
    customer_id: str = "",
) -> dict:
    """Draft a new asset group inside an existing PMax campaign — returns PREVIEW.

    asset_group has the same shape as draft_pmax_campaign's asset_group field.
    See that tool's docstring for the full schema.

    Call confirm_and_apply with the returned plan_id to execute.
    """
    from adloop.ads.pmax_write import draft_asset_group as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        campaign_id=campaign_id,
        asset_group=asset_group,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def draft_asset_group_assets(
    asset_group_id: str,
    customer_id: str = "",
    headlines: list[str] = [],
    long_headlines: list[str] = [],
    descriptions: list[str] = [],
    business_name: str = "",
    marketing_image_assets: list[str] = [],
    square_marketing_image_assets: list[str] = [],
    logo_assets: list[str] = [],
    youtube_video_ids: list[str] = [],
) -> dict:
    """Draft attaching new assets to an existing asset group — returns PREVIEW.

    Use this to add more headlines, descriptions, images, etc. to an asset
    group that already exists. Each text/youtube asset is created inline (one
    Asset.create + one AssetGroupAsset.create per item). Image and logo assets
    must already exist in the account — pass their resource_names.

    Char limits: headlines <=30, long_headlines <=90, descriptions <=90,
    business_name <=25.

    Call confirm_and_apply with the returned plan_id to execute.
    """
    from adloop.ads.pmax_write import draft_asset_group_assets as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        asset_group_id=asset_group_id,
        headlines=headlines,
        long_headlines=long_headlines,
        descriptions=descriptions,
        business_name=business_name,
        marketing_image_assets=marketing_image_assets,
        square_marketing_image_assets=square_marketing_image_assets,
        logo_assets=logo_assets,
        youtube_video_ids=youtube_video_ids,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def draft_asset_group_signal(
    asset_group_id: str,
    customer_id: str = "",
    search_theme: str = "",
    audience_resource_name: str = "",
) -> dict:
    """Draft a new signal (search theme OR audience) on an asset group — returns PREVIEW.

    Pass exactly one of search_theme (a phrase) or audience_resource_name
    (a 'customers/.../audiences/...' resource name). Search themes are
    immutable once created — to "edit", remove the old signal and add a new
    one.

    Call confirm_and_apply with the returned plan_id to execute.
    """
    from adloop.ads.pmax_write import draft_asset_group_signal as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        asset_group_id=asset_group_id,
        search_theme=search_theme,
        audience_resource_name=audience_resource_name,
    )


# ---------------------------------------------------------------------------
# Label Tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READONLY)
@_safe
def list_labels(customer_id: str = "") -> dict:
    """List all labels in the Google Ads account.

    Returns each label's id, name, status, description, and background_color.
    Use the IDs returned here with apply_label / unapply_label / remove_entity.
    """
    from adloop.ads.labels import list_labels as _impl

    return _impl(_config, customer_id=customer_id or _config.ads.customer_id)


@mcp.tool(annotations=_WRITE)
@_safe
def draft_label(
    name: str,
    customer_id: str = "",
    description: str = "",
    background_color: str = "",
) -> dict:
    """Draft creating a new Label — returns PREVIEW.

    name: required. Must be unique in the account.
    description: optional human description.
    background_color: optional hex color string like '#FF5733'.

    Call confirm_and_apply with the returned plan_id to execute.
    """
    from adloop.ads.labels import draft_label as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        name=name,
        description=description,
        background_color=background_color,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def apply_label(
    entity_type: str,
    entity_id: str,
    label_id: str,
    customer_id: str = "",
) -> dict:
    """Draft attaching a label to a campaign/ad_group/ad/keyword — returns PREVIEW.

    entity_type: 'campaign', 'ad_group', 'ad', or 'keyword'.
    entity_id: bare ID for campaign/ad_group, 'adGroupId~adId' for ad,
        'adGroupId~criterionId' for keyword.
    label_id: the ID of an existing Label (use list_labels to discover them).

    Call confirm_and_apply with the returned plan_id to execute.
    """
    from adloop.ads.labels import draft_apply_label as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        entity_type=entity_type,
        entity_id=entity_id,
        label_id=label_id,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def unapply_label(
    entity_type: str,
    entity_id: str,
    label_id: str,
    customer_id: str = "",
) -> dict:
    """Draft detaching a label from an entity (does NOT delete the Label itself).

    To delete the Label resource itself, use remove_entity with
    entity_type='label'.

    Call confirm_and_apply with the returned plan_id to execute.
    """
    from adloop.ads.labels import draft_unapply_label as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        entity_type=entity_type,
        entity_id=entity_id,
        label_id=label_id,
    )


# ---------------------------------------------------------------------------
# Custom GAQL
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READONLY)
@_safe
def run_gaql(
    query: str,
    customer_id: str = "",
    format: str = "table",
) -> dict:
    """Execute an arbitrary GAQL (Google Ads Query Language) query.

    Use this for advanced queries not covered by the other tools.
    See the GAQL reference in the AdLoop cursor rules for syntax help.

    format: "table" (default, readable), "json" (structured), "csv" (exportable)
    """
    from adloop.ads.gaql import run_gaql as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        query=query,
        format=format,
    )


# ---------------------------------------------------------------------------
# Google Ads Write Tools (Safety Layer)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_WRITE)
@_safe
def draft_campaign(
    campaign_name: str,
    daily_budget: float,
    bidding_strategy: str,
    geo_target_ids: list[str],
    language_ids: list[str],
    customer_id: str = "",
    target_cpa: float = 0,
    target_roas: float = 0,
    channel_type: str = "SEARCH",
    ad_group_name: str = "",
    keywords: list[dict] = [],
    final_url_suffix: str | None = None,
) -> dict:
    """Draft a full campaign structure — returns a PREVIEW, does NOT create anything.

    Creates: CampaignBudget + Campaign (PAUSED) + AdGroup + optional Keywords
    + geo targeting + language targeting.
    Ads are NOT included — use draft_responsive_search_ad after the campaign exists.

    bidding_strategy: MAXIMIZE_CONVERSIONS | TARGET_CPA | TARGET_ROAS |
                      MAXIMIZE_CONVERSION_VALUE | TARGET_SPEND | MANUAL_CPC
    target_cpa: required if bidding_strategy is TARGET_CPA (in account currency)
    target_roas: required if bidding_strategy is TARGET_ROAS
    keywords: list of {"text": "keyword", "match_type": "EXACT|PHRASE|BROAD"}
    geo_target_ids: REQUIRED list of geo target constant IDs
        Common: "2276" Germany, "2040" Austria, "2756" Switzerland, "2840" USA,
        "2826" UK, "2250" France. Full list: Google Ads API geo target constants.
    language_ids: REQUIRED list of language constant IDs
        Common: "1001" German, "1000" English, "1002" French, "1004" Spanish,
        "1014" Portuguese. Full list: Google Ads API language constants.
    final_url_suffix: UTM suffix auto-applied to SEARCH campaigns. Pass "" to
        disable. Defaults to standard UTM tracking with ValueTrack parameters:
        utm_source=google&utm_medium=cpc&utm_campaign={campaignid}&utm_content={adgroupid}&utm_term={keyword}

    Call confirm_and_apply with the returned plan_id to execute.
    """
    from adloop.ads.write import draft_campaign as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        campaign_name=campaign_name,
        daily_budget=daily_budget,
        bidding_strategy=bidding_strategy,
        target_cpa=target_cpa,
        target_roas=target_roas,
        channel_type=channel_type,
        ad_group_name=ad_group_name,
        keywords=keywords,
        geo_target_ids=geo_target_ids,
        language_ids=language_ids,
        final_url_suffix=final_url_suffix,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def draft_ad_group(
    campaign_id: str,
    ad_group_name: str,
    keywords: list[dict] = [],
    customer_id: str = "",
    cpc_bid_micros: int = 0,
) -> dict:
    """Draft a new ad group within an existing campaign — returns a PREVIEW, does NOT create.

    Creates an ad group (ENABLED, type SEARCH_STANDARD) in the specified campaign.
    Optionally includes keywords in the same atomic operation.

    campaign_id: The campaign to add the ad group to (get from get_campaign_performance).
    ad_group_name: Name for the new ad group.
    keywords: Optional list of {"text": "keyword", "match_type": "EXACT|PHRASE|BROAD"}.
    cpc_bid_micros: Optional ad group CPC bid in micros (only for MANUAL_CPC campaigns).

    Call confirm_and_apply with the returned plan_id to execute.
    """
    from adloop.ads.write import draft_ad_group as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        campaign_id=campaign_id,
        ad_group_name=ad_group_name,
        keywords=keywords,
        cpc_bid_micros=cpc_bid_micros,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def update_campaign(
    campaign_id: str,
    customer_id: str = "",
    bidding_strategy: str = "",
    target_cpa: float = 0,
    target_roas: float = 0,
    daily_budget: float = 0,
    geo_target_ids: list[str] = [],
    language_ids: list[str] = [],
    final_url_suffix: str | None = None,
) -> dict:
    """Draft an update to an existing campaign — returns a PREVIEW, does NOT apply.

    Only include the parameters you want to change. Omit the rest.

    campaign_id: the numeric ID of the campaign to update (required)
    bidding_strategy: MAXIMIZE_CONVERSIONS | TARGET_CPA | TARGET_ROAS |
                      MAXIMIZE_CONVERSION_VALUE | TARGET_SPEND | MANUAL_CPC
    target_cpa: required if bidding_strategy is TARGET_CPA (in account currency)
    target_roas: required if bidding_strategy is TARGET_ROAS
    daily_budget: new daily budget in account currency
    geo_target_ids: REPLACES all geo targets. Common IDs: "2276" Germany,
        "2040" Austria, "2756" Switzerland, "2840" USA, "2826" UK
    language_ids: REPLACES all language targets. Common IDs: "1001" German,
        "1000" English, "1002" French, "1004" Spanish
    final_url_suffix: set or change the campaign's Final URL suffix. Pass "" to clear.

    Call confirm_and_apply with the returned plan_id to execute.
    """
    from adloop.ads.write import update_campaign as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        campaign_id=campaign_id,
        bidding_strategy=bidding_strategy,
        target_cpa=target_cpa,
        target_roas=target_roas,
        daily_budget=daily_budget,
        geo_target_ids=geo_target_ids or None,
        language_ids=language_ids or None,
        final_url_suffix=final_url_suffix,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def draft_responsive_search_ad(
    ad_group_id: str,
    headlines: list[str | dict],
    descriptions: list[str | dict],
    final_url: str,
    customer_id: str = "",
    path1: str = "",
    path2: str = "",
) -> dict:
    """Draft a Responsive Search Ad — returns a PREVIEW, does NOT create the ad.

    Provide 3-15 headlines (max 30 chars each) and 2-4 descriptions (max 90 chars each).
    The preview shows exactly what will be created. Call confirm_and_apply to execute.

    Each headline/description can be a plain string (unpinned) or a dict with
    optional pinning: {"text": "...", "pinned_to": "HEADLINE_1"}.
    Valid headline pins: HEADLINE_1, HEADLINE_2, HEADLINE_3.
    Valid description pins: DESCRIPTION_1, DESCRIPTION_2.
    Multiple assets can be pinned to the same position (they rotate).
    """
    from adloop.ads.write import draft_responsive_search_ad as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        ad_group_id=ad_group_id,
        headlines=headlines,
        descriptions=descriptions,
        final_url=final_url,
        path1=path1,
        path2=path2,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def draft_rsa_replacement(
    ad_id: str,
    headlines: list[str | dict],
    descriptions: list[str | dict],
    final_url: str = "",
    customer_id: str = "",
    path1: str = "",
    path2: str = "",
    remove_old: bool = True,
) -> dict:
    """Fix an existing RSA — creates a corrected replacement and removes the old ad.

    Use this to fix issues with an existing RSA: wrong copy, character errors,
    data inconsistencies, truncated names, etc. The old ad is REMOVED by default
    so it cannot be accidentally re-enabled.

    For A/B testing or adding ad variants, use draft_responsive_search_ad instead.

    Provide the ad_id of the RSA to fix, plus the complete corrected copy.
    The tool fetches the old ad's details and shows a side-by-side diff preview.
    The new ad inherits the ad group from the old one and is created as PAUSED.
    If final_url is omitted, the old ad's URL is reused.
    Call confirm_and_apply with the returned plan_id to execute.

    Each headline/description can be a plain string (unpinned) or a dict with
    optional pinning: {"text": "...", "pinned_to": "HEADLINE_1"}.
    Valid headline pins: HEADLINE_1, HEADLINE_2, HEADLINE_3.
    Valid description pins: DESCRIPTION_1, DESCRIPTION_2.
    """
    from adloop.ads.write import draft_rsa_replacement as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        ad_id=ad_id,
        headlines=headlines,
        descriptions=descriptions,
        final_url=final_url,
        path1=path1,
        path2=path2,
        remove_old=remove_old,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def draft_keywords(
    ad_group_id: str,
    keywords: list[dict],
    customer_id: str = "",
) -> dict:
    """Draft keyword additions — returns a PREVIEW, does NOT add keywords.

    keywords: list of {"text": "keyword phrase", "match_type": "EXACT|PHRASE|BROAD"}
    Call confirm_and_apply with the returned plan_id to execute.
    """
    from adloop.ads.write import draft_keywords as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        ad_group_id=ad_group_id,
        keywords=keywords,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def add_negative_keywords(
    campaign_id: str,
    keywords: list[str],
    customer_id: str = "",
    match_type: str = "EXACT",
) -> dict:
    """Draft negative keyword additions — returns a PREVIEW.

    Negative keywords prevent your ads from showing for irrelevant searches.
    match_type: "EXACT", "PHRASE", or "BROAD"
    Call confirm_and_apply with the returned plan_id to execute.
    """
    from adloop.ads.write import add_negative_keywords as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        campaign_id=campaign_id,
        keywords=keywords,
        match_type=match_type,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def pause_entity(
    entity_type: str,
    entity_id: str,
    customer_id: str = "",
) -> dict:
    """Draft pausing a campaign, ad group, ad, or keyword — returns a PREVIEW.

    entity_type: "campaign", "ad_group", "ad", "keyword", or "asset_group"
    entity_id format by type:
      - campaign: campaign ID (e.g. "12345678")
      - ad_group: ad group ID (e.g. "12345678")
      - ad: "adGroupId~adId" (e.g. "12345678~987654")
      - keyword: "adGroupId~criterionId" (e.g. "12345678~987654")
      - asset_group: asset group ID (e.g. "6572147947")

    Call confirm_and_apply with the returned plan_id to execute.
    """
    from adloop.ads.write import pause_entity as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        entity_type=entity_type,
        entity_id=entity_id,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def enable_entity(
    entity_type: str,
    entity_id: str,
    customer_id: str = "",
) -> dict:
    """Draft enabling a paused campaign, ad group, ad, or keyword — returns a PREVIEW.

    entity_type: "campaign", "ad_group", "ad", "keyword", or "asset_group"
    entity_id format by type:
      - campaign: campaign ID (e.g. "12345678")
      - ad_group: ad group ID (e.g. "12345678")
      - ad: "adGroupId~adId" (e.g. "12345678~987654")
      - keyword: "adGroupId~criterionId" (e.g. "12345678~987654")
      - asset_group: asset group ID (e.g. "6572147947")

    Call confirm_and_apply with the returned plan_id to execute.
    """
    from adloop.ads.write import enable_entity as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        entity_type=entity_type,
        entity_id=entity_id,
    )


@mcp.tool(annotations=_DESTRUCTIVE)
@_safe
def remove_entity(
    entity_type: str,
    entity_id: str,
    customer_id: str = "",
) -> dict:
    """Draft REMOVING an entity — returns a PREVIEW. This is IRREVERSIBLE.

    entity_type: "campaign", "ad_group", "ad", "keyword", "negative_keyword",
                 "asset_group", "campaign_asset", or "label"
    entity_id: The resource ID. For keywords use "adGroupId~criterionId".
               For negative_keywords use the campaign criterion ID.
               For campaign_assets use "campaignId~assetId~fieldType".
               For asset_groups use the asset group ID.
               For labels use the label ID (cascades to all assignments).

    WARNING: Removed entities cannot be re-enabled. Use pause_entity instead
    if you just want to temporarily disable something. To detach a label from
    a single entity (without deleting the Label itself), use unapply_label.

    Call confirm_and_apply with the returned plan_id to execute.
    """
    from adloop.ads.write import remove_entity as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        entity_type=entity_type,
        entity_id=entity_id,
    )


@mcp.tool(annotations=_WRITE)
@_safe
def draft_sitelinks(
    campaign_id: str,
    sitelinks: list[dict],
    customer_id: str = "",
) -> dict:
    """Draft sitelink extensions for a campaign — returns a PREVIEW.

    Sitelinks appear as additional links below your ad, increasing click area
    and directing users to specific pages.

    campaign_id: the campaign to attach sitelinks to
    sitelinks: list of dicts, each with:
        - link_text (str, required, max 25 chars) — the clickable text shown
        - final_url (str, required) — destination URL for this sitelink
        - description1 (str, optional, max 35 chars) — first description line
        - description2 (str, optional, max 35 chars) — second description line

    Google recommends at least 4 sitelinks per campaign. Fewer than 2 may not show.

    Call confirm_and_apply with the returned plan_id to execute.
    """
    from adloop.ads.write import draft_sitelinks as _impl

    return _impl(
        _config,
        customer_id=customer_id or _config.ads.customer_id,
        campaign_id=campaign_id,
        sitelinks=sitelinks,
    )


@mcp.tool(annotations=_DESTRUCTIVE)
@_safe
def confirm_and_apply(
    plan_id: str,
    dry_run: bool = True,
) -> dict:
    """Execute a previously previewed change.

    IMPORTANT: Defaults to dry_run=True. You MUST explicitly pass dry_run=false
    to make real changes to the Google Ads account.

    The plan_id comes from a prior draft_* or pause/enable tool call.
    """
    from adloop.ads.write import confirm_and_apply as _impl

    return _impl(_config, plan_id=plan_id, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Tracking Tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READONLY)
@_safe
def validate_tracking(
    expected_events: list[str],
    property_id: str = "",
    date_range_start: str = "28daysAgo",
    date_range_end: str = "today",
    customer_id: str = "",
) -> dict:
    """Compare tracking events found in the codebase against actual GA4 data.

    First, search the user's codebase for gtag('event', ...) or dataLayer.push
    calls and extract event names. Then pass those names here to check which
    ones actually fire in GA4.

    Returns: matched events, events missing from GA4, unexpected GA4 events,
    and auto-collected events (page_view, session_start, etc.).

    customer_id: optional — when provided, also pulls Google Ads conversion
    actions and checks which expected events have matching Ads conversion
    actions configured.
    """
    from adloop.tracking import validate_tracking as _impl

    return _impl(
        _config,
        expected_events=expected_events,
        property_id=property_id or _config.ga4.property_id,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        customer_id=customer_id,
    )


@mcp.tool(annotations=_READONLY)
@_safe
def generate_tracking_code(
    event_name: str,
    event_params: dict | None = None,
    trigger: str = "",
    property_id: str = "",
    check_existing: bool = True,
) -> dict:
    """Generate a GA4 event tracking JavaScript snippet.

    Produces ready-to-paste gtag code for the specified event. Includes
    recommended parameters for well-known GA4 events (sign_up, purchase, etc.).
    Optionally checks GA4 to warn if the event already fires.

    trigger: "form_submit", "button_click", or "page_load" — wraps the gtag
    call in an appropriate event listener. Empty = bare gtag call.
    """
    from adloop.tracking import generate_tracking_code as _impl

    return _impl(
        _config,
        event_name=event_name,
        event_params=event_params,
        trigger=trigger,
        property_id=property_id or _config.ga4.property_id,
        check_existing=check_existing,
    )


# ---------------------------------------------------------------------------
# Planning Tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READONLY)
@_safe
def estimate_budget(
    keywords: list[dict],
    daily_budget: float = 0,
    geo_target_id: str = "2276",
    language_id: str = "1000",
    forecast_days: int = 30,
    customer_id: str = "",
) -> dict:
    """Forecast clicks, impressions, and cost for a set of keywords.

    Uses Google Ads Keyword Planner to estimate campaign performance without
    creating anything. Essential for budget planning before launching campaigns.

    keywords: list of {"text": "keyword", "match_type": "EXACT|PHRASE|BROAD", "max_cpc": 1.50}
        max_cpc is optional (defaults to 1.00 in account currency)
    geo_target_id: geo target constant (2276=Germany, 2840=USA, 2826=UK, 2250=France)
    language_id: language constant (1000=English, 1001=German, 1002=French, 1003=Spanish)
    daily_budget: if provided, insights will show what % of traffic the budget captures
    forecast_days: forecast horizon in days (default 30)
    """
    from adloop.ads.forecast import estimate_budget as _impl

    return _impl(
        _config,
        keywords=keywords,
        daily_budget=daily_budget,
        geo_target_id=geo_target_id,
        language_id=language_id,
        forecast_days=forecast_days,
        customer_id=customer_id or _config.ads.customer_id,
    )
