# Deployment — Vercel (site) + Fly.io (always-on bots)

| What | Where |
|---|---|
| Website, API, cron | **Vercel** (Hobby), entry `api/index.py`, config `vercel.json` |
| Database | **Neon Postgres** (Vercel → Storage → Neon), `DATABASE_URL` |
| Media | **Vercel Blob** (private store), `BLOB_READ_WRITE_TOKEN` |
| Fanvue / OnlyFans / X / Telegram / Discord loops | **Fly.io** app from `Dockerfile` (`fly.toml`) |
| OnlyFans/Discord/Instagram sign-in browser | **Fly.io** app from `Dockerfile.browser` (`fly.browser.toml`) |

The loops switch themselves off on Vercel (`IS_VERCEL` / `_worker_enabled`) and
on everywhere else, so the Fly bots app runs them and Vercel never does. Both
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

## 3. Fly.io (bots + sign-in browser)

Two apps, one machine each, both in `ams` (`fly.toml`, `fly.browser.toml`).

1. `fly launch --no-deploy --copy-config -c fly.toml` and the same for
   `fly.browser.toml` (keeps the app names `ai-model-chat-bots` /
   `ai-model-chat-browser`).
2. Bots: `fly secrets set -c fly.toml` with the same set as Vercel (same
   `DATABASE_URL`, `BLOB_READ_WRITE_TOKEN`, `PUBLIC_BASE_URL`), plus
   `STORAGE_BACKEND=blob`.
3. Browser: `fly secrets set -c fly.browser.toml ONLYFANS_BROWSER_TOKEN=<random>
   DATABASE_URL=...`. Its URL is `https://ai-model-chat-browser.fly.dev`.
4. On Vercel **and** the bots app set `ONLYFANS_BROWSER_URL=<that URL>` and the
   same `ONLYFANS_BROWSER_TOKEN`.
5. `fly deploy -c fly.toml` and `fly deploy -c fly.browser.toml`. Fly does not
   build on push: redeploy both after a change the loops or browser use.

## 4. Point callbacks at the domain

Already right if they use `https://velvetfunneler.com`; otherwise update:
Stripe webhook, Oxapay callback, Google OAuth redirect URI
(`/auth/google/callback`), Fanvue app redirect/webhook (reconnect Fanvue once),
Reddit/TikTok redirect URIs.

## Deploying

Push to `develop`; Vercel builds from it. Then `fly deploy` both Fly apps.
