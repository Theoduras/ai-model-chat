# Deployment Guide — Google Cloud Run

This app runs as a Docker container on **Google Cloud Run**: a managed host that
builds the image, serves it over HTTPS, and auto-scales from zero to thousands of
concurrent chats with no servers to manage.

---

## One-time setup

1. **Create a Google Cloud project** (or reuse the existing Gemini one) at
   https://console.cloud.google.com — note the **Project ID**.
2. **Install the gcloud CLI**: https://cloud.google.com/sdk/docs/install
3. Authenticate and select the project:
   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   gcloud services enable run.googleapis.com cloudbuild.googleapis.com
   ```

---

## Deploy

From the repository root:

```bash
gcloud run deploy ai-model-chat \
  --source . \
  --region europe-west4 \
  --allow-unauthenticated \
  --set-env-vars "GEMINI_API_KEY=YOUR_KEY,ADMIN_PASSWORD=YOUR_PASSWORD"
```

Cloud Run builds the `Dockerfile`, deploys it, and prints a live HTTPS URL like:

```
https://ai-model-chat-xxxxxxxxx.europe-west4.run.app
```

That URL works immediately — no domain required.

> **Secrets:** for production, prefer Secret Manager over `--set-env-vars`:
> ```bash
> echo -n "YOUR_KEY" | gcloud secrets create gemini-api-key --data-file=-
> gcloud run deploy ai-model-chat --source . --region europe-west4 \
>   --update-secrets "GEMINI_API_KEY=gemini-api-key:latest"
> ```

---

## Auto-deploy on push (recommended)

`cloudbuild.yaml` builds and deploys automatically whenever you push, so you
never run a deploy command by hand. One-time setup:

1. Enable the APIs and create an image repository:
   ```bash
   gcloud services enable cloudbuild.googleapis.com run.googleapis.com artifactregistry.googleapis.com
   gcloud artifacts repositories create ai-model-chat \
     --repository-format=docker --location=europe-west4
   ```
2. Connect the GitHub repo and create the trigger (Console is easiest):
   **Cloud Build → Triggers → Create trigger** → connect `Theoduras/ai-model-chat`,
   pick the branch, and select **cloudbuild.yaml** as the config.
   Or via CLI:
   ```bash
   gcloud builds triggers create github \
     --repo-name=ai-model-chat --repo-owner=Theoduras \
     --branch-pattern="^deploy/cloud-run-online$" \
     --build-config=cloudbuild.yaml
   ```
   This is the **live** trigger, so it needs no substitutions —
   `cloudbuild.yaml` already defaults to `ai-model-chat` in `europe-west4`.
   A trigger for any other environment must override `_SERVICE` (and
   `_REGION` if it differs), or it deploys over live. See
   [ENVIRONMENTS.md](ENVIRONMENTS.md).
3. Set secrets once on the Cloud Run service (they persist across deploys):
   ```bash
   gcloud run services update ai-model-chat --region europe-west4 \
     --set-env-vars "GEMINI_API_KEY=...,API_KEYS=...,ADMIN_PASSWORD=..."
   ```

After that, every push builds a new image and rolls it out automatically.

## Run locally (same container as production)

```bash
docker build -t ai-model-chat .
docker run -p 8080:8080 --env-file .env ai-model-chat
# open http://localhost:8080
```

---

## Custom domain

Cloud Run gives you a free `*.run.app` URL, so a domain is optional. To use your
own (e.g. `chat.yourbrand.com`):

1. Buy a domain (any registrar — Cloud Domains, Namecheap, Google Domains, etc.).
2. Map it:
   ```bash
   gcloud run domain-mappings create \
     --service ai-model-chat \
     --domain chat.yourbrand.com \
     --region europe-west4
   ```
3. Add the DNS records gcloud prints to your registrar. HTTPS is automatic.

---

## Database (conversation memory)

Conversations are stored in a database so an external app can drive a client's
chat without holding the history itself.

- **Local dev:** defaults to a SQLite file (`data.db`) — zero setup.
- **Production:** set `DATABASE_URL` to a Postgres connection string (e.g. Cloud
  SQL). Same code, no changes.

```bash
# Create a Cloud SQL Postgres instance, then deploy with:
gcloud run deploy ai-model-chat --source . --region europe-west4 \
  --add-cloudsql-instances YOUR_PROJECT:europe-west4:YOUR_INSTANCE \
  --set-env-vars "DATABASE_URL=postgresql+psycopg2://USER:PASS@/DBNAME?host=/cloudsql/YOUR_PROJECT:europe-west4:YOUR_INSTANCE"
```

Tables are created automatically on startup.

## Programmatic API (for your app → client chats)

`POST /api/v1/chat` — drives a conversation with server-side memory.

**Auth:** set `API_KEYS` (comma-separated) as an env var. Callers pass it as
`Authorization: Bearer <key>` or `X-API-Key: <key>`. If unset, the endpoint is
open (dev only — always set keys in production).

Request:
```json
{
  "message": "hey, what's new?",
  "persona": "lilith",
  "conversation_id": "optional — omit to start a new one",
  "client_id": "optional — your end-user's id"
}
```

Response:
```json
{
  "reply": "...",
  "conversation_id": "abc123",
  "persona": "lilith"
}
```

Store the returned `conversation_id` and send it back on the next call to
continue the same conversation.

## Health check

Cloud Run pings `GET /healthz`, which returns `{"status":"ok"}`.

---

## Scaling notes

- Default: scales to zero when idle (near-zero cost), scales up on traffic.
- The container runs `gunicorn` with 1 worker / 8 threads. Raise concurrency by
  editing the `CMD` in the `Dockerfile` (`--workers`) or via
  `gcloud run services update --max-instances`.
- **Persistence:** the container filesystem is ephemeral. Persona/chat writes
  currently go to local files or `/tmp` — moving them to a managed database
  (Cloud SQL) is the next step before running multiple instances in production.

---

## Deploying to Vercel

`vercel.json` routes every request to `api/index.py`, which imports the Flask
app from the repository root. `includeFiles` ships the HTML pages, `css/`,
`js/`, `personas/` and `templates/` into the function bundle — without it the
function only gets `api/`, and every page 404s.

```bash
vercel --prod
```

### Required environment variables

Set these in **Project → Settings → Environment Variables**:

| Variable | Why |
|---|---|
| `GEMINI_API_KEY` | AI replies; without it the app serves canned fallbacks. |
| `DATABASE_URL` | Postgres connection string. The SQLite default lives in `/tmp`, which Vercel wipes on every cold start — accounts and payments would vanish. |
| `SECRET_KEY` | Signs session cookies. Without it each instance generates its own and sign-ins break across instances. |

### Background workers

The Fanvue, X and Telegram poll loops do not start on Vercel: a serverless
invocation is frozen once the response is sent, so a loop would stall mid-round
and restart on every cold start. Run those channels on Cloud Run (see above), or
force them on with `FANVUE_WORKER=1`, `X_WORKER=1`, `TELEGRAM_WORKER=1`,
`TGUSER_AUTOSTART=1` if you understand the trade-off.

### Limits

`maxDuration` is 60s — the ceiling on Hobby plans. A long Gemini call that
exceeds it returns a 504.

## Sign in with Google (Google Cloud Console)

Creators can sign up and sign in with their Google account. Payment is still
handled by Oxapay — Google supplies the identity only.

### Create the OAuth client

1. <https://console.cloud.google.com> → pick the project (the same one as
   `GOOGLE_CLOUD_PROJECT`, `793708886252` by default).
2. **APIs & Services → OAuth consent screen**: External, fill in app name,
   support email and developer email. Scopes: `openid`, `email`, `profile`.
   While the app is in *Testing* only listed test users can sign in — hit
   **Publish app** to open it to everyone.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID →
   Web application**.
4. Authorised redirect URIs — add one line per deployment, exactly:
   - `https://your-domain.com/auth/google/callback`
   - `http://localhost:5000/auth/google/callback` (local testing)
5. Copy the client ID and client secret.

### Environment variables

| Variable | Why |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | Web client ID. The Google button stays hidden until this and the secret are both set. |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Web client secret. |
| `GOOGLE_OAUTH_REDIRECT_URI` | Optional. Only needed when the app sits behind a proxy that rewrites the host, so the callback URL it builds no longer matches what Google has registered. |

A `redirect_uri_mismatch` error from Google means the URI in step 4 differs
from what the app sent — scheme, host and trailing path must match character
for character.

## SEO and Google Ads (SEA)

Neither lives in Google Cloud Console. SEO is page markup plus
[Google Search Console](https://search.google.com/search-console); SEA is
[Google Ads](https://ads.google.com). Cloud Console is only involved if the
Search Console or Google Ads *APIs* are ever wired up for reporting.

### What the app now serves

| Route | Purpose |
|---|---|
| `/robots.txt` | Allows the marketing pages, blocks `/dashboard`, `/api/` and the operator consoles. Points at the sitemap. |
| `/sitemap.xml` | `/`, `/pricing`, `/register`, `/login`. Add new public pages to `_PUBLIC_PAGES` in `app.py`. |
| `/google<token>.html` | Search Console HTML-file verification, served from env. |
| `/js/analytics.js` | Loads gtag.js for GA4 and/or Google Ads. Emits nothing until an ID is set. |

The homepage carries a canonical URL, Open Graph and Twitter cards, and
`SoftwareApplication` JSON-LD.

### Keeping fan pages out of search

`/landing`, `/profile` and the fan chat carry `<meta name="robots"
content="noindex,nofollow">` plus an `X-Robots-Tag` response header, so a
creator's funnel never surfaces in search results.

They are deliberately **not** in the `robots.txt` disallow list. Disallow blocks
crawling, and a page Google cannot crawl is a page whose `noindex` Google never
reads — a URL already in the index would stay there indefinitely. Letting the
crawler in is what gets these pages dropped.

To clear anything already indexed: Search Console → **Removals** → **Temporary
Removals** tab → **New Request** → **Remove all URLs with this prefix**, one
request per fan path. Google's removals are temporary — roughly six months —
and only hide a URL from results without stopping the crawl. The `noindex`
tags are what makes it permanent, once Google recrawls during that window.

### Environments

Only the live service should be in Search Console. The dev service at
`ai-model-chat-dev-...run.app` serves the same pages, so an indexed copy
competes with the real site for its own keywords. Set `SEO_NOINDEX_ALL=1`
there.

`run.app` is on the [Public Suffix List](https://publicsuffix.org/list/), so a
Search Console **Domain** property is impossible for a `*.run.app` host —
DNS verification would require control of `run.app` itself. Only a URL-prefix
property with HTML-file or meta-tag verification works. That is one more reason
to map a real domain before doing any SEO work.

### Environment variables

| Variable | Why |
|---|---|
| `SITE_URL` | Canonical origin, e.g. `https://velvetfunnel.app`. Without it the canonical follows whatever host answered, so a `run.app` or preview URL competes with the real domain in search. |
| `SEO_NOINDEX_ALL` | Set to `1` on every non-production service. `robots.txt` becomes `Disallow: /`, the sitemap 404s and every response carries `X-Robots-Tag: noindex`. **Set this on `ai-model-chat-dev`.** |
| `GOOGLE_SITE_VERIFICATION` | The `googleXXXX.html` filename Search Console hands out (with or without the extension). |
| `GA_MEASUREMENT_ID` | GA4 measurement ID, `G-XXXXXXX`. |
| `GOOGLE_ADS_ID` | Google Ads conversion ID, `AW-XXXXXXXXX`. |
| `GOOGLE_ADS_SIGNUP_LABEL` | Conversion label for the signup action. Google gives it as `AW-123/AbC-D_efG`; use only the part after the slash. |

### Search Console

1. Add the property (Domain type if you control DNS, otherwise URL prefix).
2. Verify: set `GOOGLE_SITE_VERIFICATION`, redeploy, then click Verify.
3. **Sitemaps** → submit `sitemap.xml`.
4. **URL Inspection** → request indexing for `/` and `/pricing`.

### Google Ads

1. Create the account, then **Tools → Conversions → New conversion action →
   Website**, event name `signup`.
2. Take the conversion ID and label into `GOOGLE_ADS_ID` and
   `GOOGLE_ADS_SIGNUP_LABEL`. The tag fires on `/billing?signup=1`, which is
   where a newly registered account lands — once per signup.
3. Verify with the Google Tag Assistant before spending anything.

**Policy warning.** Google Ads prohibits ads for sexually explicit content and
restricts adult themes. Ad copy and every landing page an ad points at must
stay non-explicit — target the creator-tool angle (persona automation, chat
funnels) and never send Ads traffic to `/landing` or a fan chat page. Those
pages are already blocked from crawling in `robots.txt`. An account suspension
for adult content is difficult to reverse, so read the Google Ads adult content
policy before the first campaign.
