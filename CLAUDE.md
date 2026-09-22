# CLAUDE.md — ai-model-chat Project Rules

## CRITICAL: Session Continuity Rule

**Claude Code NEVER stops running mid-session due to user questions.**
- Questions during a run are treated as context or clarification — answer inline and keep going.
- The only stop signal is the exact string **`STOP RUNNING`** typed in full caps by the user.
- If the user asks something mid-task, answer it concisely and continue the task without waiting for further prompting.

---

## Project Purpose

This is an **OnlyFans-style AI chatbot platform** where content creators (models) can:
1. Build a custom AI persona via a **visual configuration interface**.
2. Define the persona's voice, tone, backstory, and escalation strategy.
3. Deploy that persona to convert fans → engaged chatters → content buyers.

The backend is Python/Flask + Google Gemini. The frontend is plain HTML/JS. It runs as a
Docker container on **Google Cloud Run** (see Deployment Rules below). A Vercel config is
kept as a secondary target and still works, but is not where the app is deployed.

---

## Architecture

```
app.py                          — Flask server, Gemini API, multi-persona, builder API
studio.html                     — Generation studio (admin-only while in testing)
characters.py                   — Character catalogue: views, features, level rules, prompts
characters.html                 — Character builder (face, checks, body views; admin-only)
js/character-visuals.js         — Option drawings for the character builder (SVG, parametric)
test_characters.py              — SFW/NSFW separation and validation tests
imagegen.py                     — NSFW image/video generation (Runware, ModelsLab)
credits.py                      — Credit pricing, tier packs, margin floor
storage.py                      — GCS for generated media (staging vs kept)
test_credits.py                 — Pricing and ledger tests (python test_credits.py)
onlyfans.py                     — OnlyFansAPI transport (OnlyFans chat)
onlyfans.html                   — OnlyFans console (connect, auto-reply, PPV)
of_connect.py                   — Hosted sign-in browser (OnlyFans, Discord, Instagram)
discord_gateway.py              — Discord user-account gateway, gates and caches
discord_rest.py                 — Discord REST, rate limits, client fingerprint
discord_stub.py                 — Offline Discord transport (tests only)
discord.html                    — Discord console (connect, channels, chime-in)
instagram_rest.py                — Instagram REST (upload + configure Post/Story/Reel)
instagram_stub.py                — Offline Instagram transport (tests only)
instagram.html                   — Instagram console (connect, post now — no DMs/funnel)
reddit_oauth.py                  — Reddit OAuth (approve once, refresh token that never expires)
reddit_rest.py                   — Reddit REST (S3 media lease, submit, comments, flairs)
reddit_chat.py                   — Reddit Chat gateway (Sendbird socket, DMs only)
reddit_stub.py                   — Offline Reddit transports (tests only)
reddit.html                      — Reddit console (connect, subreddits, post, DMs, comments)
tiktok_oauth.py                  — TikTok OAuth (approve once, rotating refresh token)
tiktok_rest.py                   — TikTok Content Posting API (init, upload, status)
tiktok_stub.py                   — Offline TikTok transport (tests only)
tiktok.html                      — TikTok console (connect, post a video)
admin.html                      — Visual persona builder UI (creator-facing)
index.html                      — Fan chat UI (embeds as iframe in profile.html)
chat.html                       — Standalone fan chat (mobile hamburger link)
profile.html                    — Creator landing page with embedded chat
profile_data.json               — Legacy Lilith profile data (fallback)
personas/
  lilith.txt                    — Lilith system prompt (auto-generated or hand-edited)
  lilith.config.json            — Lilith builder config (source of truth for the form)
  lilith.json                   — Lilith profile/landing page data
  {slug}.txt                    — Any other persona's system prompt
  {slug}.config.json            — Any other persona's builder config
grok-lilith-prompt.txt          — Legacy location (still loaded as fallback)
templates/profile.html          — Jinja2 template variant (unused currently)
.env                            — API keys (never commit)
Dockerfile                      — Cloud Run image (the real deployment)
api/index.py                    — Vercel entrypoint (secondary host)
api/requirements.txt            — Vercel deps (trimmed; wins over the root file)
vercel.json                     — Vercel routing + cron config (secondary host)
requirements.txt                — Python deps: flask, google-genai, python-dotenv, google-auth
```

### Key API Routes

| Route | Method | Purpose |
|---|---|---|
| `GET /` | — | Fan chat UI |
| `GET /profile` | — | Creator landing page |
| `GET /admin` | — | Persona builder UI |
| `POST /chat` | JSON | Send message, get AI reply. Accepts `persona` slug param. |
| `GET /api/profile?persona=slug` | — | Profile data JSON for landing page |
| `GET /api/personas` | — | List all personas |
| `GET /api/personas/{slug}` | — | Get persona config + prompt |
| `POST /api/personas/{slug}` | JSON | Save config and regenerate system prompt |
| `POST /api/personas/{slug}/preview` | JSON | Preview generated prompt without saving |
| `GET /studio` | — | Generation studio (admin only) |
| `GET /api/credits` | — | Balance, tier-resolved packs, generation price table |
| `POST /api/credits/checkout` | JSON | Buy a top-up pack (Stripe or Oxapay) |
| `POST /api/generate/job` | JSON | Submit an image or video generation |
| `GET /api/generate/job/{id}` | — | Poll one generation |
| `GET /api/generate/jobs?persona=` | — | Recent generations, with staged media |
| `POST /api/generate/keep` | JSON | Keep (promote + approve) or drop staged media |
| `GET /api/generate/tick` | — | Cron sweeper: advance jobs, refund dead ones (`CRON_SECRET`) |
| `GET /characters` | — | Character builder (admin only) |
| `GET/POST /api/characters` | JSON | List / create a character (`persona` creates it linked) |
| `POST /api/characters/{id}/generate` | JSON | Generate options for one view |
| `POST /api/characters/{id}/images/{img}/approve` | JSON | Make an image a view's approved photo; bumps the version |
| `GET /api/personas/{slug}/character` | — | Linked character, and which views a `shot`/`scene` sends |

---

## Visual Persona Builder — Core Feature

The persona builder is the **centrepiece** of this platform. It must be a UI panel (not code editing) where the creator fills in fields and Claude Code generates/updates the system prompt and profile data automatically.

### Persona Voice Fields (all configurable via UI)

| Field | Description |
|---|---|
| **Name & Age** | The persona's display name and age |
| **Backstory** | 2-3 sentence origin story (job, city, vibe) |
| **Personality archetype** | e.g. Deadpan/Dry, Bubbly/Sweet, Dominant/Edgy, Girl-Next-Door |
| **Speech style** | Sentence length, grammar precision, use of slang/emoji |
| **Warmth level** | Cold → Distant → Friendly → Warm → Affectionate (slider) |
| **Flirt escalation pace** | Slow burn → Moderate → Fast → Instant |
| **NSFW engagement** | Toggle on/off + explicit permission level (suggestive / moderate / explicit) |
| **Interests & hooks** | Topics the persona returns to naturally (gaming, fitness, fashion, etc.) |
| **Conversion triggers** | Phrases/moments that naturally introduce paid content offers |
| **Question frequency** | How often the persona asks the fan a question (keeps engagement) |
| **Memory style** | How the persona references past conversation details |

### Conversion Funnel Rules (must be baked into every persona prompt)

The chatbot's job is to move fans through this funnel — the persona builder controls the pace:

1. **Warm** — Friendly opener, build rapport, ask about the fan.
2. **Engage** — Reference things the fan said, create a sense of being "known".
3. **Intrigue** — Drop hints about exclusive content without hard-selling.
4. **Tease** — Light flirt or behind-the-scenes reference that implies more exists.
5. **Offer** — Natural, in-character mention of a paid post/PPV/subscription perk.
6. **Close** — Reinforce the value, make the fan feel special for buying.

The system prompt generator must inject the current funnel stage logic based on creator settings.

---

## System Prompt Generation Rules

When the persona builder saves, Claude Code must:

1. Read the field values from the UI config form.
2. Generate a `grok-{persona-slug}-prompt.txt` file using this structure:

```
You are [Name], [Age], from [Location]. [Backstory sentence].

Personality: [Archetype description]. [Speech style rules].

Voice rules:
- [Grammar/punctuation rule]
- [Sentence length rule]
- [Warmth instruction]
- [Question frequency rule]

Engagement rules:
- Always reference things the fan has shared previously.
- Build genuine interest in the fan as a person.
- [Flirt escalation instruction]
- [NSFW rule if enabled]

Conversion rules:
- Move naturally from rapport → intrigue → tease → offer.
- Never hard-sell. Let the offer feel like a favour to the fan.
- [Creator-defined conversion triggers]

Stay completely in character. Never mention being an AI.
```

3. Update `profile_data.json` with the new persona metadata.
4. Hot-reload the persona in the Flask app without restarting the server.

---

## Coding Standards

- **Python**: Flask, no ORM. Keep app.py clean — extract helpers into `utils.py` if file exceeds 300 lines.
- **Frontend**: Vanilla JS + plain HTML. No React, no build step. Tailwind CDN is acceptable for styling.
- **API calls**: Always use `try/except` around Gemini calls with meaningful fallback messages.
- **Secrets**: `.env` only. Never hardcode keys. `.env` is in `.gitignore`.
- **Logging**: Use the existing `get_chat_logger` pattern. Log every message sent/received with timestamp.
- **Persona files**: Stored in `personas/{slug}.txt` (prompt) and `personas/{slug}.config.json` (builder config). `grok-{slug}-prompt.txt` in root is legacy fallback only.
- **JSON data**: `personas/{slug}.json` is the source of truth per persona. `profile_data.json` is the legacy fallback for Lilith only.

---

## Host Rules

- All file writes at runtime must use `/tmp/`. Nothing written to disk survives:
  Cloud Run's filesystem is in-memory and gone on restart, Vercel's is read-only
  outside `/tmp` and wiped on every cold start. Anything that must persist goes in
  the database.
- Static assets served directly from root via Flask `static_folder=BASE_DIR`.
- Test locally with `python app.py` before pushing.
- Cloud Run builds from the `Dockerfile`; `vercel.json` and `api/index.py` only
  matter if deploying to Vercel. Keep both working when adding routes — everything
  already routes through Flask, so a new `@app.route` needs no config change on
  either host. See `DEPLOY.md`.
- The Fanvue, OnlyFans, X, Telegram and Discord loops need an always-on host, so
  they run on Cloud Run and stay off on Vercel (`IS_VERCEL` in `app.py`).
- **Generation is admin-only while it is in testing.** `/studio` and every
  `/api/generate/*` route go through `_require_admin`, which 404s rather than
  403s so the surface is not discoverable; the sidebar item carries
  `admin-only` and sits under Developers. An admin holds `UNLIMITED_CAPS`, so
  `credits_month` is None and nothing is charged or written to the ledger —
  the credit prices the studio shows are what a creator's plan *would* pay.
  Credit top-up checkout is admin-gated too: nobody should buy credits for a
  feature that is not offered yet. Open it to creators by dropping those
  `_require_admin` calls and the `admin-only` class on the sidebar item.
- **Generation works on Vercel as well as Cloud Run, by doing on-request what
  the worker does off-request.** `GEN_HAS_WORKER` (from `_worker_enabled`) is
  the switch. With a worker, a submit goes to a thread and `_gen_worker` polls.
  Without one — Vercel freezes a lambda the moment it answers, so a thread
  never finishes and a job nothing polls holds the creator's credits forever —
  the submit runs inline on the request, and `_gen_advance_open` moves the
  jobs a reader is about to look at, so the studio's own polling drives them.
  `/api/generate/tick` is the sweeper for jobs with no tab open on them: it is
  in `_OPEN_PATHS` because a scheduler has no session, carries `CRON_SECRET`
  instead, and stays shut when that is unset. `vercel.json` has the `crons`
  entry; on Hobby that fires once a day, which only delays a refund, so set a
  minute-granularity pinger if that matters. The staging lifecycle rule moved
  out of the worker into `_gen_ensure_lifecycle` for the same reason: the one
  host with no worker was the one host where generated media never expired.
- Storage has two backends behind one interface in `storage.py`, chosen by
  whichever is configured: GCS on Cloud Run, Vercel Blob on Vercel, which has
  no GCP credentials at all. Two things differ and both are handled there — a
  private Blob object is 403 without the store token, so `signed_url` returns
  None and the media route proxies the bytes rather than leaking that token to
  a browser; and Blob has no lifecycle rules, so `purge_staging()` carries the
  three-day window and `/api/generate/tick` is what calls it.
- `api/requirements.txt` is the Vercel dependency set and wins over the root
  one for that entrypoint. It drops playwright, patchright, telethon and
  gunicorn — the always-on host's stacks, all imported lazily, none of which a
  lambda can use — to stay under the 250 MB unzipped limit. Adding a root
  dependency that `app.py` imports at module level means adding it here too.
- **NSFW generation never runs on Google.** Imagen and `gemini-2.5-flash-image`
  refuse explicit content at any safety level, so `/api/generate/image` stays as
  the SFW path and everything explicit goes through `imagegen.py` to a managed
  provider (Runware primary, ModelsLab fallback). No self-hosted GPU: a worker
  that has to stay up is more expensive than the feature is worth.
- Identity is never left to the prompt. A still is conditioned on an approved
  vault photo *and* faceswapped from the same photo — a full-body NSFW pose is
  exactly where a reference alone drifts. **A clip that claims to be her is
  only ever generated from an already-approved still**, so the first frame
  carries the identity and there is nothing to correct. The one exception is
  the safe-work **Reel** job, which may run from a prompt alone: a prompt-only
  clip makes no claim to be anybody, carries no identity lock, and is the
  reason Reel is SFW-only in `imagegen.VIDEO_JOBS`. Every other video job —
  Animate, Extend, Multi-reference, Swap — still needs her: an approved still,
  an approved clip, or her reference slots. All of them land unapproved in
  `staging/` either way, so nothing reaches a fan unreviewed.
- **The video surface is five jobs, not a model picker.** `imagegen.VIDEO_JOBS`
  is the one table that says what each job needs, which models serve it, and
  which prompt clauses are forced on; `credits.JOB_MODELS` mirrors it so the
  picker and the price table read one list, and `SWAP_MODELS` and friends are
  derived aliases over it. The model is a consequence of the job — an admin can
  override it, but only within the job's own list, because a model moved after
  the quote is a model the creator was charged for and never ran. An explicit
  swap carries `imagegen.PRESERVE_CLAUSE` ahead of the creator's own words when
  the model regenerates rather than replaces; that text is not creator-editable.
- **An extension is its own clip, not a longer one.** Joining one onto its
  source needs ffmpeg, which the image does not have, so `extend` delivers each
  generation as a separate vault clip chained by `PersonaMedia.parent_media`.
  Its `longer` mode asks for the model's own ceiling in one pass rather than
  stitching. The frame it continues from is captured **client-side** — a canvas
  seek in `studio.html` — for the same reason: there is nothing on the server
  that can decode a video.
- **Audio is provider-side only**, for the same missing ffmpeg: either the
  generation task emits it (`RW_VIDEO_AUDIO_FLAG`/`RW_VIDEO_AUDIO_FIELD`) or a
  follow-on video-to-audio task returns a muxed clip (`RW_AUDIO_ROUTE=task`).
  A model that does not know the audio fields has them dropped by `_send`'s
  refused-parameter retry and still returns the clip, silent. **None of the
  audio model ids, the audio field names or a video-concat task is verified
  against the live catalogue** — run `imagegen.search_models('audio')` the
  moment a Runware key is reachable and correct the env defaults.
- **A character is identity material, never vault media.** `CharacterImage` is
  its own table so no send, post or pick path can reach one. Once a linked
  character is complete, every still for that persona carries its approved
  views as references and its features as prompt text, both cut to the shot's
  level by `characters.views_for_job` / `describe`: a safe-work shot never gets
  an explicit photo or word. The face and full-body photos need a confident
  "adult" from the vision check (SFW images only — nothing explicit goes to
  Google) and the face needs every feature ticked. Each approval saves a
  `CharacterVersion`; jobs record `character_version`. Face uploads are
  references only; stacked youth-leaning choices are refused in `characters.validate`.
- A generation lands in `staging/` unapproved and is **invisible to every send
  path** — `_approved_only` filters `_pick_media`, `_pick_phase_photo` and the
  vault listing, so nothing unreviewed can reach a fan. Keeping it promotes it
  to `kept/` and sets `approved`. The three-day purge is a **bucket lifecycle
  rule**, installed at boot by `_gen_worker`, never a loop: a worker that is
  not running must not be why a generation outlives its window.
- **Credits are pegged to provider cost** — `credits.CREDIT_COST_USD`, one
  number — so margin is identical whatever is generated and a new model is a
  table entry, not a pricing decision. `credits.py` asserts at import that every
  pack clears `MIN_MARGIN_MULTIPLE` times cost, and `test_credits.py` walks the
  matrix, so a discount that would lose money fails the build. The ledger is
  append-only (`CreditLedger`): balance is the sum of rows, never a counter,
  because people buy these. A spend drains the expiring monthly allowance before
  anything purchased, and a refund returns credits to the bucket they left.
- A credit top-up rides the same `Payment` row, providers and webhooks as a
  subscription — `kind` is the only thing that tells them apart, so Oxapay keeps
  working and a redelivered webhook cannot credit twice.
- Fanvue, OnlyFans and Discord DMs share one reply engine through the platform
  adapters (`_Platform` in `app.py`): a platform says where its state is keyed,
  how a chat reads, and how a message goes out. New platform work belongs in an
  adapter, never in a second copy of the round.
- **Per-platform detail lives in `docs/platforms.md`.** Discord, OnlyFans,
  Reddit, Instagram and TikTok each have a section there explaining why they
  are built the way they are and why the parked ones are parked. Read the one
  section for the platform you are touching, not the file. The rules below are
  the parts that bind everywhere, so they stay here.
- Discord and Instagram are driven as **real signed-in accounts**, against
  both services' terms, so anything that makes an account look automated is a
  bug: one socket per token, one stable client fingerprint, never join a
  server, never open a DM first, never reconnect at a token that was already
  refused. Both connect through the hosted sign-in browser (`of_connect.SITES`).
  Reddit and TikTok are **registered apps** with OAuth bearer tokens instead,
  which is the shape worth extending.
- A session is cleared **only on a refusal the platform calls final**
  (400/401/403). A network failure raises and leaves the token alone, because
  losing a refresh token means the operator approves again for nothing.
  TikTok's refresh token rotates on every refresh and must be stored each time;
  Reddit's does not expire.
- **A public room never runs the funnel.** `_dc_channel_round` and
  `_rd_comment_round` sit deliberately outside the shared reply round: no
  funnel, no nudge, no offer, and on Reddit no link, CTA or URL at all. A paid
  link only ever goes out in a DM, and a Sendbird group channel is dropped at
  the dispatcher.
- **Parked right now: Reddit, TikTok, and OnlyFans in the UI.** The sidebar
  says Coming soon, `growth.PUBLISHABLE` drops Reddit and TikTok, and a planned
  post for either comes back to the creator as a `manual` row. Everything else
  is built, tested and stays in the tree. `docs/platforms.md` says what each
  one needs to unpark.
- Instagram and TikTok are posting-only and deliberately **not** `_Platform`
  adapters — no DMs, no funnel, no comment replies. `growth.SFW_LOCKED` holds
  TikTok safe for work, so no paid link ever rides on it.

---

## Deployment Rules

The app auto-deploys via a Cloud Build trigger on push to `develop`, to the
single Cloud Run service `ai-model-chat-dev` (see `ENVIRONMENTS.md`).

- **Push to `develop`.** That is the deploy: the trigger picks it up.
- Never run a `gcloud run deploy` or `gcloud builds` command by hand to ship app
  code — pushing to `develop` is the only path for that.
- **The app's trigger reads `cloudbuild.app.yaml`** (it carried an inline config
  until that was exported and replaced; the inline one built with `--no-cache`).
  It builds the `Dockerfile` and passes only `--image` and the deploy labels to
  `gcloud run services update`. So a Cloud Run setting for `ai-model-chat-dev` —
  memory, instances, session affinity, env vars — **still cannot be changed from
  this repository**: never widen that step into a full `run deploy`. Set it on
  the service; it persists across deploys.
- `ai-model-chat-dev-browser` runs the same image with a different entrypoint
  and deploys itself from `develop` through its own trigger, which *does* read a
  build config from here: `cloudbuild.browser.yaml`. Every service flag it needs
  is restated in that file, because its deploy step is a full `gcloud run
  deploy`. It was hand-deployed so that a deploy could not interrupt a sign-in;
  in practice it ran week-old code and blocked the signing repair, which runs
  inside it. An interrupted sign-in can be started again — see
  `ENVIRONMENTS.md`.

---

## Feature Development Priorities

When building new features, tackle in this order:

1. **Persona builder UI** — visual form that generates system prompts.
2. **Multi-persona support** — switch between different creator personas.
3. **Conversation analytics** — track funnel stage per fan, conversion events.
4. **Scheduled messages** — drip campaigns to re-engage fans who go quiet.
5. **Content unlock flow** — in-chat PPV/link delivery triggered by funnel stage.

---

## What NOT to Do

- Do not add comments explaining what code does — only add comments when the WHY is non-obvious.
- Do not create new files unless they are genuinely needed.
- Do not add error handling for impossible scenarios.
- Do not build features beyond what the current task requires.
- Do not push to any branch other than `develop`.
- Do not create a pull request unless the user explicitly asks.

---

## Git Workflow

- Branch: `develop` — work goes here, including after a `/clear` or a new session,
  and regardless of which branch a session's setup message names.
- Commit messages: imperative, present tense, describe what and why.
- Push with: `git push -u origin develop`
- Retry push up to 4 times on network failure (2s, 4s, 8s, 16s backoff).
- `develop` is shared: pull before starting and merge rather than force-push, so
  nobody else's commits are dropped.

---

## Testing Checklist Before Every Push

- [ ] `python app.py` starts without errors
- [ ] Chat sends a message and gets a Gemini response
- [ ] Persona prompt loads correctly from file
- [ ] No API keys or secrets in committed files
- [ ] `Dockerfile` still builds if deps or entrypoint changed
- [ ] `python test_credits.py` passes — it is what stops a pack being sold below cost
