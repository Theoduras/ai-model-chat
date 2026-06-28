# Deploy with no terminal — Google Cloud Console (click-through)

Deploy the app straight from GitHub using only your browser. ~10 minutes.

---

## 0. One-time: a Google Cloud project

1. Go to https://console.cloud.google.com
2. Top bar → project dropdown → **New Project** (or reuse your Gemini project).
3. Make sure **billing is enabled**: search "Billing" in the top search bar and
   link a card. (Cloud Run is free while idle; you only pay under real traffic.)

---

## 1. Connect Cloud Run to your GitHub repo

1. In the top search bar, type **Cloud Run** and open it.
2. Click **Deploy container → Service**.
3. Choose **Continuously deploy from a repository (source or function)** →
   **Set up with Cloud Build**.
4. Click **Enable** on any APIs it asks for (Cloud Build, Artifact Registry).
5. **Repository provider:** GitHub → authenticate → pick
   **Theoduras/ai-model-chat**.
6. **Branch:** `^deploy/cloud-run-online$`
7. **Build type:** select **Dockerfile** (the repo already has one). Leave the
   path as `/Dockerfile`.
8. Click **Save**.

---

## 2. Service settings

1. **Region:** `us-central1` (or closest to your audience).
2. **Authentication:** choose **Allow unauthenticated invocations**
   (this is a public website — fans need to reach it).
3. Expand **Container(s), Volumes, Networking, Security** →
   **Variables & Secrets** tab → add these environment variables:

   | Name | Value |
   |---|---|
   | `GEMINI_API_KEY` | your Gemini key |
   | `API_KEYS` | any secret string you invent (for your app's API calls) |
   | `ADMIN_PASSWORD` | a password to lock the `/dashboard` builder |

   *(For stronger security you can store these as Secrets instead of plain
   variables — but plain variables are fine to launch.)*

4. Leave the rest at defaults. Click **Create**.

---

## 3. Done

Cloud Run builds the container and shows a green check with your live URL:

```
https://ai-model-chat-xxxxx-uc.a.run.app
```

- Fans open that URL → the chat.
- `…/dashboard` → the persona builder (asks for `ADMIN_PASSWORD`).
- `…/landing` → the creator landing page.

From now on, **every push to `deploy/cloud-run-online` redeploys automatically.**

---

## 4. Later: your own domain

Cloud Run → your service → **Manage custom domains** → **Add mapping**, enter
your domain, and add the DNS records it shows you at your registrar. HTTPS is
automatic. No redeploy needed.

---

## If something goes wrong

- **Build failed:** Cloud Run → your service → **Revisions** / **Logs**, or
  Cloud Build → **History** → click the red build to see the error.
- Copy the error and send it over — it's almost always a missing env var or a
  billing/permission toggle.
