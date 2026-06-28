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

## Tips

- Give the dev service a **separate** `ADMIN_PASSWORD` if you want, so dev and
  live dashboards are isolated.
- You can use the same `GEMINI_API_KEY` on both, or a separate key on dev to
  track usage independently.
- Watch a deploy: Cloud Run → service → **Revisions**, or Cloud Build → **History**.
