"""Google Ads read tools — campaign, ad, keyword, search term, and insights performance."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adloop.config import AdLoopConfig


def list_accounts(config: AdLoopConfig) -> dict:
    """List all accessible Google Ads accounts."""
    from adloop.ads.gaql import execute_query

    mcc_id = config.ads.login_customer_id
    if mcc_id:
        query = """
            SELECT customer_client.id, customer_client.descriptive_name,
                   customer_client.status, customer_client.manager
            FROM customer_client
        """
        rows = execute_query(config, mcc_id, query)
    else:
        query = """
            SELECT customer.id, customer.descriptive_name,
                   customer.status, customer.manager
            FROM customer
            LIMIT 1
        """
        rows = execute_query(config, config.ads.customer_id, query)

    return {"accounts": rows, "total_accounts": len(rows)}


def get_campaign_performance(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """Get campaign-level performance metrics for the given date range."""
    from adloop.ads.gaql import execute_query

    date_clause = _date_clause(date_range_start, date_range_end)

    query = f"""
        SELECT campaign.id, campaign.name, campaign.status,
               campaign.advertising_channel_type, campaign.bidding_strategy_type,
               metrics.impressions, metrics.clicks, metrics.cost_micros,
               metrics.conversions, metrics.conversions_value,
               metrics.ctr, metrics.average_cpc
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          {date_clause}
        ORDER BY metrics.cost_micros DESC
    """

    rows = execute_query(config, customer_id, query)
    _enrich_cost_fields(rows)

    return {"campaigns": rows, "total_campaigns": len(rows)}


def get_ad_performance(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """Get ad-level performance data including headlines, descriptions, and metrics."""
    from adloop.ads.gaql import execute_query

    date_clause = _date_clause(date_range_start, date_range_end)

    query = f"""
        SELECT campaign.name, campaign.id, ad_group.name, ad_group.id,
               ad_group_ad.ad.id, ad_group_ad.ad.type,
               ad_group_ad.ad.responsive_search_ad.headlines,
               ad_group_ad.ad.responsive_search_ad.descriptions,
               ad_group_ad.ad.final_urls,
               ad_group_ad.status,
               metrics.impressions, metrics.clicks, metrics.ctr,
               metrics.conversions, metrics.cost_micros
        FROM ad_group_ad
        WHERE ad_group_ad.status != 'REMOVED'
          {date_clause}
        ORDER BY metrics.cost_micros DESC
    """

    rows = execute_query(config, customer_id, query)
    _enrich_cost_fields(rows)

    return {"ads": rows, "total_ads": len(rows)}


def get_keyword_performance(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """Get keyword metrics including quality scores and competitive data."""
    from adloop.ads.gaql import execute_query

    date_clause = _date_clause(date_range_start, date_range_end)

    query = f"""
        SELECT campaign.name, ad_group.name,
               ad_group_criterion.keyword.text,
               ad_group_criterion.keyword.match_type,
               ad_group_criterion.quality_info.quality_score,
               metrics.impressions, metrics.clicks, metrics.ctr,
               metrics.average_cpc, metrics.cost_micros,
               metrics.conversions
        FROM keyword_view
        WHERE ad_group_criterion.status != 'REMOVED'
          {date_clause}
        ORDER BY metrics.cost_micros DESC
    """

    rows = execute_query(config, customer_id, query)
    _enrich_cost_fields(rows)

    return {"keywords": rows, "total_keywords": len(rows)}


def get_search_terms(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """Get search terms report — what users actually typed before clicking ads."""
    from adloop.ads.gaql import execute_query

    date_clause = _date_clause(date_range_start, date_range_end)

    query = f"""
        SELECT search_term_view.search_term,
               campaign.name, ad_group.name,
               metrics.impressions, metrics.clicks,
               metrics.cost_micros, metrics.conversions
        FROM search_term_view
        WHERE segments.date DURING LAST_30_DAYS
          {f"AND segments.date BETWEEN '{date_range_start}' AND '{date_range_end}'" if date_range_start and date_range_end else ""}
        ORDER BY metrics.clicks DESC
        LIMIT 200
    """
    # search_term_view requires an explicit date segment, so we always
    # include DURING LAST_30_DAYS as baseline and override if dates given.
    if date_range_start and date_range_end:
        query = f"""
            SELECT search_term_view.search_term,
                   campaign.name, ad_group.name,
                   metrics.impressions, metrics.clicks,
                   metrics.cost_micros, metrics.conversions
            FROM search_term_view
            WHERE segments.date BETWEEN '{date_range_start}' AND '{date_range_end}'
            ORDER BY metrics.clicks DESC
            LIMIT 200
        """
    else:
        query = """
            SELECT search_term_view.search_term,
                   campaign.name, ad_group.name,
                   metrics.impressions, metrics.clicks,
                   metrics.cost_micros, metrics.conversions
            FROM search_term_view
            WHERE segments.date DURING LAST_30_DAYS
            ORDER BY metrics.clicks DESC
            LIMIT 200
        """

    rows = execute_query(config, customer_id, query)
    _enrich_cost_fields(rows)

    return {"search_terms": rows, "total_search_terms": len(rows)}


def get_negative_keywords(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    campaign_id: str = "",
) -> dict:
    """List negative keywords for a campaign or all campaigns."""
    from adloop.ads.gaql import execute_query

    campaign_filter = ""
    if campaign_id:
        campaign_filter = f"AND campaign.id = {campaign_id}"

    query = f"""
        SELECT campaign.id, campaign.name,
               campaign_criterion.keyword.text,
               campaign_criterion.keyword.match_type,
               campaign_criterion.negative,
               campaign_criterion.criterion_id
        FROM campaign_criterion
        WHERE campaign_criterion.negative = TRUE
          AND campaign_criterion.status != 'REMOVED'
          {campaign_filter}
        ORDER BY campaign.name
    """

    rows = execute_query(config, customer_id, query)
    return {"negative_keywords": rows, "total_negative_keywords": len(rows)}


# ---------------------------------------------------------------------------
# Impression Share & Insights Tools
# ---------------------------------------------------------------------------


def get_impression_share(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    level: str = "campaign",
) -> dict:
    """Get impression share metrics segmented by campaign, ad group, or keyword."""
    from adloop.ads.gaql import execute_query

    date_clause = _date_clause(date_range_start, date_range_end)

    share_metrics = """metrics.impressions, metrics.clicks, metrics.cost_micros,
               metrics.search_impression_share,
               metrics.search_budget_lost_impression_share,
               metrics.search_rank_lost_impression_share,
               metrics.search_exact_match_impression_share,
               metrics.search_top_impression_share,
               metrics.search_absolute_top_impression_share"""

    if level == "ad_group":
        query = f"""
            SELECT campaign.name, ad_group.id, ad_group.name,
                   {share_metrics}
            FROM ad_group
            WHERE ad_group.status != 'REMOVED'
              {date_clause}
            ORDER BY metrics.impressions DESC
        """
    elif level == "keyword":
        query = f"""
            SELECT campaign.name, ad_group.name,
                   ad_group_criterion.keyword.text,
                   ad_group_criterion.keyword.match_type,
                   {share_metrics}
            FROM keyword_view
            WHERE ad_group_criterion.status != 'REMOVED'
              {date_clause}
            ORDER BY metrics.impressions DESC
        """
    else:
        query = f"""
            SELECT campaign.id, campaign.name, campaign.status,
                   {share_metrics}
            FROM campaign
            WHERE campaign.status != 'REMOVED'
              {date_clause}
            ORDER BY metrics.impressions DESC
        """

    rows = execute_query(config, customer_id, query)
    _enrich_cost_fields(rows)
    _enrich_impression_share_fields(rows)

    return {"impression_share": rows, "total_rows": len(rows), "level": level}


def get_change_history(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    resource_type: str = "",
    operation_type: str = "",
    limit: int = 100,
) -> dict:
    """Get account change history from the change_event resource."""
    from adloop.ads.gaql import execute_query

    # change_event has a hard API max of 10,000 rows.
    limit = max(1, min(limit, 10_000))

    resource_filter = ""
    if resource_type:
        resource_filter = (
            f"AND change_event.change_resource_type = '{resource_type}'"
        )

    operation_filter = ""
    if operation_type:
        operation_filter = (
            f"AND change_event.resource_change_operation = '{operation_type}'"
        )

    # change_event uses change_date_time (a timestamp), not segments.date.
    # A bare date like '2026-03-27' means midnight, which misses the rest
    # of that day.  Append end-of-day time when only a date is provided.
    if date_range_start and date_range_end:
        end = date_range_end
        if "T" not in end and " " not in end:
            end = f"{end} 23:59:59"
        date_where = (
            f"change_event.change_date_time >= '{date_range_start}'"
            f" AND change_event.change_date_time <= '{end}'"
        )
    else:
        date_where = "change_event.change_date_time DURING LAST_14_DAYS"

    query = f"""
        SELECT change_event.change_date_time,
               change_event.user_email,
               change_event.change_resource_type,
               change_event.resource_change_operation,
               change_event.changed_fields,
               change_event.old_resource,
               change_event.new_resource,
               change_event.resource_name
        FROM change_event
        WHERE {date_where}
          {resource_filter}
          {operation_filter}
        ORDER BY change_event.change_date_time DESC
        LIMIT {limit}
    """

    rows = execute_query(config, customer_id, query)
    return {"changes": rows, "total_changes": len(rows)}


def get_device_performance(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    level: str = "campaign",
) -> dict:
    """Get performance segmented by device (MOBILE, DESKTOP, TABLET)."""
    from adloop.ads.gaql import execute_query

    date_clause = _date_clause(date_range_start, date_range_end)

    if level == "ad_group":
        query = f"""
            SELECT campaign.name, ad_group.id, ad_group.name,
                   segments.device,
                   metrics.impressions, metrics.clicks, metrics.ctr,
                   metrics.cost_micros, metrics.average_cpc,
                   metrics.conversions, metrics.conversions_value
            FROM ad_group
            WHERE ad_group.status != 'REMOVED'
              {date_clause}
            ORDER BY ad_group.name, metrics.cost_micros DESC
        """
    else:
        query = f"""
            SELECT campaign.id, campaign.name,
                   segments.device,
                   metrics.impressions, metrics.clicks, metrics.ctr,
                   metrics.cost_micros, metrics.average_cpc,
                   metrics.conversions, metrics.conversions_value
            FROM campaign
            WHERE campaign.status != 'REMOVED'
              {date_clause}
            ORDER BY campaign.name, metrics.cost_micros DESC
        """

    rows = execute_query(config, customer_id, query)
    _enrich_cost_fields(rows)
    _enrich_conversion_rate(rows)

    return {"device_performance": rows, "total_rows": len(rows), "level": level}


def get_location_performance(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
) -> dict:
    """Get performance segmented by geographic location."""
    from adloop.ads.gaql import execute_query

    # geographic_view requires segments.date in WHERE, like search_term_view.
    if date_range_start and date_range_end:
        query = f"""
            SELECT geographic_view.country_criterion_id,
                   geographic_view.location_type,
                   campaign.name,
                   metrics.impressions, metrics.clicks, metrics.ctr,
                   metrics.cost_micros, metrics.conversions,
                   metrics.conversions_value
            FROM geographic_view
            WHERE segments.date BETWEEN '{date_range_start}' AND '{date_range_end}'
            ORDER BY metrics.cost_micros DESC
            LIMIT 200
        """
    else:
        query = """
            SELECT geographic_view.country_criterion_id,
                   geographic_view.location_type,
                   campaign.name,
                   metrics.impressions, metrics.clicks, metrics.ctr,
                   metrics.cost_micros, metrics.conversions,
                   metrics.conversions_value
            FROM geographic_view
            WHERE segments.date DURING LAST_30_DAYS
            ORDER BY metrics.cost_micros DESC
            LIMIT 200
        """

    rows = execute_query(config, customer_id, query)
    _enrich_cost_fields(rows)
    _enrich_conversion_rate(rows)

    return {"locations": rows, "total_locations": len(rows)}


def get_quality_score_details(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    campaign_id: str = "",
) -> dict:
    """Get keyword-level Quality Score with component breakdowns."""
    from adloop.ads.gaql import execute_query

    date_clause = _date_clause(date_range_start, date_range_end)

    campaign_filter = ""
    if campaign_id:
        campaign_filter = f"AND campaign.id = {campaign_id}"

    query = f"""
        SELECT campaign.name, campaign.id, ad_group.name,
               ad_group_criterion.keyword.text,
               ad_group_criterion.keyword.match_type,
               ad_group_criterion.quality_info.quality_score,
               ad_group_criterion.quality_info.creative_quality_score,
               ad_group_criterion.quality_info.post_click_quality_score,
               ad_group_criterion.quality_info.search_predicted_ctr,
               metrics.impressions, metrics.clicks, metrics.cost_micros,
               metrics.conversions
        FROM keyword_view
        WHERE ad_group_criterion.status != 'REMOVED'
          {date_clause}
          {campaign_filter}
        ORDER BY metrics.cost_micros DESC
    """

    rows = execute_query(config, customer_id, query)
    _enrich_cost_fields(rows)

    return {"quality_scores": rows, "total_keywords": len(rows)}


def get_bid_strategy_status(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    campaign_id: str = "",
) -> dict:
    """Get bid strategy type, learning status, and budget for each campaign."""
    from adloop.ads.gaql import execute_query

    campaign_filter = ""
    if campaign_id:
        campaign_filter = f"AND campaign.id = {campaign_id}"

    query = f"""
        SELECT campaign.id, campaign.name, campaign.status,
               campaign.bidding_strategy_type,
               campaign.bidding_strategy_system_status,
               campaign_budget.amount_micros,
               metrics.conversions, metrics.cost_micros
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          {campaign_filter}
          AND segments.date DURING LAST_30_DAYS
        ORDER BY metrics.cost_micros DESC
    """

    rows = execute_query(config, customer_id, query)
    _enrich_cost_fields(rows)
    _enrich_budget_fields(rows)

    return {"strategies": rows, "total_campaigns": len(rows)}


def get_budget_pacing(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    campaign_id: str = "",
) -> dict:
    """Get monthly budget pacing — spend-to-date, projected spend, pace %."""
    import calendar
    from datetime import date

    from adloop.ads.gaql import execute_query

    campaign_filter = ""
    if campaign_id:
        campaign_filter = f"AND campaign.id = {campaign_id}"

    # Query 1: budget settings
    budget_query = f"""
        SELECT campaign.id, campaign.name, campaign.status,
               campaign_budget.amount_micros
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          {campaign_filter}
    """

    # Query 2: month-to-date spend (segments.date breaks down by day; we sum)
    spend_query = f"""
        SELECT campaign.id, metrics.cost_micros
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          AND segments.date DURING THIS_MONTH
          {campaign_filter}
    """

    budget_rows = execute_query(config, customer_id, budget_query)
    spend_rows = execute_query(config, customer_id, spend_query)

    # Aggregate daily spend per campaign
    spend_by_campaign: dict[str, int] = {}
    for row in spend_rows:
        cid = row.get("campaign.id")
        cost = row.get("metrics.cost_micros", 0) or 0
        spend_by_campaign[cid] = spend_by_campaign.get(cid, 0) + cost

    today = date.today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_elapsed = today.day
    days_remaining = days_in_month - days_elapsed

    pacing = []
    for row in budget_rows:
        cid = row.get("campaign.id")
        budget_micros = row.get("campaign_budget.amount_micros", 0) or 0
        daily_budget = round(budget_micros / 1_000_000, 2)
        month_budget = round(daily_budget * days_in_month, 2)

        month_spend_micros = spend_by_campaign.get(cid, 0)
        month_spend = round(month_spend_micros / 1_000_000, 2)

        daily_avg = round(month_spend / days_elapsed, 2) if days_elapsed > 0 else 0
        projected = round(daily_avg * days_in_month, 2)
        pace_pct = round(projected / month_budget * 100, 1) if month_budget > 0 else 0

        pacing.append({
            "campaign.id": cid,
            "campaign.name": row.get("campaign.name"),
            "campaign.status": row.get("campaign.status"),
            "daily_budget": daily_budget,
            "month_budget": month_budget,
            "month_spend": month_spend,
            "daily_avg_spend": daily_avg,
            "projected_month_spend": projected,
            "days_elapsed": days_elapsed,
            "days_remaining": days_remaining,
            "pace_pct": pace_pct,
        })

    return {"pacing": pacing, "total_campaigns": len(pacing)}


def get_ad_schedule_performance(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    campaign_id: str = "",
) -> dict:
    """Get performance by hour of day and day of week."""
    from adloop.ads.gaql import execute_query

    date_clause = _date_clause(date_range_start, date_range_end)

    campaign_filter = ""
    if campaign_id:
        campaign_filter = f"AND campaign.id = {campaign_id}"

    query = f"""
        SELECT campaign.name, campaign.id,
               segments.day_of_week, segments.hour,
               metrics.impressions, metrics.clicks, metrics.ctr,
               metrics.cost_micros, metrics.conversions
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          {date_clause}
          {campaign_filter}
        ORDER BY segments.day_of_week, segments.hour
    """

    rows = execute_query(config, customer_id, query)
    _enrich_cost_fields(rows)

    return {"schedule_performance": rows, "total_rows": len(rows)}


def get_auction_insights(
    config: AdLoopConfig,
    *,
    customer_id: str = "",
    date_range_start: str = "",
    date_range_end: str = "",
    campaign_id: str = "",
) -> dict:
    """Get auction insights — competitor overlap, outranking share, position data.

    Note: only available for allowlisted accounts. Returns a helpful error
    message if the account does not have access.
    """
    from adloop.ads.gaql import execute_query

    date_clause = _date_clause(date_range_start, date_range_end)

    campaign_filter = ""
    if campaign_id:
        campaign_filter = f"AND campaign.id = {campaign_id}"

    query = f"""
        SELECT campaign.name, campaign.id,
               segments.auction_insight_domain,
               metrics.auction_insight_search_impression_share,
               metrics.auction_insight_search_overlap_rate,
               metrics.auction_insight_search_outranking_share,
               metrics.auction_insight_search_position_above_rate,
               metrics.auction_insight_search_top_impression_percentage,
               metrics.auction_insight_search_absolute_top_impression_percentage
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          {date_clause}
          {campaign_filter}
        ORDER BY metrics.auction_insight_search_impression_share DESC
    """

    try:
        rows = execute_query(config, customer_id, query)
    except Exception as exc:
        err = str(exc)
        if "QUERY_NOT_ALLOWED" in err or "not allowed" in err.lower():
            return {
                "error": "Auction insights are not available for this account.",
                "hint": (
                    "Auction insights via GAQL require an allowlisted account. "
                    "Contact your Google account manager to request access, or "
                    "view auction insights in the Google Ads UI instead."
                ),
            }
        raise

    return {"auction_insights": rows, "total_rows": len(rows)}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _date_clause(start: str, end: str) -> str:
    """Build a GAQL date WHERE fragment."""
    if start and end:
        return f"AND segments.date BETWEEN '{start}' AND '{end}'"
    return "AND segments.date DURING LAST_30_DAYS"



def _enrich_cost_fields(rows: list[dict]) -> None:
    """Add human-readable cost and CPA fields computed from cost_micros."""
    for row in rows:
        cost_micros = row.get("metrics.cost_micros", 0) or 0
        row["metrics.cost"] = round(cost_micros / 1_000_000, 2)

        conversions = row.get("metrics.conversions", 0) or 0
        if conversions > 0:
            row["metrics.cpa"] = round(cost_micros / 1_000_000 / conversions, 2)

        avg_cpc_micros = row.get("metrics.average_cpc", 0) or 0
        if avg_cpc_micros:
            row["metrics.average_cpc_eur"] = round(avg_cpc_micros / 1_000_000, 2)


def _enrich_impression_share_fields(rows: list[dict]) -> None:
    """Convert impression share fractions (0.0-1.0) to readable percentages."""
    share_fields = [
        "metrics.search_impression_share",
        "metrics.search_budget_lost_impression_share",
        "metrics.search_rank_lost_impression_share",
        "metrics.search_exact_match_impression_share",
        "metrics.search_top_impression_share",
        "metrics.search_absolute_top_impression_share",
    ]
    for row in rows:
        for field in share_fields:
            val = row.get(field)
            if val is not None and isinstance(val, (int, float)):
                row[field + "_pct"] = f"{val * 100:.1f}%"


def _enrich_conversion_rate(rows: list[dict]) -> None:
    """Compute conversion rate percentage from clicks and conversions."""
    for row in rows:
        clicks = row.get("metrics.clicks", 0) or 0
        conversions = row.get("metrics.conversions", 0) or 0
        if clicks > 0:
            row["metrics.conversion_rate"] = round(conversions / clicks * 100, 2)


def _enrich_budget_fields(rows: list[dict]) -> None:
    """Add human-readable budget amount from budget_micros."""
    for row in rows:
        budget_micros = row.get("campaign_budget.amount_micros", 0) or 0
        if budget_micros:
            row["campaign_budget.amount"] = round(budget_micros / 1_000_000, 2)
