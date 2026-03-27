---
description: Analyze Google Ads + GA4 performance with cross-channel insights
allowed-tools: ["mcp"]
---

Analyze Google Ads and GA4 performance: $ARGUMENTS

## 1. Pull data (AdLoop MCP)

- `get_campaign_performance` — relevant date range (default: last 30 days)
- `get_impression_share` — visibility and lost opportunity analysis
- `get_bid_strategy_status` — check learning status and strategy health
- `analyze_campaign_conversions` — cross-referenced Ads + GA4 data with GDPR gap detection
- If specific campaigns mentioned, filter by name
- If keywords are relevant, also pull `get_keyword_performance` and `get_search_terms`
- If budget concerns, pull `get_budget_pacing` for month-to-date pacing

## 2. Analyze

- Spend, Clicks, Conversions, CPA, CTR per campaign
- Impression share: search IS, budget-lost IS, rank-lost IS — identify visibility gaps
- Bid strategy status: any campaigns in learning phase? Appropriate strategy type?
- Paid vs organic comparison (from non_paid_channels)
- GDPR gap (clicks vs sessions ratio — 2:1 to 5:1 is normal in EU)
- Flag: zero conversions with significant spend, CPA > 3x target, QS < 5, wasteful search terms, high budget-lost IS

If conversion issues found: run `attribution_check`
If landing page problems suspected: run `landing_page_analysis`
If quality scores are low: run `get_quality_score_details` for component breakdowns
If device performance varies: run `get_device_performance` to compare mobile vs desktop

## 3. Present results

- Summary table of all campaigns with key metrics
- Highlight what's working and what's not
- Ranked list of recommended actions with priority and estimated impact
- If search terms show waste, quantify the amount and suggest negatives

Keep the GDPR consent gap in mind — never diagnose clicks > sessions as broken tracking without considering consent rejection first.
