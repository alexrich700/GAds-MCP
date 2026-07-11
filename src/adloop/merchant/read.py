"""Merchant Center read tools — account discovery + feed health.

Read-only use of the Content API (Google offers no read-only scope for
it). This is the minimal slice of the Merchant Center integration: which
accounts exist, and whether products are actually serving — disapproved
feed items are the most common silent killer of Shopping/PMax campaigns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adloop.config import AdLoopConfig


def list_merchant_accounts(config: AdLoopConfig) -> dict:
    """List Merchant Center accounts the connected Google user can access."""
    from adloop.merchant.client import get_merchant_client

    client = get_merchant_client(config)
    info = client.accounts().authinfo().execute()

    accounts = []
    for ident in info.get("accountIdentifiers", []):
        if ident.get("merchantId"):
            accounts.append({
                "merchant_id": ident["merchantId"],
                "type": "standalone",
            })
        elif ident.get("aggregatorId"):
            accounts.append({
                "merchant_id": ident["aggregatorId"],
                "type": "aggregator (MCA)",
            })

    insights = []
    if not accounts:
        insights.append(
            "No Merchant Center accounts are accessible with this Google "
            "account. If you expected one, check user access in Merchant "
            "Center under Settings → People and access."
        )
    return {"accounts": accounts, "total": len(accounts), "insights": insights}


def get_merchant_feed_health(
    config: AdLoopConfig,
    *,
    merchant_id: str = "",
    account_id: str = "",
) -> dict:
    """Feed health for a Merchant Center account — disapprovals + issues.

    account_id defaults to merchant_id (standalone accounts). For
    sub-accounts under an MCA, pass the MCA as merchant_id and the
    sub-account as account_id.
    """
    from adloop.merchant.client import get_merchant_client

    merchant_id = str(merchant_id or "").strip()
    if not merchant_id.isdigit():
        return {
            "error": "merchant_id must be a numeric Merchant Center ID — "
                     "call list_merchant_accounts first."
        }
    account_id = str(account_id or "").strip() or merchant_id
    if not account_id.isdigit():
        return {"error": "account_id must be a numeric Merchant Center ID."}

    client = get_merchant_client(config)
    status = (
        client.accountstatuses()
        .get(merchantId=merchant_id, accountId=account_id)
        .execute()
    )

    account_issues = [
        {
            "title": issue.get("title", ""),
            "severity": issue.get("severity", ""),
            "country": issue.get("country", ""),
            "documentation": issue.get("documentation", ""),
        }
        for issue in status.get("accountLevelIssues", [])
    ]

    destinations = []
    top_item_issues: list[dict] = []
    totals = {"active": 0, "pending": 0, "disapproved": 0, "expiring": 0}
    for product in status.get("products", []):
        stats = product.get("statistics", {}) or {}
        entry = {
            "destination": product.get("destination", ""),
            "channel": product.get("channel", ""),
            "country": product.get("country", ""),
            "active": int(stats.get("active", 0)),
            "pending": int(stats.get("pending", 0)),
            "disapproved": int(stats.get("disapproved", 0)),
            "expiring": int(stats.get("expiring", 0)),
        }
        destinations.append(entry)
        for key in totals:
            totals[key] += entry[key]
        for issue in product.get("itemLevelIssues", []):
            top_item_issues.append({
                "destination": product.get("destination", ""),
                "code": issue.get("code", ""),
                "description": issue.get("description", ""),
                "severity": issue.get("servability", "")
                or issue.get("severity", ""),
                "affected_items": int(issue.get("numItems", 0)),
                "resolution": issue.get("resolution", ""),
                "documentation": issue.get("documentation", ""),
            })
    top_item_issues.sort(key=lambda i: i["affected_items"], reverse=True)

    insights = []
    serving_total = totals["active"] + totals["pending"] + totals["disapproved"]
    if serving_total and totals["disapproved"] > 0:
        pct = round(totals["disapproved"] / serving_total * 100, 1)
        insights.append(
            f"{totals['disapproved']:,} product(s) ({pct}%) are DISAPPROVED "
            f"and not serving — this directly starves Shopping and "
            f"Performance Max campaigns. Fix the top issues below before "
            f"touching bids or budgets."
        )
    error_issues = [i for i in account_issues if i["severity"] == "error"]
    if error_issues:
        insights.append(
            f"{len(error_issues)} account-level error(s) — these can "
            f"suspend the whole account: "
            + "; ".join(i["title"] for i in error_issues[:3])
        )
    if not account_issues and not totals["disapproved"]:
        insights.append("No disapprovals or account issues — feed is healthy.")

    return {
        "merchant_id": merchant_id,
        "account_id": account_id,
        "totals": totals,
        "by_destination": destinations,
        "account_issues": account_issues,
        "top_item_issues": top_item_issues[:15],
        "insights": insights,
    }
