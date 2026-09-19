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
vercel.json                     — Vercel routing config (secondary host)
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
- Discord is driven as a real user account, not a bot application. That is
  against Discord's terms of service and the account can be terminated for it,
  so anything that makes an account look automated is a bug: one socket per
  token, one stable client fingerprint, never join a server, never open a DM
  first, and never reconnect at a token Discord has already refused.
- Her Discord account is connected through the same hosted sign-in browser
  OnlyFans uses (`of_connect.SITES`, `/discord/connect`), not by pasting a
  token. The operator signs in on Discord's own page, so the captcha, 2FA and
  the new-device code are Discord's to handle; what comes back is the token
  plus the build and capabilities that account really identified with. Those
  two are what the gateway must then claim to be — captured, never guessed.
- **Generation is admin-only while it is in testing.** `/studio` and every
  `/api/generate/*` route go through `_require_admin`, which 404s rather than
  403s so the surface is not discoverable; the sidebar item carries
  `admin-only` and sits under Developers. An admin holds `UNLIMITED_CAPS`, so
  `credits_month` is None and nothing is charged or written to the ledger —
  the credit prices the studio shows are what a creator's plan *would* pay.
  Credit top-up checkout is admin-gated too: nobody should buy credits for a
  feature that is not offered yet. Open it to creators by dropping those
  `_require_admin` calls and the `admin-only` class on the sidebar item.
- **NSFW generation never runs on Google.** Imagen and `gemini-2.5-flash-image`
  refuse explicit content at any safety level, so `/api/generate/image` stays as
  the SFW path and everything explicit goes through `imagegen.py` to a managed
  provider (Runware primary, ModelsLab fallback). No self-hosted GPU: a worker
  that has to stay up is more expensive than the feature is worth.
- Identity is never left to the prompt. A still is conditioned on an approved
  vault photo *and* faceswapped from the same photo — a full-body NSFW pose is
  exactly where a reference alone drifts. **A clip is only ever generated from
  an already-approved still** (`kind='video'` requires `reference_media`), so
  the first frame carries the identity and there is nothing to correct. There
  is no text-to-video path, and the Animate button lives on a vault item rather
  than a prompt box for that reason.
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
- Discord server channels are the one exception, in `_dc_channel_round`: a room
  full of people is not a fan being worked towards something, so it never runs
  the funnel, never nudges, and never carries an offer. A paid link only ever
  goes out in a DM.
- **TikTok is parked too.** The sidebar says Coming soon, `growth.PUBLISHABLE`
  no longer carries it, and a planned TikTok post goes back to the creator as a
  `manual` row rather than firing at a channel the console is not offering. The
  app, the OAuth and the Content Posting transport are all built and tested and
  stay in the tree; unpark it by putting `tiktok` back in `PUBLISHABLE`, in
  `PL_PUBLISHABLE` in `planner.html`, and restoring the sidebar item.
- **OnlyFans is parked in the UI.** The sidebar says Coming soon and the
  onboarding's done card no longer offers it, so nobody is pointed at a
  connection they are not meant to make yet. Nothing else changed: the console,
  `onlyfans.py`, the hosted sign-in and the reply loop all stay in the tree and
  `/onlyfans` still serves, the same way Reddit's does. Unpark it by restoring
  the sidebar item (`data-platform="onlyfans"` with its `openPlatform` handler)
  and the `onlyfans` entry in `PLATFORM_CARDS` in `js/onboarding.js`.
- **Reddit is parked.** The sidebar says Coming soon, `growth.PUBLISHABLE` no
  longer carries it, and a planned Reddit post goes back to the creator as a
  `manual` row. Everything else is built and tested and stays in the tree —
  only the way in is missing, and it is missing at Reddit's end, not ours.
  Reddit shut down self-serve app creation (the create button on
  `/prefs/apps` silently refreshes), Data API access is now a manual approval
  at <https://developers.reddit.com/app-registration>, and Devvit cannot
  stand in: its apps install only into communities you fully moderate, and
  `runAs: 'USER'` states plainly that it needs "an explicit manual action,
  e.g. from a button" and forbids automated actions. Unpark it by getting an
  approved `client_id` and putting Reddit back in `PUBLISHABLE` and the
  sidebar.
- Reddit is the one platform here that is **not** a driven browser, and it got
  there the hard way. It started out like Discord — hosted sign-in
  (`of_connect.SITES['reddit']`), cookie, bearer, a Sendbird socket for chat —
  because Reddit Chat has never been reachable from the API. Reddit refused that
  browser on every auth path: correct credentials came back "invalid username or
  password", the one-time email link came back `UPEl3D`, on a clean residential
  IP with patchright, with the page loading fine. Reddit was rejecting the
  client, not the account. Chat was then dropped from scope, which removed the
  only reason to drive a browser at all, so her account is now connected as a
  **registered Reddit app** (`reddit_oauth.py`, `/reddit/oauth/start` →
  `/reddit/oauth/callback`): the operator approves once, `duration=permanent`
  brings back a refresh token that does not expire, and `reddit_rest.Rest`
  speaks to `oauth.reddit.com` with a bearer and no cookie. That is the only
  path worth extending. It also fixes the ToS posture — a declared app under
  Reddit's developer terms, rather than a client its terms forbid.
- Devvit (Reddit's Developer Platform, developers.reddit.com) is **not** an
  option for this and was checked: a Devvit app can only be installed into
  communities the developer *fully moderates*. It cannot post into a subreddit
  she does not own, which is the entire job. It is worth revisiting only if a
  persona ever runs her own subreddit — there it needs no auth at all, fires on
  a CommentCreate trigger rather than polling, and can act as her via
  `runAs: 'USER'` — but it is TypeScript on Reddit's infrastructure, so it
  would be a second codebase calling back into this one for the reply text.
- The two fallbacks stay because they are the only things that can carry a chat
  token: the hosted window, and a session pasted in by hand. Both expire, both
  need a per-persona residential proxy (`_rd_proxy_for`), and an OAuth session
  deliberately uses no proxy at all — an approved app has no reason to hide, and
  a pool only adds a way for a post to fail. `reddit_chat.py` and the DM half of
  the adapter are kept but dormant, gated on a `bearer` no OAuth session has.
- A refresh token is the whole connection, so it is never thrown away on a
  guess. `_rd_fresh_token` clears the session only when Reddit calls the
  refusal final (400/401/403); a network failure raises and leaves the token
  alone, because losing it means the operator approves again for nothing.
- Reddit's two carve-outs are not optional. A public comment thread never runs
  the funnel and never carries a link, a CTA or a URL — a subreddit is the
  fastest place to lose an account over one, and `_rd_comment_round` is
  deliberately outside the shared round for that reason, the same way
  `_dc_channel_round` is. And a Sendbird group channel is dropped at the
  dispatcher, so a room full of people never reaches the reply round. In a DM
  the funnel runs in full, but the offer is her profile or linktree rather than
  a direct unlock link: Reddit filters known paysite domains.
- One planned Reddit post is several posts. The planner writes one
  `ScheduledPost` row per subreddit, each with its own title, flair and slot,
  staggered by default — the same words in four subreddits at once is what a
  spam filter is built to catch.
- Instagram is built the same way Discord is — a real signed-in account
  through the same hosted sign-in browser (`of_connect.SITES['instagram']`,
  `/instagram/connect`), because Meta's Graph API needs a Business/Creator
  account plus app review and still cannot post Stories at all. It is
  posting-only (Stories, Posts, Reels via `instagram_rest.py`), so it is
  deliberately not a `_Platform` adapter: there is no DM, no funnel and no
  scheduler yet, just `_ig_post_now` triggered from the console. Instagram's
  terms do not allow an automated client either, so the same care applies —
  an account that can be lost, not the creator's only one.
- TikTok is a **registered app**, the same answer Reddit arrived at, and for
  the same kind of reason. It started out as a signed-in account through the
  hosted browser; that cannot work, because every tiktok.com web call carries a
  signature its own JavaScript computes over the query string and the user
  agent, which a captured cookie cannot carry — calls come back as empty 200s —
  and the sign-in burned the account's SMS quota before it ever got in. Posting
  is all that is wanted here and posting is documented, so it is
  `tiktok_oauth.py` (Login Kit, `/tiktok/oauth/start` → `/tiktok/oauth/callback`)
  plus `tiktok_rest.py` speaking the Content Posting API with a bearer token.
- **What an unaudited app may do decides the shape of it.** Direct Post — up on
  her profile — needs TikTok to audit the app, and until it does every post is
  SELF_ONLY and only five accounts a day may post at all. Uploading to her inbox
  needs no audit: the video lands in her TikTok drafts and she taps publish,
  picking the privacy herself. So inbox is the default and `TIKTOK_DIRECT_POST=1`
  is the switch, one env var and one re-approval, the day the audit clears. The
  console and the planner both say which of the two is live, because "posted"
  and "in her drafts" are not the same claim.
- TikTok's refresh token **rotates on every refresh**, unlike Reddit's. The one
  that comes back is stored or the connection dies within the day. The
  fatal-vs-blip rule is Reddit's exactly: only a refusal TikTok calls final
  (400/401/403) clears the session, because losing a token to a network blip
  means approving again for nothing.
- It is not a `_Platform` adapter and will not become one: no DMs, no funnel, no
  comment replies — TikTok has no comment API at all. Posting is one video; a
  photo post can only be pulled from a URL on a domain verified with TikTok,
  which is setup nobody has done, so `growth.MEDIA_SUPPORT['tiktok']` offers a
  clip only rather than failing at a slot. TikTok bars pointing anyone at adult
  content, so `growth.SFW_LOCKED` holds this channel safe for work and no paid
  link ever rides on it.

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
