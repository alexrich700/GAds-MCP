"""Merchant Center read tools — account discovery + feed health.

Read-only use of the Merchant API (Google offers no read-only scope for
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
    from adloop.merchant.client import merchant_get

    accounts: list[dict] = []
    page_token = ""
    while True:
        params = {"pageToken": page_token} if page_token else {}
        payload = merchant_get(config, "accounts", params)
        for account in payload.get("accounts", []):
            name = account.get("name", "")
            accounts.append({
                "account_id": account.get("accountId")
                or name.removeprefix("accounts/"),
                "name": account.get("accountName", ""),
                "test_account": bool(account.get("testAccount", False)),
            })
        page_token = payload.get("nextPageToken") or ""
        if not page_token:
            break

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
    account_id: str = "",
) -> dict:
    """Feed health for a Merchant Center account — disapprovals + issues.

    Combines aggregateProductStatuses (per reporting-context counts of
    approved/pending/disapproved products plus the product issues behind
    them) with account-level issues that can suspend the whole account.
    """
    from adloop.merchant.client import merchant_get

    account_id = str(account_id or "").strip()
    if not account_id.isdigit():
        return {
            "error": "account_id must be a numeric Merchant Center ID — "
                     "call list_merchant_accounts first."
        }

    statuses = merchant_get(
        config, f"accounts/{account_id}/aggregateProductStatuses"
    )
    issues_payload = merchant_get(config, f"accounts/{account_id}/issues")

    account_issues = [
        {
            "title": issue.get("title", ""),
            "severity": issue.get("severity", ""),
            "detail": issue.get("detail", ""),
            "reporting_contexts": sorted({
                dest.get("reportingContext", "")
                for dest in issue.get("impactedDestinations", [])
                if dest.get("reportingContext")
            }),
            "documentation": issue.get("documentationUri", ""),
        }
        for issue in issues_payload.get("accountIssues", [])
    ]

    contexts = []
    top_item_issues: list[dict] = []
    totals = {"approved": 0, "pending": 0, "disapproved": 0}
    for status in statuses.get("aggregateProductStatuses", []):
        stats = status.get("statistics", {}) or {}
        entry = {
            "reporting_context": status.get("reportingContext", ""),
            "country": status.get("countryCode", ""),
            "approved": int(stats.get("approvedCount", 0) or 0),
            "pending": int(stats.get("pendingCount", 0) or 0),
            "disapproved": int(stats.get("disapprovedCount", 0) or 0),
        }
        contexts.append(entry)
        totals["approved"] += entry["approved"]
        totals["pending"] += entry["pending"]
        totals["disapproved"] += entry["disapproved"]
        for issue in status.get("issues", []):
            top_item_issues.append({
                "reporting_context": status.get("reportingContext", ""),
                "issue": issue.get("issueType", "")
                or issue.get("title", ""),
                "severity": issue.get("severity", ""),
                "affected_products": int(issue.get("numProducts", 0) or 0),
                "documentation": issue.get("documentationUri", ""),
            })
    top_item_issues.sort(key=lambda i: i["affected_products"], reverse=True)

    insights = []
    serving_total = sum(totals.values())
    if serving_total and totals["disapproved"] > 0:
        pct = round(totals["disapproved"] / serving_total * 100, 1)
        insights.append(
            f"{totals['disapproved']:,} product(s) ({pct}%) are DISAPPROVED "
            f"and not serving — this directly starves Shopping and "
            f"Performance Max campaigns. Fix the top issues below before "
            f"touching bids or budgets."
        )
    critical = [
        i for i in account_issues if i["severity"] in ("CRITICAL", "ERROR")
    ]
    if critical:
        insights.append(
            f"{len(critical)} account-level issue(s) at ERROR/CRITICAL "
            f"severity — CRITICAL means offers stop serving entirely: "
            + "; ".join(i["title"] for i in critical[:3])
        )
    if not account_issues and not totals["disapproved"]:
        insights.append("No disapprovals or account issues — feed is healthy.")

    return {
        "account_id": account_id,
        "totals": totals,
        "by_reporting_context": contexts,
        "account_issues": account_issues,
        "top_item_issues": top_item_issues[:15],
        "insights": insights,
    }
