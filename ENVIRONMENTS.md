# Environments — Live vs Development

Two separate Cloud Run services, each auto-deploying from its own git branch.

| | Git branch | Cloud Run service | Region | URL | Audience |
|---|---|---|---|---|---|
| **LIVE** 🟢 | `deploy/cloud-run-online` | `ai-model-chat` | `europe-west4` | (your live URL) | Fans / clients |
| **DEV** 🛠️ | `develop` | `ai-model-chat-dev` | `europe-west4` | https://ai-model-chat-dev-793708886252.europe-west4.run.app | Internal testing only |

## Each service needs its own `PUBLIC_BASE_URL`

Set as an environment variable **on the Cloud Run service**, not in
`cloudbuild.yaml` — the value differs per environment, and a shared default
would point dev's callbacks at live.

```
# dev
gcloud run services update ai-model-chat-dev --region europe-west4 \
  --update-env-vars PUBLIC_BASE_URL=https://ai-model-chat-dev-793708886252.europe-west4.run.app

# live (owner only)
gcloud run services update ai-model-chat --region europe-west4 \
  --update-env-vars PUBLIC_BASE_URL=https://velvetfunneler.com
```

`--update-env-vars` merges; `--set-env-vars` would wipe every other variable
on the service, secrets included.

Without it the app builds callback and webhook URLs from whichever host served
the request. That matters most for **Fanvue webhooks**: the subscription is
created on connect for the origin it sees at that moment, so a creator who
connects through the bare `*.run.app` host has their purchases and unsubscribes
delivered there rather than to the custom domain. The app prints a
`CONFIG WARNING` at startup while it is unset, and the Fanvue page shows the
URL each subscription actually points at.

## Cloud Build triggers — the substitutions matter

Both environments build from the **same** `cloudbuild.yaml`. Which service a
push lands on is decided entirely by the trigger's substitution variables, and
the file's defaults point at **live**:

| Trigger | Branch pattern | Required substitutions |
|---|---|---|
| live | `^deploy/cloud-run-online$` | none — the defaults are correct |
| dev | `^develop$` | `_SERVICE=ai-model-chat-dev`, `_REGION=europe-west4` |

**A dev trigger that does not override `_SERVICE` deploys `develop` over the
live service.** If the dev URL stops picking up pushes, check that first:
Cloud Build → Triggers → the `develop` trigger → Substitution variables.

Creating the dev trigger from scratch:

```bash
gcloud artifacts repositories create ai-model-chat-dev \
  --repository-format=docker --location=europe-west4   # once, if missing

gcloud builds triggers create github \
  --name=ai-model-chat-dev \
  --repo-name=ai-model-chat --repo-owner=Theoduras \
  --branch-pattern="^develop$" \
  --build-config=cloudbuild.yaml \
  --substitutions=_SERVICE=ai-model-chat-dev,_REGION=europe-west4
```

Each service keeps its own image repository at
`{region}-docker.pkg.dev/{project}/{service}/app`, so a new `_SERVICE` needs a
matching Artifact Registry repo before its first build succeeds.

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
