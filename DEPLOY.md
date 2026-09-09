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
  --update-env-vars "GEMINI_API_KEY=YOUR_KEY,ADMIN_PASSWORD=YOUR_PASSWORD"
```

> **Always `--update-env-vars`, never `--set-env-vars`.** `--set-` replaces the
> service's whole environment with what you typed, so a command naming one
> variable silently drops `DATABASE_URL`, the Stripe keys and everything else —
> the service comes back up on a fresh SQLite file with billing broken.
> `--update-` adds and overwrites only the variables you name. The same applies
> to `--set-secrets` vs `--update-secrets`.

Cloud Run builds the `Dockerfile`, deploys it, and prints a live HTTPS URL like:

```
https://ai-model-chat-xxxxxxxxx.europe-west4.run.app
```

That URL works immediately — no domain required.

> **Secrets:** for production, prefer Secret Manager over env vars:
> ```bash
> echo -n "YOUR_KEY" | gcloud secrets create gemini-api-key --data-file=-
> gcloud run deploy ai-model-chat --source . --region europe-west4 \
>   --update-secrets "GEMINI_API_KEY=gemini-api-key:latest"
> ```

---

## Auto-deploy on push

Pushing to `develop` builds and deploys automatically — you never run a deploy
command by hand. It is already set up; this section is what it actually is, not
how to recreate it.

The trigger was created by **Cloud Run → Set up continuous deployment**, not by
hand, so it is one of Google's managed `rmgpgab-*` triggers. That matters:

- **It carries its own inline build config. There is no `cloudbuild.yaml`.** The
  trigger builds the `Dockerfile` and then runs `gcloud run services update
  <service> --image=...`, passing nothing else.
- **So Cloud Run flags cannot be set from the repo.** Memory, CPU throttling,
  min/max instances, session affinity, env vars — none of it is in version
  control, and a file in the repo claiming to set them would be ignored. There
  used to be a `cloudbuild.yaml` here that did exactly that, silently, for
  months; it has been deleted rather than left to mislead.
- **Service settings persist across deploys.** `services update --image` leaves
  everything else alone, so a setting applied once stays applied. That is why
  the commands below are one-time.

Inspect the trigger with:

```bash
gcloud builds triggers list --format="table(name, github.owner, github.name, filename)"
gcloud builds triggers describe <name> --format=yaml
```

An empty `FILENAME` column confirms the inline config.

### Changing a Cloud Run setting

Directly on the service, once:

```bash
gcloud run services update ai-model-chat-dev --region europe-west4 \
  --memory=2Gi --no-cpu-throttling
```

### Setting secrets

Also directly on the service, and also once:
```bash
gcloud run services update ai-model-chat-dev --region europe-west4 \
  --update-env-vars "GEMINI_API_KEY=...,ADMIN_PASSWORD=..."
```
Generate `API_KEYS` in the same command so the key never lands in your shell
history:
```bash
gcloud run services update ai-model-chat-dev --region europe-west4 \
  --update-env-vars "API_KEYS=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
```
Read it back when you need to hand it to a caller:
```bash
gcloud run services describe ai-model-chat-dev --region europe-west4 \
  --format="value(spec.template.spec.containers[0].env)"
```

Every push after that builds a new image and rolls it out; nothing else changes.
