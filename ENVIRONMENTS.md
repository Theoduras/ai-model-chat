# Environments — Live vs Development

Two separate Cloud Run services, each auto-deploying from its own git branch.

| | Git branch | Cloud Run service | URL | Audience |
|---|---|---|---|---|
| **LIVE** 🟢 | `deploy/cloud-run-online` | `ai-model-chat` | (your europe-west4 URL) | Fans / clients |
| **DEV** 🛠️ | `develop` | `ai-model-chat-dev` | (the dev URL) | Internal testing only |

## How changes flow

```
make change ──> push to `develop` ──> DEV site updates ──> test it
                                                              │
                                              happy? merge to live branch
                                                              │
                                                              v
                                  push to `deploy/cloud-run-online` ──> LIVE site updates
```

- Pushing to `develop` **never** affects the live site.
- The live site only changes when code reaches `deploy/cloud-run-online`.
- Each service keeps its own env vars/secrets; set them once per service.

## Promote dev → live (when a change is approved)

```bash
git checkout deploy/cloud-run-online
git merge develop
git push            # triggers the live deploy
```

## Password-protecting an environment

The dashboard, `/admin/*`, `/xbot`, the chat/conversation logs, and the X API
endpoints are gated whenever the service has an `ADMIN_PASSWORD` env var. **No
password set → open access**, so set it on *every* environment you want locked
(dev is easy to forget):

```bash
gcloud run services update ai-model-chat-dev \
  --region=europe-west4 \
  --update-env-vars ADMIN_PASSWORD='your-password'
```

Or in the Console: Cloud Run → service → **Edit & deploy new revision** →
**Variables & Secrets** → add `ADMIN_PASSWORD`. Updating the var creates a new
revision on its own — no code redeploy needed.

The login form ships a fixed `admin` username + a `current-password` field, so
your browser's password manager will offer to **save and autofill** it (per
browser, i.e. only on your own PC). Prefer not to keep the value in plaintext
env? Store it in Secret Manager and use
`--update-secrets ADMIN_PASSWORD=admin-password:latest` instead.

## Tips

- Give the dev service a **separate** `ADMIN_PASSWORD` if you want, so dev and
  live dashboards are isolated.
- You can use the same `GEMINI_API_KEY` on both, or a separate key on dev to
  track usage independently.
- Watch a deploy: Cloud Run → service → **Revisions**, or Cloud Build → **History**.
