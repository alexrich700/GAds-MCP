# Privacy Policy

**Last updated:** April 2, 2026

AdLoop is an open-source, locally-run tool. It has no backend, no servers, and no telemetry. This policy exists because Google requires one for OAuth verification.

## What AdLoop Accesses

When you authorize AdLoop, you grant it permission to:

- **Read your Google Analytics (GA4) data** — property summaries, reports, realtime data, event configurations
- **Modify GA4 settings** — required by the Google Analytics Admin API scope, though AdLoop currently uses it only for read operations (listing properties)
- **Read and manage your Google Ads account** — campaigns, ads, keywords, search terms, performance metrics, and the ability to create/pause/modify campaigns and ads

These permissions are requested through Google's standard OAuth consent flow. You see exactly which permissions are requested before granting them.

## How Your Data Is Handled

- **All data stays on your machine.** AdLoop runs locally as an MCP server inside your code editor. There is no AdLoop server, cloud service, or hosted backend.
- **OAuth tokens are stored locally** at `~/.adloop/token.json` on your filesystem. They are never transmitted anywhere except back to Google's APIs to authenticate requests.
- **No data is collected, stored, or transmitted to AdLoop's developers or any third party.** The only network requests AdLoop makes are directly to Google's APIs (Analytics Data API, Analytics Admin API, Google Ads API) on your behalf.
- **No analytics or telemetry.** AdLoop does not track usage, collect crash reports, or phone home in any way.
- **Audit logs are local.** All operations are logged to `~/.adloop/audit.log` on your machine for your own review.

## Third-Party Services

AdLoop communicates exclusively with Google's APIs:

- Google Analytics Data API (`analyticsdata.googleapis.com`)
- Google Analytics Admin API (`analyticsadmin.googleapis.com`)
- Google Ads API (`googleads.googleapis.com`)

No other third-party services are contacted.

## Revoking Access

You can revoke AdLoop's access to your Google account at any time:

1. Go to [Google Account → Security → Third-party apps with account access](https://myaccount.google.com/permissions)
2. Find "AdLoop" and click **Remove Access**
3. Delete `~/.adloop/token.json` from your machine

## Open Source

AdLoop's source code is publicly available at [github.com/kLOsk/adloop](https://github.com/kLOsk/adloop). You can audit exactly what the tool does with your data.

## Contact

For privacy questions: info@daniel-klose.com
