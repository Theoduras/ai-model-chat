# Environment

One Cloud Run service, auto-deploying from `develop`.

| Git branch | Cloud Run service | Region | URL |
|---|---|---|---|
| `develop` | `ai-model-chat-dev` | `europe-west4` | https://velvetfunneler.com |

## `PUBLIC_BASE_URL`

Set as an environment variable **on the Cloud Run service**, not in
`cloudbuild.yaml`:

```
gcloud run services update ai-model-chat-dev --region europe-west4 \
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

## Running the growth layer

The social-to-subscriber layer (free-trial links, source attribution, the
content register, the win-back ladder) is off for every persona unless a slug
is named. `GROWTH_BETA_PERSONAS` is the default before anyone has saved a
roster from the dashboard:

```
gcloud run services update ai-model-chat-dev --region europe-west4 \
  --update-env-vars GROWTH_BETA_PERSONAS=lilly
```

`*` switches it on for every persona. An operator can also set the roster from
the dashboard's Growth panel, and that wins from then on — including an empty
roster, which really does mean off everywhere.

## Cloud Build trigger

Builds from `cloudbuild.yaml`. The `develop` branch trigger overrides the
file's defaults with `_SERVICE=ai-model-chat-dev`, `_REGION=europe-west4` —
without that override a push would deploy over the wrong service name. If the
site stops picking up pushes, check first: Cloud Build → Triggers → the
`develop` trigger → Substitution variables.

Creating the trigger from scratch:

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

## The sign-in browser service

A creator's OnlyFans sign-in runs a real Chromium, and it lives in the memory of
whichever process opened it — a Chromium cannot be handed to another instance
halfway through. So while that browser ran inside the app, **every app deploy
ended every sign-in in flight**, mid-2FA, with "that sign-in is no longer open".

`ai-model-chat-dev-browser` is that browser on its own service, built from the
same image with a different entrypoint (`of_browser:service()`). Shipping app
code no longer touches it. Its trigger is filtered to the files that actually
change the browser, so it is redeployed rarely and deliberately:

```bash
gcloud builds triggers create github \
  --name=ai-model-chat-browser \
  --repo-name=ai-model-chat --repo-owner=Theoduras \
  --branch-pattern="^develop$" \
  --build-config=cloudbuild.browser.yaml \
  --included-files="of_connect.py,of_browser.py,Dockerfile,requirements.txt" \
  --substitutions=_SERVICE=ai-model-chat-dev,_REGION=europe-west4
```

Then point the app at it, with a shared token both sides hold — every route but
`/health` refuses a request without it:

```bash
TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')

gcloud run services update ai-model-chat-dev-browser --region europe-west4 \
  --update-env-vars "ONLYFANS_BROWSER_TOKEN=$TOKEN"

gcloud run services update ai-model-chat-dev --region europe-west4 \
  --update-env-vars "ONLYFANS_BROWSER_TOKEN=$TOKEN,ONLYFANS_BROWSER_URL=https://<browser service URL>"
```

| Variable | Where | What it does |
|---|---|---|
| `ONLYFANS_BROWSER_URL` | app | The browser service's URL. **Unset means the browser runs inside the app**, as before — so nothing breaks until both variables are set on both services. |
| `ONLYFANS_BROWSER_TOKEN` | both | Shared secret. Must match, or the app cannot drive the browser. |

Once the app is pointed at the browser service, the app's own
`--max-instances=1` (in `cloudbuild.yaml`) can come off: it is only there
because a browser in the app's memory cannot survive a second instance.

## Password-protecting the environment

The dashboard, `/admin/*`, `/xbot`, the chat/conversation logs, and the X API
endpoints are gated whenever the service has an `ADMIN_PASSWORD` env var. **No
password set → open access.**

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

- Watch a deploy: Cloud Run → service → **Revisions**, or Cloud Build → **History**.
