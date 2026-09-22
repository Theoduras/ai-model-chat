# Generation tokens — pricing options

Decision document for the Generation Studio billing unit. Nothing here is
implemented yet. Read sections 1–3, pick an option in section 4, answer the
three open questions in section 9.

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

| Platform | Entry plan | Unit | Per image | Per 5s clip |
|---|---|---|---|---|
| [Higgsfield](https://higgsfield.ai/pricing) | $19 → 270 credits | credits | ~2 cr (**~$0.14**) | 7–29 cr |
| [Candy AI](https://candyaiapp.com/pricing/) | $12.99/mo + token packs | tokens | ~2 tk (**~$0.20**) | 5–10 tk |
| [Kling](https://www.cloudzero.com/blog/kling-ai-pricing/) | $8.80 → 660 credits | credits, expire monthly | — | — |
| [Runway](https://www.cloudzero.com/blog/kling-ai-pricing/) | ~$15 → 625 credits | credits | — | Max $95 → 9,500 cr |
| [Leonardo](https://www.eesel.ai/blog/leonardo-ai-pricing) | $12 → 8,500 tokens | tokens | ~$0.035 | — |
| [SeaArt](https://www.tooljunction.io/ai-tools/seaart-ai) | $5.99 | "stamina" | ~12 cr/query | — |
| [Supercreator](https://ofm-tools.com/supercreator-review/) | $99/account | **+5% of AI-driven sales** | — | — |
| [Botly](https://www.topsocialtools.com/insights/getbotly/) | ~$129 | + usage fees | — | — |
| **Us, proposed** | €49 → 30 tokens | tokens, expire monthly | **€0.15** | €1.80 |
| **Us, today** | €49 → 600 credits | credits | ~€0.56 | ~€6.40 |

Three things fall out of this:

**The market has already split into low-number and high-number units.** The
platforms creators actually enjoy using — Higgsfield, Candy — charge *2 of
something* per image. The ones people complain about — Kling, Runway, and us —
charge hundreds. Low numbers are not a cosmetic choice; they are what makes a
price legible at the moment of spending.

**Leonardo's $0.035 is not a comparable number.** That is SDXL-class output with
no identity lock. Our pipeline conditions on an approved vault photo *and*
faceswaps from the same photo, on Seedream, which bills us $0.04 before we add
anything. We cannot and should not try to reach $0.035.

**Supercreator prices on a completely different axis:** $99 flat plus 5% of
AI-driven net sales. Worth knowing it exists, but it needs attributable revenue
per message, which we do not have. Not proposed here.

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

Monthly allowance: **10 / 30 / 125 / 375** (Demo / Starter / Pro / Agency).

### Option C — graded, 2 tokens = 1 photo

Identical shape, doubled. Matches the Candy AI and Higgsfield convention
exactly, so a creator arriving from either reads our prices without conversion.

| Model | 2k | 4k | | Model | 720p 5s | 1080p 5s |
|---|---|---|---|---|---|---|
| Seedream 4.5 | **2** | 2 | | Wan 2.5 | **23** | 57 |
| Nano Banana 2 | 6 | 11 | | Wan 2.7 | 26 | 63 |
| Nano Banana Pro | 7 | 14 | | Seedance 2.5 | 30 | 75 |

Audio: 5 tokens. Allowance: **20 / 60 / 250 / 750**.

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
| Pro allowance | 125 | **125** | 250 |
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

## 6. The included allowance is now the problem

Re-denominating exposes something the old scale was hiding. Here is what each
plan actually includes today:

| Plan | Price | Credits now | = Tokens | = Photos | = Clips | Costs us |
|---|---|---|---|---|---|---|
| Demo | free | 200 | 10 | 10 | 0 | $0.40 |
| Starter | €49 | 600 | 30 | 30 | **2** | $1.20 |
| Pro | €149 | 2,500 | 125 | 125 | **10** | $5.00 |
| Agency | €349 | 7,500 | 375 | 375 | **31** | $15.00 |

**Starter is €49 a month for two video clips.** Agency is €349 for thirty-one.
For a product whose entire pitch is "generate content your fans will pay for",
these allowances are not credible — and at the old €0.56-per-photo pricing they
at least *sounded* substantial. At €0.15 they are visibly thin: Pro's 125 tokens
are about €19 of value inside a €149 plan.

Raising them is cheap, because generation costs us very little:

| Plan | Proposed | = Photos | = Clips | Costs us/mo | % of plan price |
|---|---|---|---|---|---|
| Demo | 25 | 25 | 2 | $1.00 | — |
| Starter | 100 | 100 | 8 | $4.00 | 8% |
| Pro | 350 | 350 | 29 | $14.00 | 9% |
| Agency | 1,000 | 1,000 | 83 | $40.00 | 11% |

That is a 3x increase in what every plan includes for single-digit percentages
of revenue, and it restores the upgrade ladder that dropping the discount bands
takes away. **Recommended.**

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

## 9. Decisions needed

1. **Which denomination?** A is ruled out by arithmetic. Recommend **B** — one
   token, one photo, the sentence that makes the system explainable.
2. **Raise the included allowances?** Recommend **yes**: 25 / 100 / 350 / 1,000.
   Starter at two clips a month is not a sellable entry tier.
3. **Drop the tier discount bands?** Recommend **yes** — the size ladder already
   discounts 40%, and stacking tier bands on top would reach ~1.7x cost.

Once these are settled the implementation is mechanical: `credits.py` and its
tests first (pure, no I/O, the floor assertions prove the numbers before
anything else moves), then the ledger, then the routes and checkout, then the
studio's own labels.
