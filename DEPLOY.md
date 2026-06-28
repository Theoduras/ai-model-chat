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
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "GEMINI_API_KEY=YOUR_KEY,ADMIN_PASSWORD=YOUR_PASSWORD"
```

Cloud Run builds the `Dockerfile`, deploys it, and prints a live HTTPS URL like:

```
https://ai-model-chat-xxxxx-uc.a.run.app
```

That URL works immediately — no domain required.

> **Secrets:** for production, prefer Secret Manager over `--set-env-vars`:
> ```bash
> echo -n "YOUR_KEY" | gcloud secrets create gemini-api-key --data-file=-
> gcloud run deploy ai-model-chat --source . --region us-central1 \
>   --update-secrets "GEMINI_API_KEY=gemini-api-key:latest"
> ```

---

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
     --region us-central1
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
gcloud run deploy ai-model-chat --source . --region us-central1 \
  --add-cloudsql-instances YOUR_PROJECT:us-central1:YOUR_INSTANCE \
  --set-env-vars "DATABASE_URL=postgresql+psycopg2://USER:PASS@/DBNAME?host=/cloudsql/YOUR_PROJECT:us-central1:YOUR_INSTANCE"
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
