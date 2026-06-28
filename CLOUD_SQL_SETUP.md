# Attach a durable database (Cloud SQL Postgres) — no terminal

By default the app uses a built-in SQLite file that is **wiped on every
redeploy**. Connecting Cloud SQL (managed Postgres) makes saved personas,
copies, and conversations **survive refreshes AND new deploys**.

You do this once per service (live + dev). ~10–15 minutes in the browser.

---

## 1. Create one Postgres instance (shared by both services)

1. Top search bar → **SQL** → open it → **Create instance** → **PostgreSQL**.
2. Settings:
   - **Instance ID:** `ai-model-chat-db`
   - **Password:** set one and **save it somewhere** — you'll need it below.
   - **Region:** `europe-west4` (same as your services).
   - **Edition / size:** the smallest (Sandbox / Enterprise "lightweight") is
     fine to start — cheapest tier.
3. Click **Create instance** (takes a few minutes to provision).

---

## 2. Create two databases inside it

Open the instance → **Databases** tab → **Create database**, twice:

- `appdb`      ← used by the **live** service
- `appdb_dev`  ← used by the **dev** service

(Keeping them separate means test data never mixes with real data.)

Also note the instance's **Connection name** (Overview tab), it looks like:
`your-project:europe-west4:ai-model-chat-db`

---

## 3. Connect each Cloud Run service to the instance

Do this for **both** `ai-model-chat` and `ai-model-chat-dev`:

1. **Cloud Run** → open the service → **Edit & deploy new revision**.
2. Open the **Containers → Connections** tab (or the "Cloud SQL connections"
   section) → **Add connection** → pick `ai-model-chat-db`.
3. Go to the **Variables & Secrets** tab and add ONE variable:

   **Name:** `DATABASE_URL`

   **Value (LIVE service, database `appdb`):**
   ```
   postgresql+psycopg2://postgres:YOUR_PASSWORD@/appdb?host=/cloudsql/your-project:europe-west4:ai-model-chat-db
   ```

   **Value (DEV service, database `appdb_dev`):**
   ```
   postgresql+psycopg2://postgres:YOUR_PASSWORD@/appdb_dev?host=/cloudsql/your-project:europe-west4:ai-model-chat-db
   ```

   Replace `YOUR_PASSWORD` with the password from step 1, and
   `your-project:europe-west4:ai-model-chat-db` with your real Connection name.

4. Click **Deploy**.

The app creates its tables automatically on first start. Done — data now
persists across every future deploy.

---

## How to tell it worked

1. Open the dashboard, **copy a model**, then **redeploy** (push any change).
2. After the redeploy, refresh the dashboard — the copy is **still there**.
   (With SQLite it would have vanished.)

---

## Cost note

A smallest-tier Postgres instance runs a few dollars/month even at idle (unlike
Cloud Run, it does not scale to zero). If that matters early on, you can keep
SQLite for now and attach Cloud SQL once you have real users — the app needs no
code changes either way.
