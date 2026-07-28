# Hosting deploy runbook — Cloud Run (Phase G)

Deploys the hosted AdLoop MCP server to **Google Cloud Run**, region **`us-west1`**
(colocated with the Supabase DB in `us-west-1`). The server image is built in the
cloud by Cloud Build from the repo `Dockerfile` — **no local Docker required**.

Do **staging first** (Motivent Staging Supabase), verify, then repeat for prod
(Client Brain Supabase). Values below are for staging.

---

## 0. One-time project setup

```bash
gcloud auth login
gcloud config set project gads-mcp-490901

# APIs used by the build + deploy + secrets
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com
```

## 1. Create the secrets (Secret Manager)

Only the genuinely secret values go here; plain config is passed as env in step 3.
Create each from a local value **without echoing it into shell history** where
possible (`--data-file=-` reads stdin):

```bash
# Supabase transaction-pooler URL — MUST be the IPv4 shared-pooler string
#   postgresql://postgres.<ref>:<PASSWORD>@aws-1-us-west-1.pooler.supabase.com:6543/postgres
# (percent-encode special chars in the password)
printf '%s' 'PASTE_ADLOOP_DATABASE_URL' | gcloud secrets create adloop-database-url --data-file=-

# Google Web OAuth client secret (from client adloop-hosted-web)
printf '%s' 'PASTE_GOOGLE_CLIENT_SECRET' | gcloud secrets create adloop-google-client-secret --data-file=-

# Google Ads developer token
printf '%s' 'PASTE_ADS_DEVELOPER_TOKEN' | gcloud secrets create adloop-ads-dev-token --data-file=-
```

Grant the Cloud Run runtime service account read access to each secret (replace
`PROJECT_NUMBER`; the default runtime SA is `PROJECT_NUMBER-compute@developer.gserviceaccount.com`):

```bash
for S in adloop-database-url adloop-google-client-secret adloop-ads-dev-token; do
  gcloud secrets add-iam-policy-binding "$S" \
    --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
done
```

## 2. First deploy (to obtain the service URL)

`--allow-unauthenticated` is correct here: the server does its **own** auth at the
app layer (Supabase OAuth 2.1 + client_id pinning via `SupabaseProvider`), so
Cloud Run IAM must not also gate it — Claude reaches it with a bearer token, not a
Google identity.

`ADLOOP_TOOLSETS=ads,ga4` is baked into the image (Dockerfile), so it's not passed
here. `ADLOOP_BASE_URL` / allow-lists / `ADLOOP_EXPECTED_CLIENT_ID` are set in
step 4 once we know the URL + connector id.

```bash
gcloud run deploy adloop-hosted \
  --source . \
  --region us-west1 \
  --allow-unauthenticated \
  --set-env-vars ADLOOP_SUPABASE_URL=https://lkqinhtagvvzxhaxxsgq.supabase.co \
  --set-env-vars ADLOOP_JWT_ALGORITHM=ES256 \
  --set-env-vars ADLOOP_GOOGLE_CLIENT_ID=955371824855-h0dpakb837g2egl2ehq8haeedjjnhs5k.apps.googleusercontent.com \
  --set-env-vars ADLOOP_ADS_LOGIN_CUSTOMER_ID=4762726066 \
  --set-secrets ADLOOP_DATABASE_URL=adloop-database-url:latest \
  --set-secrets ADLOOP_GOOGLE_CLIENT_SECRET=adloop-google-client-secret:latest \
  --set-secrets ADLOOP_ADS_DEVELOPER_TOKEN=adloop-ads-dev-token:latest
```

> Note: each env var is passed as its own `--set-env-vars` flag on purpose — a
> single comma-joined list would break on any value containing a comma. If you
> ever need to override the comma-valued `ADLOOP_TOOLSETS`, use `--env-vars-file`
> (YAML) rather than `--set-env-vars`.

Grab the service URL it prints (e.g. `https://adloop-hosted-XXXX-uw.a.run.app`).

## 3. Enable Supabase OAuth Server + register the connector

Blocked until the Supabase **Owner/Admin** access lands (Auth-settings write gate):
1. Motivent Staging → `Authentication → OAuth Server` → enable + **dynamic client
   registration**.
2. Add the connector in Claude pointing at the service URL from step 2; Supabase
   dynamic registration mints a **connector `client_id`** — capture it.

## 4. Second deploy (pin base URL + connector id)

```bash
gcloud run services update adloop-hosted --region us-west1 \
  --set-env-vars ADLOOP_BASE_URL=https://adloop-hosted-XXXX-uw.a.run.app \
  --set-env-vars ADLOOP_ALLOWED_HOSTS=adloop-hosted-XXXX-uw.a.run.app \
  --set-env-vars ADLOOP_EXPECTED_CLIENT_ID=PASTE_CONNECTOR_CLIENT_ID
```

Without `ADLOOP_BASE_URL` the server logs a warning and runs **unauthenticated**
(see `install_auth`); without `ADLOOP_EXPECTED_CLIENT_ID` client-id pinning is OFF
(a token minted for another Supabase OAuth connector would verify). Both must be
set before real use.

---

## Environment variable reference

| Var | Where | Value / notes |
|---|---|---|
| `ADLOOP_SUPABASE_URL` | env | `https://lkqinhtagvvzxhaxxsgq.supabase.co` |
| `ADLOOP_BASE_URL` | env (step 4) | the Cloud Run service URL |
| `ADLOOP_JWT_ALGORITHM` | env | `ES256` |
| `ADLOOP_EXPECTED_CLIENT_ID` | env (step 4) | connector client_id to pin |
| `ADLOOP_GOOGLE_CLIENT_ID` | env | Web client `955371824855-h0dp…` |
| `ADLOOP_ADS_LOGIN_CUSTOMER_ID` | env | MCC `4762726066` |
| `ADLOOP_TOOLSETS` | image (Dockerfile) | `ads,ga4` |
| `ADLOOP_ALLOWED_HOSTS` / `_ORIGINS` | env (step 4) | Cloud Run host / origin |
| `ADLOOP_DATABASE_URL` | 🔒 secret | IPv4 shared-pooler string, port 6543 |
| `ADLOOP_GOOGLE_CLIENT_SECRET` | 🔒 secret | Web client secret |
| `ADLOOP_ADS_DEVELOPER_TOKEN` | 🔒 secret | Ads dev token |
| `ADLOOP_DB_POOL_MAX` | env (optional) | max pooled conns/instance (default 4) |
| `ADLOOP_DEV_REFRESH_TOKEN` | — | **local-dev only; never set in prod.** Phase E's per-user lookup replaces it. Set it temporarily only for a single-user staging smoke test. |

## Security

- **Rotate** the old Desktop client (`GAds MCP`, `955371824855-m4ph…`) secret /
  refresh token — its credentials were exposed in chat during testing. The hosted
  flow uses the new Web client (`adloop-hosted-web`), so the Desktop creds can be
  retired.
- All three secrets live in Secret Manager and are injected at runtime; none are
  baked into the image (`.dockerignore` excludes `.env*`).

## Cutover

Once staging is verified end-to-end, repeat steps 0–4 against the Client Brain
prod Supabase project (`zqmteiehwhbhcsubcqvr`) with prod values, then retire the
local-install setup guide.
