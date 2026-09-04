# Threads Reply-Bot — Implementation Plan

Mirrors the existing X integration (`_x_*` helpers, `/api/x/*` routes, `xbot.html`).
Threads is **public-only** — no DMs — so this is a *comment/mention auto-reply* bot,
not a DM funnel. No PPV path.

## Constants / hosts
- Authorize: `https://threads.net/oauth/authorize`
- Token exchange: `POST https://graph.threads.net/oauth/access_token`
- Long-lived exchange/refresh: `GET https://graph.threads.net/access_token?grant_type=th_exchange_token` / `th_refresh_token`
- API base: `https://graph.threads.net/v1.0`
- Scopes: `threads_basic`, `threads_content_publish`, `threads_read_replies`, `threads_manage_replies`

## Files to touch

### [MODIFY] app.py — backend
Helpers (mirror `_x_*`, store tokens via settings `threads_tokens_{persona}`):
1. `_threads_api(method, path, token, body)` — graph.threads.net helper.
2. `_load_threads_tokens` / `_save_threads_tokens` — settings-backed, per persona.
3. `_threads_refresh(persona)` — `th_refresh_token` (long-lived, 60d).
4. `_threads_call(persona, method, path, body)` — refresh-on-401 wrapper.
5. `_threads_me(persona)` — `GET /me?fields=id,username`.
6. `_threads_publish(persona, text, reply_to_id=None)` — 2-step create→publish.
7. `_threads_recent_media(persona)` — `GET /{uid}/threads`.
8. `_threads_replies(persona, media_id)` — `GET /{media-id}/replies`.
9. `_threads_mentions(persona)` — `GET /{uid}/mentions`.
10. `_threads_auto_round(persona, opts)` — for each recent post + mentions:
    fetch replies, skip own + already-seen (cursor in `threads_cursor_{persona}`),
    generate in-persona reply via `_persona_text`, `_fv_trim`, POST reply, log via `_log_x_message` (reuse logger).
11. Publishing-limit guard: `GET /{uid}/threads_publishing_limit`.

Routes (mirror `/api/x/*`):
- `GET  /api/threads/auth-url`     — build consent URL (PKCE optional; Threads uses client_secret)
- `GET  /api/threads/callback`     — code→token→long-lived, store
- `GET  /api/threads/oauth-redirect`
- `GET  /api/threads/status`       — connected?/username
- `POST /api/threads/disconnect`
- `POST /api/threads/publish`      — manual test post
- `POST /api/threads/poll`         — one reply round now
- `GET/POST /api/threads/auto`     — persistent toggle + opts (reply-to-comments, reply-to-mentions, interval)
- `POST /api/threads/auto-run`     — one round (used by worker)
- `GET  /api/threads/webhook` + `POST /api/threads/webhook` — verify challenge + receive `replies`/`mentions` events → enqueue round

App config settings: `threads_client_id`, `threads_client_secret`, `threads_redirect_uri`, `threads_webhook_verify_token`.

### [MODIFY] background worker
Add `threads` personas to the same auto-loop that runs `_fanvue_auto_round` / X rounds
(settings `threads_auto_personas`). Webhook is primary; poll is fallback.

### [NEW] threads.html
Clone `xbot.html`: persona picker + Connect Threads, "Auto-reply to comments/mentions"
toggles, interval, manual publish box, status pill, log link. Add nav link.

### [MODIFY] nav (devnav / page headers)
Add "Threads" link alongside "X Bot" / "Fanvue".

### [MODIFY] vercel.json / routes
Register `/threads` static route if needed (Flask serves from BASE_DIR, likely no change).

## Explicitly OUT of scope
- DMs / private chat (unsupported by API).
- PPV / paid content.
- Meta app review itself (user action — scopes need approval before prod).

## Test plan
1. `python app.py` boots, `/threads` renders.
2. `/api/threads/status` returns not-connected JSON.
3. After manual token paste (dev): `/api/threads/publish` posts a test thread.
4. `_threads_auto_round` dry-run (preview=True) returns generated replies without posting.
5. Webhook GET challenge echoes `hub.challenge`.

## Order of execution
1. Backend helpers 1–6 + `status`/`publish`/`callback`/`auth-url` (auth+publish core).
2. Reply round 7–11 + `poll`.
3. `auto`/`auto-run` + worker wiring.
4. Webhook verify+receive.
5. threads.html + nav.
