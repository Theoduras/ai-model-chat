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
deployment**, so it is one of Google's managed `rmgpgab-*` triggers. The inline
build config it came with — which built with `--no-cache`, rebuilding Chrome and
every dependency on every push — has been replaced by
[`cloudbuild.app.yaml`](cloudbuild.app.yaml) in this repository. It builds the
`Dockerfile` with a layer cache and runs `gcloud run services update
ai-model-chat-dev --image=...` and nothing more.

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

**It deploys itself on every push to `develop`**, from
[`cloudbuild.browser.yaml`](cloudbuild.browser.yaml) in this repository. Unlike
the app's trigger, this one carries no inline config: the build config is the
file, so what the browser service deploys with is readable and fixable from a
checkout.

### Creating the trigger

This is the only part that is not in the repository, and it is one command:

```bash
gcloud builds triggers import --source=infra/browser-trigger.yaml --region=global
```

`infra/browser-trigger.yaml` is the trigger itself. It is imported rather than
built up from `gcloud builds triggers create github` flags, because create
re-derives the repository link and refuses with a bare
`INVALID_ARGUMENT: Request contains an invalid argument` when what it derives
disagrees with how the repository is really connected — which says nothing about
which half was wrong. The `github:` block in that file is copied verbatim from
the app's trigger, which has deployed from this repository since June, so the
link is the one already proven to work:

| | |
|---|---|
| Connection | 1st generation, Cloud Build GitHub App |
| Trigger region | `global` — not `europe-west4`, where the services are |
| Owner / repo | `Theoduras` / `ai-model-chat` — capital T, and it is case-sensitive |
| Deploy service account | the project's compute default, `793708886252-compute@…` |

Import is idempotent on the name: running it again updates this trigger instead
of adding a second one, and it never touches the app's.

To confirm what the app's trigger uses before trusting the copy:

```bash
gcloud builds triggers list --region=global --format="value(name)"
gcloud builds triggers describe TRIGGER_NAME --region=global \
  --format="value(github.owner, github.name, repositoryEventConfig.repository)"
```

A populated `github.owner`/`github.name` is 1st generation, as above. A
`projects/…/connections/…/repositories/…` resource name instead would mean the
connection had been migrated to 2nd generation, and `infra/browser-trigger.yaml`
would need its `github:` block swapped for `repositoryEventConfig:` with that
resource name, and the trigger moved to the connection's own region.

Check it is there and firing:

```bash
gcloud builds triggers list --region=global \
  --format="table(name, disabled, github.push.branch, filename)"
gcloud builds list --region=global --limit=5 \
  --format="table(id, status, source.repoSource.branchName, buildTriggerId)"
```

The browser service was briefly handed to Cloud Run continuous deployment
instead, configured on the service in the console. That put the failure this
service already had — not deploying at all — back somewhere this repository
could not see or repair; a build config here is what makes it visible.

It used to have no trigger on purpose: a deploy kills a sign-in in flight, which
was the bug being fixed when this service was split out. That protection cost
more than it saved — the service sat for days on an image older than the app,
the signing repair could not be fixed because the repair runs *here*, and a
debugging round went on rediscovering it each time. A sign-in interrupted by a
deploy is simply started again; an app whose browser is a week behind is not.

Both services therefore build from the same commit but as two separate builds,
which finish at different times. The build fingerprint in `/api/diag` says when
they disagree; a lasting disagreement means one of the two builds failed.

The app's trigger is one of Google's managed `rmgpgab-…` ones, now pointed at
`cloudbuild.app.yaml`. The browser's is an ordinary trigger named
`ai-model-chat-dev-browser` pointed at `cloudbuild.browser.yaml`. Both live in the `global` Cloud Build region, not
`europe-west4` — that is where the GitHub connection is.

To deploy the browser service by hand — a rollback, or an image the trigger did
not build — every flag matters, because a deploy that omits one resets that
setting. This is the same deploy step `cloudbuild.browser.yaml` runs, against
the app's current image rather than a fresh build:

```bash
IMAGE=$(gcloud run services describe ai-model-chat-dev --region europe-west4 \
  --format='value(spec.template.spec.containers[0].image)')

gcloud run deploy ai-model-chat-dev-browser --image="$IMAGE" \
  --region=europe-west4 --platform=managed --allow-unauthenticated \
  --command=/bin/sh --args=/app/start.sh \
  --update-env-vars="GUNICORN_TARGET=of_browser:service()" \
  --min-instances=1 --max-instances=1 --no-cpu-throttling \
  --session-affinity --memory=2Gi
```

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
  --command=/bin/sh \
  --args=/app/start.sh \
  --update-env-vars="GUNICORN_TARGET=of_browser:service()" \
  --min-instances=1 --max-instances=1 --no-cpu-throttling \
  --session-affinity --memory=2Gi
```

`start.sh` is what gives the sign-in browser its display: no `DISPLAY`, and Chrome runs
headless, which is exactly what Cloudflare's check reads and refuses. Running gunicorn
directly, as this command used to, silently drops that and sign-ins hang on the
Cloudflare screen forever.

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

## Which build is live

The app and the browser service deploy from `develop` through separate
triggers, so either can be the old one — and a sign-in window driven by an old
browser service behaves like the bug you just fixed. Both report the sign-in
relay they carry:

```bash
curl -s https://velvetfunneler.com/healthz | grep -o '"relay":"[^"]*"'
curl -s "$BROWSER_URL/health" | grep -o '"relay":"[^"]*"'
```

Two different numbers means one of the two has not deployed yet. The number is
also in the sign-in window's own header (`r3`), next to the round-trip time and
the picture quality it settled on — so a creator reporting a slow window can
say which build they were on and how slow the link actually was.

Wanted: `{"browser":true,"guarded":true,"ok":true}`. `browser:false` means
Chromium is not starting there — **stop, and leave the app unwired**, because
the in-process browser it would replace still works. Only then:

```bash
gcloud run services update ai-model-chat-dev --region europe-west4 \
  --update-env-vars "ONLYFANS_BROWSER_TOKEN=$TOKEN,ONLYFANS_BROWSER_URL=$BROWSER_URL"
```

Confirm it end to end by starting a sign-in and pushing to `develop` while it is
open. Surviving that is the whole point; before this it was what broke it.

## OnlyFans exit IP (`infra/of-egress.sh`)

Sign-ins reached OnlyFans but died within a second: Cloud Run's default egress
comes from a shared Google pool that changes per request, and OnlyFans drops a
session the moment its IP changes. `infra/of-egress.sh` fixes that for ~€9/month
by running a small Squid VM with one reserved static IP, and pointing both
services at it over Direct VPC egress.

```bash
infra/of-egress.sh create   # VM + static IP + VPC, one-time
infra/of-egress.sh wire     # attaches egress to both services, sets ONLYFANS_PROXY_TEMPLATE
infra/of-egress.sh status   # check it
```

`wire` sets `ONLYFANS_PROXY_TEMPLATE` on **both** `ai-model-chat-dev` and
`ai-model-chat-dev-browser`. It must match on both — otherwise sign-in and API
calls leave by different doors, which fails in exactly the confusing
logs-in-then-drops way this was already chased for three rounds. No app code
changes; this is infra-only and survives image deploys.

### Reddit's app (the way Reddit is connected)

Reddit is not signed in through a browser any more. It refused the hosted
window on every path — correct credentials answered "invalid username or
password", the emailed one-time link answered `UPEl3D` — while the page itself
loaded fine on a clean residential IP. So Reddit is a registered app instead,
and the connection never expires.

Set up once, for every persona:

1. Sign in on reddit.com as the account that will own the app, go to
   <https://www.reddit.com/prefs/apps> → **create another app**.
2. Type: **web app**. Redirect URI: the service URL plus
   `/reddit/oauth/callback`, exactly — Reddit compares it character for
   character. For dev that is
   `https://ai-model-chat-dev-<hash>.run.app/reddit/oauth/callback`.
3. The id is the string under the app name; the secret is next to `secret`.
4. Put them on the **app** service (not the browser one):

```bash
gcloud run services update ai-model-chat-dev --region europe-west4 \
  --update-env-vars \
  'REDDIT_CLIENT_ID=...,REDDIT_CLIENT_SECRET=...,REDDIT_REDIRECT_URI=https://.../reddit/oauth/callback,REDDIT_APP_UA=web:ai-model-chat:v1 (by /u/<owner>)'
```

`REDDIT_APP_UA` is not cosmetic: `oauth.reddit.com` rate-limits a
browser-shaped User-Agent far harder than a declared one, and the declared form
is what Reddit's API rules ask for.

Then per persona: `/reddit` → **Connect with Reddit** → approve. Done for good
— `duration=permanent` means the refresh token does not expire, and nothing
needs re-pasting.

A persona connected this way needs **no proxy**; `_rd_proxy_for` returns `''`
for her deliberately. The pools below only matter for the two fallbacks.

### Reddit, Instagram and TikTok pools

Each has its own template, read by both the sign-in browser and the REST side:
`REDDIT_PROXY_TEMPLATE`, `INSTAGRAM_PROXY_TEMPLATE`, `TIKTOK_PROXY_TEMPLATE`.
They are kept apart from OnlyFans' so turning one pool off does not move
another account onto a datacentre IP.

Reddit refuses a sign-in from Google's ranges and shows only its generic
"Server error. Try again later." banner for it, so a residential pool is not
optional there. With DataImpulse the template is:

```bash
for s in ai-model-chat-dev ai-model-chat-dev-browser; do
  gcloud run services update "$s" --region europe-west4 \
    --update-env-vars '^~^REDDIT_PROXY_TEMPLATE=http://USER__cr.{country}__sessid.{session}:PASS@gw.dataimpulse.com:823'
done
```

**Per-model credentials come first.** `REDDIT_PROXY_TEMPLATE` is one provider
account with a sticky session per persona: different exit IPs, but one
subscription, so a ban on one model's credentials reaches every model behind
it. Each persona can instead carry her own proxy, set in the Reddit console
under *Her connection* and stored encrypted beside her session. Hers wins over
the template, which stays the fallback for a deployment running one account.

Copy the exact gateway string out of the provider's dashboard and swap only the
country code for `{country}` and the session id for `{session}` — those two are
the only placeholders the app fills, per persona, so an account keeps one
sticky exit IP. An account that signs in from one country and posts from
another is an account that gets checked.

Set it on **both** services or the sign-in and the posting calls leave by
different doors.

`^~^` is gcloud's delimiter override, needed because the template contains `,`
and `;` which gcloud would otherwise read as another variable. **The delimiter
has to be a character the template does not contain** — `^@^` looks natural and
is wrong, because `@` separates the password from the host.

### Turning the proxy pool off

`ONLYFANS_PROXY_TEMPLATE` unset means no pool: accounts go out on the service's
own address. Remove it from **both** services or they leave by different doors:

```bash
for s in ai-model-chat-dev ai-model-chat-dev-browser; do
  gcloud run services update "$s" --region europe-west4 \
    --remove-env-vars ONLYFANS_PROXY_TEMPLATE
done
```

Sessions store the proxy they were created on, so `of_client._opener` ignores a
stored address whenever no template is configured — otherwise removing the var
would leave existing accounts still dialling a gateway nobody pays for.

What this costs: Cloud Run's default egress comes from a shared Google pool
whose address changes per request, which is what made sign-ins log in and die a
second later. If that returns, the fix is a stable exit IP rather than a
residential pool — `infra/of-egress.sh create && infra/of-egress.sh wire` runs
one small Squid VM on a reserved static IP for about €9/month.

To roll back the browser service, remove `ONLYFANS_BROWSER_URL` from the app and
the in-process browser takes over on the next revision:

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

## Reading a running instance without a browser

Debugging the OnlyFans plumbing has meant a person copying console text into a
chat, one question per round, while the answer sat in a process nobody could
query. `GET /api/diag` is that process answering for itself.

```bash
curl -s -H "X-Diag-Key: $DIAG_KEY" https://velvetfunneler.com/api/diag | jq
```

It returns the build fingerprint and Cloud Run revision, the persistence
warnings, and — on the direct transport — the whole Live watcher payload:
signing rules and whether they reproduce OnlyFans' own signature, the browser
service's build, each account's session and watcher, the open sign-in's state,
and the recent log ring from both services.

It is read-only, and it does not exist until `DIAG_KEY` is set on the service
(`gcloud run services update ai-model-chat-dev --region europe-west4
--update-env-vars "DIAG_KEY=$(openssl rand -hex 24)"`). A key shorter than 16
characters is treated as unset, and a wrong key gets the same 404 as no key, so
the endpoint never announces itself. Sessions come through
`of_session.public()`, which strips the cookie, the token and the user agent
before anything is serialised. Rotate the key by setting a new one.
