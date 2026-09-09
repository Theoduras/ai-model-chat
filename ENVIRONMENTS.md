# Environment

One Cloud Run service, auto-deploying from `develop`.

| Git branch | Cloud Run service | Region | URL |
|---|---|---|---|
| `develop` | `ai-model-chat-dev` | `europe-west4` | https://velvetfunneler.com |

## `PUBLIC_BASE_URL`

Set as an environment variable **on the Cloud Run service** — there is
nowhere in this repository it could be set from (see Cloud Build trigger):

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

The `develop` trigger was created by **Cloud Run → Set up continuous
deployment**, so it is one of Google's managed `rmgpgab-*` triggers with an
inline build config. **There is no `cloudbuild.yaml`** — the trigger builds the
`Dockerfile` and runs `gcloud run services update ai-model-chat-dev --image=...`
and nothing more.

The practical consequence: **no Cloud Run setting can be changed from this
repository.** Memory, CPU throttling, min/max instances, session affinity and
env vars live on the service and only on the service. They survive image
deploys, so each is set once by hand:

```bash
gcloud run services update ai-model-chat-dev --region europe-west4 \
  --memory=2Gi --no-cpu-throttling
```

If the site stops picking up pushes:

```bash
gcloud builds triggers list --format="table(name, github.owner, github.name, filename)"
```

There should be exactly one trigger for `ai-model-chat-dev`. A second trigger on
this repo deploying a second service means two copies of the Fanvue, OnlyFans, X
and Telegram loops running against the same accounts — two bots answering the
same fan. Check `disabled` and `substitutions._SERVICE_NAME` on anything else
listed.

## The sign-in browser service

A creator's OnlyFans sign-in runs a real Chromium, and it lives in the memory of
whichever process opened it — a Chromium cannot be handed to another instance
halfway through. So while that browser ran inside the app, **every app deploy
ended every sign-in in flight**, mid-2FA, with "that sign-in is no longer open".

`ai-model-chat-dev-browser` is that browser on its own Cloud Run service,
running the same image with a different entrypoint (`of_browser:service()`).

**It has no trigger, deliberately.** Not being redeployed is the entire feature:
a trigger is the thing that would kill sign-ins in flight, which is what we were
fixing. It is deployed by hand, and only when the browser itself changes —
`of_connect.py`, `of_browser.py`, `Dockerfile` or `requirements.txt`. Do that at
a quiet moment; it ends any sign-in running at the time.

| Variable | Where | What it does |
|---|---|---|
| `ONLYFANS_BROWSER_URL` | app | The browser service's URL. **Unset means the browser runs inside the app**, as before — so nothing breaks until both variables are set on both services, and removing it is the rollback. |
| `ONLYFANS_BROWSER_TOKEN` | both | Shared secret. Must match, or the app cannot drive the browser. |
| `SECRET_KEY` | both | Needed at import by `of_session`. Must be the same value as the app's. |

### Creating or redeploying it

Takes the image the app is currently running, so the two never drift:

```bash
IMAGE=$(gcloud run services describe ai-model-chat-dev --region europe-west4 \
  --format='value(spec.template.spec.containers[0].image)')

gcloud run deploy ai-model-chat-dev-browser \
  --image="$IMAGE" \
  --region=europe-west4 --platform=managed \
  --allow-unauthenticated \
  --command=gunicorn \
  --args=--bind,:8080,--workers,1,--threads,8,--timeout,0,of_browser:service\(\) \
  --min-instances=1 --max-instances=1 --no-cpu-throttling \
  --session-affinity --memory=2Gi
```

One instance, always warm: the attempt store is per-process, so a second
instance is one that cannot see the sign-in the first one is running.

`--allow-unauthenticated` is safe here only because of the token — every route
but `/health` refuses a request without a matching `X-Browser-Token`.

### Wiring the app to it

Set the browser's token first. An app pointed at a browser service that has no
token yet is the one ordering worth avoiding:

```bash
TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')
SECRET=$(gcloud run services describe ai-model-chat-dev --region europe-west4 \
  --format='value(spec.template.spec.containers[0].env.filter("name:SECRET_KEY").extract("value"))')

gcloud run services update ai-model-chat-dev-browser --region europe-west4 \
  --update-env-vars "ONLYFANS_BROWSER_TOKEN=$TOKEN,SECRET_KEY=$SECRET"

BROWSER_URL=$(gcloud run services describe ai-model-chat-dev-browser \
  --region europe-west4 --format='value(status.url)')

curl -s "$BROWSER_URL/health"
```

Wanted: `{"browser":true,"guarded":true,"ok":true}`. `browser:false` means
Chromium is not starting there — **stop, and leave the app unwired**, because
the in-process browser it would replace still works. Only then:

```bash
gcloud run services update ai-model-chat-dev --region europe-west4 \
  --update-env-vars "ONLYFANS_BROWSER_TOKEN=$TOKEN,ONLYFANS_BROWSER_URL=$BROWSER_URL"
```

Confirm it end to end by starting a sign-in and pushing to `develop` while it is
open. Surviving that is the whole point; before this it was what broke it.

To roll back, remove `ONLYFANS_BROWSER_URL` from the app and the in-process
browser takes over on the next revision:

```bash
gcloud run services update ai-model-chat-dev --region europe-west4 \
  --remove-env-vars ONLYFANS_BROWSER_URL
```

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
