# Pricing tiers & account roles

Design document. Nothing here is implemented yet — this is the target the
enforcement work is built from.

## Why

Three plans already sell (`_BASE_TIERS`, `app.py`): Starter €49, Pro €149,
Agency €349, each with an annual twin at 9x the monthly price, plus a quote-only
`CUSTOM_TIER` card. Stripe and Oxapay checkout both work, `Payment` rows are
written, and `_activate_plan` sets `tier`/`status`/`expires_at`.

**But every tier's feature list is marketing copy and nothing else.**
`user['tier']` is never read to allow or deny anything. The only entitlement
mechanism is `_require_paid_account`, which is binary — paid or not paid:

- "1 AI persona" / "5" / "15" — `api_persona_save` and `api_persona_copy` do no
  count check at all.
- "Scheduled follow-ups" and "outfit locking" are sold as Pro+ but implemented
  unconditionally (`_fan_outfit_lock`, `_x_followup_round`, `followup_min` in
  both Telegram and Fanvue settings).
- "Conversation analytics" is an Agency bullet, but `/api/fanvue/ppv-stats`,
  `/api/telegram/stats`, `/api/visitors` and `/api/xlog` are reachable by any
  paid user.
- "Telegram, X and Fanvue" vs "Fanvue only" — the `_PAID_API` prefixes map 1:1
  to platforms, but the paywall treats them identically.
- The three tiers share four of their six bullets, so the ladder gives a buyer
  no reason to move up other than persona count.
- **Gemini spend is unmetered at every price point.** `/api/generate/image` is
  the dominant cost driver and has no cap of any kind.

Authorization is separately spread across four overlapping mechanisms, one of
which fails open:

| Mechanism | Where | Problem |
|---|---|---|
| `ADMIN_PASSWORD` session | `_check_admin()`, **58 call sites** | Returns `True` for *everyone* when `ADMIN_PASSWORD` is unset — the exact config where creators reach the dashboard |
| DB role `user`\|`admin` | `User.role`, bootstrapped from `ADMIN_EMAILS` | Only two levels; admins bypass the paywall entirely (`_user_is_active`) |
| Operator / persona scoping | `utils.py` — `_is_operator`, `operator_only`, `platform_scoped`, `owned_slugs` | The correct pattern, but only some routes use it |
| Ownership | `SavedPersona.owner_id`, `_guard_persona_writes` | Per-user, so an "Agency" account cannot have a team |

Prices are not changing: €49 / €149 / €349, ~25% off annual, no free tier.

---

## 1. Capability axes

Built from levers the code already exposes, so nothing below needs new plumbing
to *measure* — only to *check*:

| # | Axis | Existing hook |
|---|---|---|
| 1 | Persona count | `db_list_personas(owner_id=...)` |
| 2 | Platform access | `_PAID_API` prefixes; `api_platforms_overview`; per-platform `ready` flag in `js/platform-setup.js` |
| 3 | Funnel phase count | `api_persona_phases_save` already clamps to `items[:10]`, min 2 |
| 4 | Outfit locking | `_fan_outfit_lock`, `outfits_{slug}` setting |
| 5 | Scheduled follow-ups / auto-run loops | `/api/fanvue/auto`, `/api/x/auto-run`, `/api/threads/auto` — real server cost |
| 6 | **AI image generation** | `/api/generate/image` — the dominant Gemini COGS, currently unbounded |
| 7 | Analytics | `ppv_set_stats` / `fan_ppv_spend`; `/api/telegram/stats`; `/api/visitors`; `/api/xlog` |
| 8 | PPV reconciliation | `/api/fanvue/reconcile` |
| 9 | Seats | Does not exist yet — see section 4 |

### Deliberately *not* used as levers

- **Media library size.** Would mean capping rows in `persona_media`. With image
  generation capped the generated side is already bounded, and capping *uploads*
  penalises creators for using their own real photos — the behaviour the product
  wants most. Revisit only if Postgres storage actually bites (base64 in a Text
  column will get there eventually).
- **PPV sophistication.** The full engine — price ladders, per-fan pricing,
  re-offer cadence and discounts, the "Try a message" simulator — ships at
  **every tier**. Monetisation is the reason a creator buys at all; crippling it
  at €49 makes the entry tier fail at the one job it has.
- **Programmatic API** and **BYO Gemini key** — see section 5. Both are
  unsellable as written.

---

## 2. Tier re-cut

Same three price points. Each step answers a different question: **Starter buys
a persona that earns. Pro buys a roster across every platform. Agency buys a
team and the numbers to manage it.**

### Starter — €49/mo (€441/yr)
*"One persona, one platform, fully monetised."*

- **1 persona**, 1 seat
- **1 platform connection** of your choice (Telegram hosted bot, X, Fanvue or Threads)
- Full persona builder: every voice field, AI backstory interview, archetypes
- **Up to 3 funnel phases** + CTA link
- Photo sending from the media library
- **The complete PPV engine** — price ladders, per-fan pricing, re-offer cadence
  and discounts, the "Try a message" simulator
- **15 AI image generations/mo**
- Unlimited photo uploads
- Email support

### Pro — €149/mo (€1,341/yr) — *featured*
*"A full roster, on every platform, running itself."*

Everything in Starter, plus:

- **5 personas**, **2 seats**
- **All platforms** — Telegram (hosted bot + personal MTProto account), X, Fanvue, Threads
- **Up to 10 funnel phases**, with per-phase photo-send rate
- **Outfit locking** + media tagging (clothing / place / lighting)
- **Scheduled follow-ups** and auto-run loops (silence re-engagement, X audience
  gathering, Fanvue auto-reply)
- Fanvue list include/exclude targeting
- **75 AI image generations/mo**
- Priority support

### Agency — €349/mo (€3,141/yr)
*"Run a roster with a team, and see what it earns."*

Everything in Pro, plus:

- **15 personas**, **6 seats with roles** (manager / chatter — see section 4)
- **Conversation & revenue analytics** — PPV stats, per-fan spend, funnel-stage
  breakdown, visitor and X event logs
- **PPV reconciliation** against Fanvue earnings
- Persona cloning across the roster
- **225 AI image generations/mo**
- Dedicated support + onboarding call

### Custom — quote only, `coming_soon` (unchanged)
Unlimited personas and seats, custom funnel phases and integrations, roster
migration, named contact. Stays out of `TIERS` so nothing can charge for it.

### Why this ladder holds together

- **No bullet repeats verbatim across tiers.** Where a capability spans tiers it
  is *graded* (3 -> 10 phases; 15 -> 75 -> 225 generations), which is what makes
  the price step legible.
- **The expensive thing is metered.** Image generation is the real COGS and is
  currently unbounded at every price point. 15/75/225 makes gross margin a
  function of the plan rather than of how enthusiastic a customer feels.
- **Starter can actually make money.** Full PPV at €49 means the entry tier pays
  for itself, which is what drives the upgrade — a creator who is earning wants
  more personas and more platforms, and that is exactly what Pro sells.
- **Pro is the scale unlock.** 1->5 personas and one->all platforms is a single
  coherent story: you outgrew one girl on one site.
- **Agency is seats.** With the API and BYO-key bullets removed (section 5),
  seats, analytics and reconciliation are what justify €200 over Pro. That makes
  P4 load-bearing rather than optional: **Agency should not be marketed on seats
  until seats exist.** Until then Agency is honestly "15 personas + analytics +
  225 generations", which is thinner — consider holding the Agency re-cut until
  P4 ships.

### Migration note — resolve before enforcing

Today's Starter bullets promise "PPV content selling" and Pro promises
"Scheduled follow-ups", both of which currently work at *every* tier, and image
generation is unlimited everywhere. Enforcement is therefore a retroactive
downgrade for existing customers. Recommended: stamp `entitlements_version` on
`User` at activation and grandfather anyone active before the cutover onto the
old (unlimited) set until their next renewal.

---

## 3. Making tiers machine-readable (P0)

```python
# app.py, inside each _BASE_TIERS entry, alongside 'features'
'capabilities': {
    'personas': 1,                  # int, or None for unlimited
    'seats': 1,
    'platforms': 1,                 # count of connectable platforms, or None
    'phases_max': 3,
    'outfit_lock': False,
    'scheduled_followups': False,
    'analytics': False,
    'ppv_reconcile': False,
    'image_generations_month': 15,
}
```

`features` (the display bullets) stays hand-written for marketing tone; a test
asserts the two never contradict each other on the countable fields.

Three helpers, all pure and testable:

- `tier_capabilities(tier_key)` — resolves through `ANNUAL_SUFFIX`, since the
  `*_annual` twins are generated from the same base and must return the *same*
  capabilities.
- `user_capabilities(user)` — `tier_capabilities(user['tier'])`, with admins
  returning the unlimited set, preserving today's `_user_is_active` bypass.
- `require_capability(name)` — decorator returning **402** with
  `{'error', 'capability', 'current_tier', 'upgrade_to'}`, so the dashboard can
  render a targeted upgrade prompt rather than a generic paywall bounce.

The frontend hook already exists: `identifyUser()` in `dashboard.html` adds
`body.is-admin`, and CSS hides `.admin-only` while `.auth-pending`. Extend
`/api/me` to return the capability dict and add `data-cap="outfit_lock"`
alongside the existing `admin-only` class, driven from the same bootstrap.

Image generations need the one genuinely new store: a
`usage_counters(workspace_id, metric, period_start, count)` table, incremented
in `/api/generate/image` and surfaced in the dashboard as "12 of 15 used this
month". Without the visible counter the cap reads as a bug.

---

## 4. Account roles

### Three orthogonal concepts

1. **Platform role** — `User.platform_role` in `{user, support, admin}`. Crosses
   tenants. `support` is read-only across workspaces for troubleshooting, with
   every cross-tenant read logged; `admin` keeps today's behaviour.
2. **Workspace** — the billing tenant. `tier`, `status`, `expires_at` and
   `Payment.user_id` move here from `User`. Today the User *is* the workspace,
   so the backfill is one workspace per existing user.
3. **Membership** — `(workspace_id, user_id, role)` where role is one of
   `{owner, manager, chatter}`, counted against the tier's `seats` capability.

**Tier gates capabilities. Role gates actions. Both must pass** — an Agency
chatter cannot edit a persona even though Agency includes persona editing.

| Action | owner | manager | chatter | support | admin |
|---|:-:|:-:|:-:|:-:|:-:|
| Billing, plan changes | yes | — | — | — | yes |
| Invite / remove seats | yes | — | — | — | yes |
| Create / edit / delete personas | yes | yes | — | read | yes |
| Connect / disconnect platforms | yes | yes | — | read | yes |
| Configure PPV sets and cadence | yes | yes | — | read | yes |
| Send / reply in fan inboxes | yes | yes | yes | — | yes |
| Generate AI images | yes | yes | — | — | yes |
| View analytics | yes | yes | — | read | yes |
| Site content / landing editor | — | — | — | — | yes |

### Phased migration

**P0 — capabilities as data.** Section 3 above. Ship `capabilities` on every
tier, expose via `/api/me`, render `/pricing` and the `#pricing` section of
`comingsoon.html` from `/api/pricing` so marketing copy and enforcement share
one source. *No behaviour change* — a pure refactor, safe to ship alone.

**P1 — enforce the cheap, high-value caps.** Persona count at
`api_persona_save` and `api_persona_copy`; phase count at
`api_persona_phases_save`; platform allow-list beside `_path_needs_plan`;
image-generation counter at `/api/generate/image`. All server-side; UI hints are
cosmetic on top.

**P2 — close the authorization holes.** Three fixes, all security:

- Replace all 58 `_check_admin()` call sites with `@operator_only` /
  `@platform_scoped` from `utils.py`. `_check_admin` returns `True` for everyone
  when `ADMIN_PASSWORD` is unset, so on that deployment `/api/fanvue/auto`,
  `/api/x/settings` and `/api/fanvue/ppv-stats` are open to any paid user, for
  any persona.
- **Lock `/api/config/gemini-key` to `@operator_only`.** It sits under
  `/api/config` in `_PAID_API` with no role check, and the dashboard only hides
  the button with a CSS class — so any paying customer can swap the platform's
  Gemini key today (section 5).
- Make `_valid_api_key()` fail closed instead of returning `True` when
  `API_KEYS` is unset.

Sequence P2 **before** P3: a tenancy model built on gates that fail open buys
nothing.

**P3 — Workspace + Membership tables.** New models in `db.py` following the
existing `_sync_columns` / `init_db` auto-migration pattern. Backfill one
workspace per user plus an owner membership, then repoint
`SavedPersona.owner_id` to `workspace_id`. `_persona_owner`, `_can_edit_persona`
and `owned_slugs()` are the only three read points that change. Add an email
invitation flow. Re-key `usage_counters` from user to workspace here.

**P4 — seats and role checks.** Enforce the `seats` capability at invitation
time. Add `require_role(...)` next to `require_capability(...)`. Extend the
`.admin-only` / `body.is-admin` pattern with `data-role` so the dashboard hides
what the seat cannot do. **Agency's marketing depends on this** (section 2).

---

## 5. Two features that cannot be sold as written

Both were candidate Agency bullets. Both fail on inspection; recorded here so
they are not re-proposed.

**Programmatic API (`/api/v1/chat`).** Lets an external app POST a message and
get the persona's reply without the chat UI. But `_valid_api_key()` reads one
global comma-separated `API_KEYS` env var shared by all customers, **and returns
`True` for anyone when the var is unset**. No per-customer keys, no attribution,
no revocation, no metering. Prerequisite: an `ApiKey` table keyed to the
workspace (P3). Sell it after that, not before.

**BYO Gemini key (`/api/config/gemini-key`).** Intended to let a customer bill
AI calls to their own Google account. In practice it calls
`update_env_var('GEMINI_API_KEY', ...)`, sets `os.environ`, and re-runs
`init_gemini_client()` — which reassigns the **module-global** `client`. One
customer pasting a key therefore switches Gemini **for every tenant on the
server**. It is also rejected outright on Vercel (read-only filesystem). This is
a live defect, not a feature: fix in P2 by locking the route to operators. A
genuine per-workspace BYO key needs a per-request client, which is a much larger
change.

---

## Carried-forward blocker

Stripe is wired as one-off Checkout Sessions, not Subscriptions
(`_checkout_stripe`; the tier's `days` is the period). Seat-based pricing and
mid-cycle upgrades will force a move to real Stripe Subscriptions with
proration. Decide before P4, not during it.
