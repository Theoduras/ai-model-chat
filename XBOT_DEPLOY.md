# X Bot — separate deployed version

This is a third standalone Cloud Run service (alongside **live** and **dev**),
dedicated to the X engagement bot. It auto-deploys from the `deploy/x-bot`
branch using the same `cloudbuild.yaml`, just with a different service name.

| Version | Branch                     | Cloud Run service     |
| ------- | -------------------------- | --------------------- |
| Live    | `deploy/cloud-run-online`  | `ai-model-chat`       |
| Dev     | `develop`                  | `ai-model-chat-dev`   |
| **X bot** | **`deploy/x-bot`**       | **`ai-model-chat-xbot`** |

## One-time setup (Console, ~5 min)

### 1. Create the Cloud Build trigger
**Cloud Build → Triggers → Create trigger**
- **Name:** `deploy-xbot`
- **Event:** Push to a branch
- **Source:** this repo
- **Branch (regex):** `^deploy/x-bot$`
- **Configuration:** Cloud Build configuration file → `/cloudbuild.yaml`
- **Substitution variables** (Add variable):
  - `_SERVICE` = `ai-model-chat-xbot`
  - `_REGION`  = `europe-west4`   *(match your live/dev region)*
- Create.

### 2. First deploy
Run the trigger once (**Run** on the trigger), or just push to `deploy/x-bot`.
The first build creates the `ai-model-chat-xbot` service automatically.

### 3. Set env vars on the new service
**Cloud Run → `ai-model-chat-xbot` → Edit & deploy new revision → Variables & Secrets.**
Add the same secrets the other services use:
- `GEMINI_API_KEY`
- `ADMIN_PASSWORD` (dashboard / `/xbot` login)
- `DATABASE_URL` — point at its **own** database (e.g. `appdb_xbot`) so it
  doesn't share state with live/dev. See `CLOUD_SQL_SETUP.md`.
- `SECRET_KEY` (any long random string)

### 4. Use it
Open `https://ai-model-chat-xbot-<projnum>.europe-west4.run.app/xbot`.
Connect each persona's X account in **Dashboard → persona → X.com** first
(the new OAuth scopes — tweet/follow — require a fresh reconnect).

## Promoting changes into this version
Develop on `feature/x-engagement-bot`, then fast-forward the deploy branch:

```
git checkout deploy/x-bot
git merge --ff-only origin/feature/x-engagement-bot
git push origin deploy/x-bot
```
