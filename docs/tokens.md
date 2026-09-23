# Generation tokens — pricing

The Generation Studio billing unit, as shipped. **Option B, raised allowances
and no tier discount bands** were chosen; sections 4 and 9 keep the options and
the reasoning so a settled question is not re-opened.

Implemented in `credits.py`, `db.py`, `app.py` and `studio.html`; the numbers
below are the ones the code asserts at import. Generation itself is still
admin-only — this prices the feature, it does not open it.

---

## 1. What is wrong with credits

A photo costs **20 credits**. A five-second clip costs **230**. Starter includes
**600 a month**, Pro 2,500, Agency 7,500.

None of those numbers mean anything to a creator. The scale exists because
`CREDIT_COST_USD = 0.002` made one credit a fixed slice of *provider cost* —
excellent for margin discipline, useless as a thing a person reads before
pressing Generate. A creator cannot tell whether 230 is a lot.

Two changes:

1. **Re-denominate to tokens at human scale.** One token is about one photo.
   A clip is about twelve. A month's allowance is a two- or three-digit number.
2. **Cut the retail price from ~15x provider cost to 2.3–3.8x**, on a ladder
   that discounts 40% by volume. A photo is **€0.15** at the smallest pack and
   **€0.09** at the largest — at or below every comparable platform.

The margin discipline is kept exactly: a token is still a fixed slice of
provider cost, the slice is just twenty times bigger.

---

## 2. What the market charges

The comparison set is deliberately two: **Higgsfield**, the retail product a
creator would otherwise buy, and **WaveSpeed**, an inference provider we could
buy from instead of Runware. One is a competitor for the customer, the other a
competitor for our cost base.

| | photo | 5s 720p clip | NSFW | our cost |
|---|---|---|---|---|
| **Us** | €0.15 → €0.09 | €1.80 → €1.08 | **yes, identity-locked** | $0.040 / $0.504 |
| Higgsfield, $19 tier | $0.141 | $0.56 | no | — |
| Higgsfield, $99 tier | $0.066 | $0.26 | no | — |
| WaveSpeed (a provider) | $0.040 Seedream | $1.80 Seedance 720p | n/a | it *is* cost |

Our range is smallest pack to largest. Higgsfield's is entry tier to top tier —
its credits get cheaper the more you subscribe, the same shape as our pack
ladder.

### What €130 buys

| | tokens or credits | photos | 5s clips |
|---|---|---|---|
| **Us** (1,000-token pack) | 1,000 | **1,000** | 83 |
| Higgsfield at its $19 rate | ~1,995 | 998 | 249 |
| Higgsfield at its $99 rate | ~4,255 | 2,127 | 532 |

**On photos we are level with Higgsfield's entry tier and behind its top tier.
On video we are 3–6x behind, and this is a cost problem, not a margin one.**
Runware bills us $0.09076 a second for Wan 2.5; fal.ai lists $0.05 and EvoLink
$0.0708. Higgsfield's top tier sells a 5s Wan 2.7 clip for **$0.264 — less than
the $0.504 it costs us**. No pricing decision closes that gap; a cheaper
provider might. See section 8.

WaveSpeed is in the table as the provider alternative and does not beat Runware
where it matters: Seedream is the same $0.04, and its Seedance 2.5 at 720p is
$1.80 per 5s against Runware's $0.60. Its cheap rung is *Wan 2.2 Ultra Fast* at
$0.01 a second, which is a different, lower-quality model rather than the same
clip for less.

### The one thing neither of them sells

Higgsfield refuses NSFW outright, and WaveSpeed is an API you would have to
build this on top of. **Nothing in the comparison set generates explicit
content of a specific creator's face**, conditioned on an approved vault photo
and faceswapped from the same photo. That is the product; the per-photo price
is not what a creator is choosing between.

---

## 3. What a token costs us

Unchanged from today, just re-pegged. `TOKEN_COST_USD = 0.04` — what one token
is allowed to cost us at the provider. Every generation is priced
`ceil(provider_cost / TOKEN_COST_USD)`, so rounding always favours us.

Measured provider costs (from live bills, in `credits.PROVIDER_COST_USD`):

| | cost |
|---|---|
| Seedream 4.5 / 5.0 Pro, any size | $0.04 |
| Nano Banana 2, 2k | $0.10255 |
| Nano Banana Pro, 2k | $0.138 |
| Wan 2.5, per second 720p | $0.09076 |
| Wan 2.7, per second 720p | $0.10076 |

Everything else in the table is a deliberate over-estimate. **That mattered
little at 15x margin and matters a great deal at 2.3–3.8x** — see section 8.

---

## 4. The three options

### Option A — flat rate

*One token per photo, ten per clip, whatever model you pick.* The clearest
pricing a person could be given.

**It does not work.** At €0.15 a token — the smallest pack's rate — here is what
a flat rate actually earns against what it costs:

| Generation | Costs us | Flat price | Earns | Multiple | |
|---|---|---|---|---|---|
| Photo — Seedream 2k/4k | $0.040 | 1 token | $0.153 | 3.82x | OK |
| Photo — Nano Banana 2, 2k | $0.103 | 1 token | $0.153 | 1.49x | under floor |
| Photo — Nano Banana Pro, 2k | $0.138 | 1 token | $0.153 | 1.11x | under floor |
| Photo — Nano Banana Pro, 4k | $0.276 | 1 token | $0.153 | 0.55x | **loses money** |
| Clip — Wan 2.5, 5s 720p | $0.454 | 10 tokens | $1.530 | 3.37x | OK |
| Clip — Wan 2.7, 5s 720p | $0.504 | 10 tokens | $1.530 | 3.04x | OK |
| Clip — Wan 2.5, 5s 1080p | $1.135 | 10 tokens | $1.530 | 1.35x | under floor |
| Clip — Seedance, 10s 1080p | $3.000 | 10 tokens | $1.530 | 0.51x | **loses money** |

To clear the 2.25x floor flat we would need a **5-token photo** and a
**9-token clip** — at which point it is no longer flat in any useful sense. The
only way to keep "1 token = 1 photo" flat is to **remove every image model except
Seedream and cap video at 720p**, which throws away Nano Banana Pro and the whole
premium tier.

Recorded so it is not re-proposed. At 15x margin this worked; at these prices it cannot.

### Option B — graded, 1 token ≈ 1 photo *(recommended)*

A standard photo is 1 token. Better models cost more, in single digits.

**Images**

| Model | 2k | 4k |
|---|---|---|
| Seedream 4.5 | **1** | **1** |
| Seedream 5.0 Pro | 1 | 1 |
| Nano Banana 2 | 3 | 6 |
| Nano Banana Pro | 4 | 7 |

**Video** (whole job rounded up, so any length is priced)

| Model | 720p 3s | 5s | 10s | 1080p 3s | 5s | 10s |
|---|---|---|---|---|---|---|
| Wan 2.5 | 7 | **12** | 23 | 18 | 29 | 57 |
| Wan 2.7 | 8 | 13 | 26 | 19 | 32 | 63 |
| Seedance 2.5 | 9 | 15 | 30 | 23 | 38 | 75 |
| Face swap | 9 | 15 | 30 | 23 | 38 | 75 |

Audio add-on: **3 tokens** a clip. Upscale and the NSFW check disappear as
separate line items — at this scale they round to 1 token, which would be a 10x
overcharge on a $0.004 operation, so they fold into the base price.

Monthly allowance, as shipped: **150 / 800 / 2,500** (Starter / Pro / Agency).
Demo keeps unlimited generation, so it has no allowance to state.

### Option C — graded, 2 tokens = 1 photo

Identical shape, doubled. Matches the Candy AI and Higgsfield convention
exactly, so a creator arriving from either reads our prices without conversion.

| Model | 2k | 4k | | Model | 720p 5s | 1080p 5s |
|---|---|---|---|---|---|---|
| Seedream 4.5 | **2** | 2 | | Wan 2.5 | **23** | 57 |
| Nano Banana 2 | 6 | 11 | | Wan 2.7 | 26 | 63 |
| Nano Banana Pro | 7 | 14 | | Seedance 2.5 | 30 | 75 |

Audio: 5 tokens. Allowance would have been **200 / 700 / 2,000**.

The case for C is finer grading — the gap between Seedream and Nano Banana Pro
is 2→7 rather than 1→4, so a price difference is visible without being
dramatic. The case against is that every number is twice as big for no extra
information, and "1 token = 1 photo" is the single sentence that makes the whole
system explainable.

### Side by side

| | A — flat | B — 1 tk/photo | C — 2 tk/photo |
|---|---|---|---|
| Standard photo | 1 | **1** | 2 |
| Premium photo 4k | 1 | 7 | 14 |
| 5s 720p clip | 10 | **12** | 23 |
| Pro allowance | 350 | **350** | 700 |
| Explainable in one line | yes | yes | nearly |
| Clears the 2.25x margin floor | **no** | yes | yes |
| Needs models removed | **yes** | no | no |

---

## 5. What a token costs, in euro, dollars and pounds

**Euro is the base currency.** Prices are set in euro — matching the existing
€49 / €149 / €349 plans — and the dollar and pound ladders are hand-set round
numbers alongside, not live conversions. A pack never costs €13.47 and never
moves because the exchange rate did.

The floor is still checked in dollars, because the providers bill us in
dollars. Each euro and pound price is converted at a **conservative** reference
rate (€1 = $1.02, £1 = $1.18) and must still clear 3.0x. Using a pessimistic
rate means an ordinary FX swing cannot quietly push a price under cost.

### The ladder — one price for everyone

| Tokens | EUR | USD | GBP | €/token | margin | buys |
|---|---|---|---|---|---|---|
| 100 | **€15** | $16 | £13 | €0.150 | 3.82x | 100 photos / 8 clips |
| 500 | **€70** | $75 | £62 | €0.140 | 3.57x | 500 photos / 41 clips |
| 1,000 | **€130** | $139 | £115 | €0.130 | 3.31x | 1,000 photos / 83 clips |
| 2,000 | **€220** | $235 | £195 | €0.110 | 2.81x | 2,000 photos / 166 clips |
| 5,000 | **€450** | $479 | £395 | €0.090 | 2.29x | 5,000 photos / 416 clips |

A photo runs **€0.15 down to €0.09** and a five-second clip **€1.80 down to
€1.08**, depending on pack size. Generation prices quoted in cash use the
smallest pack's rate, because that is the marginal price of buying more — the
same reason `credits.credit_rate_usd()` reads `PACK_SIZES[0]` today.

**`MIN_MARGIN_MULTIPLE` moves 4.0 → 2.25.** The two largest packs sell at 2.81x
and 2.29x, under the 3.0x the smaller ones clear. That is the volume discount
working as intended — gross margin is still 64% and 56% — but the floor is a
build-gate assertion that runs at import, so it must come down or nothing starts.

### The ladder already is the discount — drop the tier bands

Today Pro and Agency buy credits ~13% and ~27% cheaper than Starter. This ladder
already falls 40% from smallest pack to largest, which is the same incentive
bought by volume rather than by subscription tier. Stacking the tier bands on top
would put the 5,000 pack at roughly **1.7x cost**.

**Recommendation: drop the bands.** Price on size alone, and let the upgrade
incentive live in the included allowance instead — which is what section 6 is
about.

---

## 6. The allowance, raised

Re-denominating exposed something the old scale was hiding. What each plan used
to include, read in clips rather than credits:

| Plan | Price | Was | = Photos | = Clips | Cost us |
|---|---|---|---|---|---|
| Starter | €49 | 600 credits | 30 | **2** | $1.20 |
| Pro | €149 | 2,500 credits | 125 | **10** | $5.00 |
| Agency | €349 | 7,500 credits | 375 | **31** | $15.00 |

**Starter was €49 a month for two video clips.** For a product whose entire
pitch is "generate content your fans will pay for" that is not a credible entry
tier — and at the old €0.56-per-photo pricing it at least *sounded* substantial.

Shipped instead, roughly 3x across the board:

| Plan | Now | = Photos | = Clips | Costs us/mo | % of plan price |
|---|---|---|---|---|---|
| Starter | **100** | 100 | 8 | $4.00 | 8% |
| Pro | **350** | 350 | 29 | $14.00 | 9% |
| Agency | **1,000** | 1,000 | 83 | $40.00 | 11% |

Single-digit percentages of revenue, and it restores the upgrade ladder that
dropping the pack discount bands gives up. The numbers live once, in
`credits.MONTHLY_TOKENS`; the tier bullets and the enforced capability both read
them from there, so a marketing string cannot drift from what the plan allows —
`test_tokens.py` asserts it.

---

## 7. What is not changing

- **Monthly allowance still expires monthly.** No rollover. Bought tokens never
  expire. Same as Kling and Higgsfield, same as the code does today.
- **Generation stays admin-only.** `/studio` and every `/api/generate/*` route
  keep `_require_admin` and keep 404-ing rather than 403-ing. Token checkout
  stays shut too — nobody should buy tokens for a feature that is not offered
  yet. This document prices the feature; it does not open it.
- **The ledger stays append-only.** Balance is the sum of rows, never a counter,
  because people will buy these. A spend still drains the expiring allowance
  before anything purchased; a refund still returns tokens to the bucket they
  left.
- **Pricing stays pegged to provider cost.** One number, `TOKEN_COST_USD`.
  A new model is a table entry, not a pricing decision.

---

## 8. The risk this creates

Most of the price table is not measured. From `credits.py`'s own notes, the
following are deliberate over-estimates carried because *a guess that is too low
loses money on every generation and nothing reports it*:

- **Seedance 2.5** — refused the probe at ByteDance's moderation end before
  billing anything. Entire row is a guess.
- **Every 4k image rung** — priced at twice 2k because the 4k result came back
  before the provider reported a cost.
- **480p and 1080p video** — only 720p was measured. 480p is charged at the
  720p rate; 1080p keeps the old table's 2.5x shape.
- **The swap models** (`p-video-replace`, `ml-face-swap`) — unmeasured.
- **The audio add-on** — $0.10 assumed, and per `CLAUDE.md` none of the audio
  model ids or field names have been verified against the live catalogue.

At 15x margin a 2x over-estimate was invisible. **At 2.3x on the largest pack it is
the difference
between a healthy margin and selling under cost.** Before this ships, measure at
minimum: Seedance at 720p, one 4k image on each Google model, and one 1080p clip.
Run `imagegen.search_models('audio')` against a live Runware key and correct the
audio defaults.

The build gate protects us either way — `_assert_generation_floor()` walks the
entire job matrix at import and refuses to start if any rung would sell under
cost — but it can only check the numbers it is given.

---

## 9. Decisions — settled

1. **Which denomination?** → **B**. One token, one photo.
2. **Raise the included allowances?** → **yes**: 100 / 350 / 1,000 for
   Starter / Pro / Agency. Demo keeps unlimited generation, so it has no
   allowance to state.
3. **Drop the tier discount bands?** → **yes**. The size ladder already
   discounts 40%.

All three are implemented. What remains open is the video cost gap in section 2:
measure Wan on fal.ai and WaveSpeed against Runware's $0.09076 a second, and if
a cheaper route holds, clips fall below 12 tokens at the same margin.

Once these are settled the implementation is mechanical: `credits.py` and its
tests first (pure, no I/O, the floor assertions prove the numbers before
anything else moves), then the ledger, then the routes and checkout, then the
studio's own labels.

> **Free plan:** a one-time grant of 15 tokens (`credits.FREE_CREDITS`), never refilled. `MONTHLY_TOKENS["free"]` is 0.
