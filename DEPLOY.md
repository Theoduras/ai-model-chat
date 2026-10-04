# Deployment — Vercel (site) + Railway (always-on bots)

| What | Where |
|---|---|
| Website, API, cron | **Vercel** (Hobby), entry `api/index.py`, config `vercel.json` |
| Database | **Neon Postgres** (Vercel → Storage → Neon), `DATABASE_URL` |
| Media | **Vercel Blob** (private store), `BLOB_READ_WRITE_TOKEN` |
| Fanvue / OnlyFans / X / Telegram / Discord loops | **Railway** service from `Dockerfile` (`railway.json`) |
| OnlyFans/Discord/Instagram sign-in browser | **Railway** service from `Dockerfile.browser` (`railway.browser.json`) |

The loops switch themselves off on Vercel (`IS_VERCEL` / `_worker_enabled`) and
on everywhere else, so the Railway service runs them and Vercel never does. Both
hosts share the one database and Blob store.

---

## 1. Vercel

1. vercel.com → **Add New → Project** → import `Theoduras/ai-model-chat`.
   Framework preset: **Other**. Settings → Git → **Production Branch: `develop`**.
2. **Storage → Create → Neon (Postgres)** → connect to the project. This sets
   `DATABASE_URL` (the app normalises `postgres://` itself).
3. **Storage → Create → Blob** → access **Private** → connect. This sets
   `BLOB_READ_WRITE_TOKEN`.
4. **Settings → Environment Variables**: copy every variable the Cloud Run
   service had (`GEMINI_API_KEY`, `ADMIN_PASSWORD`, `SECRET_KEY`, Stripe,
   Oxapay, Runware, ModelsLab, Fanvue, Google OAuth, SMTP, `CRON_SECRET`, …)
   **except** `GCS_BUCKET` and the `DB_*` / `CLOUD_SQL_*` ones. Add
   `PUBLIC_BASE_URL=https://velvetfunneler.com`.
   Add `RUNPOD_API_KEY` (runpod.io → Settings → API Keys) to offer Wan 2.2
   for explicit Animate; without it, Animate keeps Wan 2.7.
5. **Settings → Domains** → add `velvetfunneler.com` and the bio domain
   (`velvt.online`) and set the DNS records Vercel shows at your registrar.
6. Redeploy once so the new variables load.

Hobby limits: 60 s per request, the `/api/generate/tick` cron runs once a day,
4.5 MB request bodies, Blob 1 GB / Neon 0.5 GB on the free tiers.

## 2. Copy the data off Google Cloud (once)

1. Turn GCP billing back on. The `develop` trigger deploys this code to Cloud Run.
2. Sign in as super admin on the **old** site and open `/admin/migrate`.
3. Paste Neon's `DATABASE_URL` and the `BLOB_READ_WRITE_TOKEN` (both under
   Vercel → Storage → the store → `.env.local`) and press **Start**.
4. Wait for `"done": true`. If it stops or shows failures, press Start again —
   it skips what is already copied.
5. Check the new site, then turn GCP billing off.

## 3. Railway (bots + sign-in browser)

1. railway.com → **New Project → Deploy from GitHub repo** → this repo, branch
   `develop`. Settings → **Config-as-code path: `railway.json`**.
2. Variables: the same set as Vercel (same `DATABASE_URL`, `BLOB_READ_WRITE_TOKEN`,
   `PUBLIC_BASE_URL`), plus `STORAGE_BACKEND=blob`.
3. Add a second service from the same repo with config path
   `railway.browser.json` and variables `GUNICORN_TARGET=of_browser:service()`,
   `ONLYFANS_BROWSER_TOKEN=<random>` and the same `DATABASE_URL`. Generate a
   public domain for it.
4. On Vercel **and** the bot service set `ONLYFANS_BROWSER_URL=<that domain>` and
   the same `ONLYFANS_BROWSER_TOKEN`.

## 4. Point callbacks at the domain

Already right if they use `https://velvetfunneler.com`; otherwise update:
Stripe webhook, Oxapay callback, Google OAuth redirect URI
(`/auth/google/callback`), Fanvue app redirect/webhook (reconnect Fanvue once),
Reddit/TikTok redirect URIs.

## Deploying

Push to `develop`. Vercel and Railway both build from it.
