"""Token pricing for AI image and video generation.

Every retail price in this file is derived from one number: what a generation
costs us at the provider. A token is a fixed slice of that cost, so gross margin
is the same whatever the creator generates -- they cannot arbitrage us by
favouring an expensive model, and adding a model is a table entry rather than a
pricing decision. If a provider raises prices, TOKEN_COST_USD is the only number
to move; every pack rate follows it and the margin floor re-asserts at import.

The unit used to be a "credit" worth $0.002, which made a photo 20 and a clip
230. Those numbers were unreadable: nobody can tell whether 230 is a lot. A
token is the same idea at a human scale -- **one token is about one photo** --
so the slice is twenty times bigger and every price the studio shows is a number
a creator can hold in their head.
"""
import math
import os

# The job table, and with it the aspect and audio vocabularies, live in
# imagegen beside the per-model payload differences they describe. Imported
# rather than restated: two lists of what a job may be is how a picker starts
# offering something nothing can price. This costs nothing at import -- the
# provider stack itself (requests, the endpoints) is loaded lazily inside
# imagegen's own functions, so `python test_tokens.py` still needs no network.
import imagegen as _IG

# What one token is allowed to cost us at the provider, in USD. Pegged to a
# standard Seedream still, which is why one token buys one photo. Every
# generation is priced at ceil(provider_cost / TOKEN_COST_USD), so rounding
# always favours us.
TOKEN_COST_USD = 0.04

# No pack may sell tokens for less than this multiple of their cost. Asserted at
# import, so a discount that would lose money fails the build rather than a
# month of invoices.
#
# It was 4.0 when the ladder was four packs with a shallow curve. The ladder now
# runs from EUR 0.15 a token down to EUR 0.09 -- a 40% volume discount -- so the
# two largest packs land at 2.81x and 2.29x. That is the discount working as
# intended (64% and 56% gross margin), not a mistake, but the floor has to admit
# it or nothing starts.
MIN_MARGIN_MULTIPLE = 2.25

IMAGE_MODELS = ('seedream-4-5', 'seedream-5-pro',
                'nano-banana-pro', 'nano-banana-2')
RESOLUTIONS = ('2k', '4k')
VIDEO_RESOLUTIONS = ('480p', '720p', '1080p')
# The presets the picker offers. Any whole number in VIDEO_SECONDS_RANGE is
# priced and accepted -- these are the three worth one click.
VIDEO_DURATIONS = (3, 5, 10)
VIDEO_SECONDS_MIN = 2

DEFAULT_IMAGE_MODEL = 'seedream-4-5'
DEFAULT_RESOLUTION = '2k'
DEFAULT_VIDEO_RESOLUTION = '720p'
DEFAULT_VIDEO_DURATION = 5

VIDEO_MODELS = ('wan-2-5', 'wan-2-7', 'seedance-2-5', 'seedance-2-0',
                'seedance-2-0-fast', 'minimax-h3', 'minimax-h3-fast', 'wan-3-0')
DEFAULT_VIDEO_MODEL = 'wan-2-5'

# Wan 2.7 is the only video model that takes an input clip, so a face swap into
# an uploaded video is always priced and run on it whatever the picker says.
VIDEO_EDIT_MODEL = 'wan-2-7'

# Wan 2.7's own ceiling. A longer upload cannot be swapped, so it is refused at
# the upload rather than truncated after it is paid for.
VIDEO_MAX_SECONDS = 15

# ── What the provider actually bills us, in USD ───────────────────────────────
# This is the source of every number in this file: the token tables below are
# derived from these, never hand-written beside them. A second hand-maintained
# table is exactly how a clip came to be sold at 30 credits while costing 46.
#
# `PROVIDER_COST_MEASURED` names the rungs that came off a live bill. Everything
# else is an over-estimate: a guess that is too low loses money quietly, so the
# guesses are deliberately high. Measure one and move its key into the measured
# set. At the old 15x margin a 2x over-estimate was invisible; at 2.3x on the
# largest pack these are load-bearing.
PROVIDER_COST_USD = {
    'seedream-4-5':    {'2k': 0.04, '4k': 0.04},
    'seedream-5-pro':  {'2k': 0.04, '4k': 0.04},
    'nano-banana-2':   {'2k': 0.10255, '4k': 0.2051},
    'nano-banana-pro': {'2k': 0.138, '4k': 0.276},
}

VIDEO_COST_USD_PER_SECOND = {
    'wan-2-5':      {'480p': 0.09076, '720p': 0.09076, '1080p': 0.2269},
    'wan-2-7':      {'480p': 0.10076, '720p': 0.10076, '1080p': 0.2519},
    'seedance-2-5': {'480p': 0.12, '720p': 0.12, '1080p': 0.30},
    'p-video-replace': {'480p': 0.12, '720p': 0.12, '1080p': 0.30},
    'ml-face-swap': {'480p': 0.12, '720p': 0.12, '1080p': 0.30},
    # Runware's published list prices, unmeasured. A rung a model does not
    # serve carries its dearest real one: video_size snaps it to a rung it
    # does serve, and the quote must never have been below that.
    'p-video-animate': {'480p': 0.06, '720p': 0.03, '1080p': 0.06},
    'seedance-2-0': {'480p': 0.07, '720p': 0.16, '1080p': 0.40},
    'seedance-2-0-fast': {'480p': 0.06, '720p': 0.13, '1080p': 0.13},
    'minimax-h3': {'480p': 0.13, '720p': 0.08, '1080p': 0.13},
    'minimax-h3-fast': {'480p': 0.046, '720p': 0.046, '1080p': 0.046},
    'wan-3-0': {'480p': 0.05, '720p': 0.10, '1080p': 0.20},
    # Kling motion control runs at 1080p only; every rung snaps there.
    'kling-2-6-mc': {'480p': 0.07, '720p': 0.07, '1080p': 0.07},
    'kling-3-0-mc': {'480p': 0.17, '720p': 0.17, '1080p': 0.17},
}

PROVIDER_COST_MEASURED = {
    'images': {'seedream-4-5': ('2k', '4k'),
               'nano-banana-2': ('2k',),
               'nano-banana-pro': ('2k',)},
    'videos': {'wan-2-5': ('720p',), 'wan-2-7': ('720p',)},
}

# What an add-on costs us, where it costs anything. Unmeasured and therefore
# deliberately high, the same as every other guess in this file.
ADDON_COST_USD = {
    'audio': 0.10,
}


def credits_for_cost(usd):
    """The token price of something that costs us `usd`, rounded up."""
    return max(1, int(math.ceil(float(usd) / TOKEN_COST_USD)))


# Tokens per generation, by model and resolution, derived from the bill above.
# Seedream bills $0.04 a still whatever the size, so 2k and 4k both land on one
# token -- that flat rate is what makes "one token, one photo" true.
IMAGE_PRICES = {model: {res: credits_for_cost(cost)
                        for res, cost in rows.items()}
                for model, rows in PROVIDER_COST_USD.items()}

# Video is priced per second, but **rounded once for the whole job**, not per
# second. At this scale a per-second integer rate would overcharge badly: Wan
# 2.5 at 720p is 2.269 tokens a second, and rounding that up to 3 would sell a
# five-second clip at 15 tokens instead of 12. So the rate stays fractional and
# `video_price` does the single ceil.
VIDEO_RATE_PER_SECOND = {
    model: {res: cost / TOKEN_COST_USD for res, cost in rows.items()}
    for model, rows in VIDEO_COST_USD_PER_SECOND.items()
}

# The five video jobs, mirrored from imagegen so the picker and the price table
# read one list. A job names its own models; a model is never the question a
# creator is asked.
JOB_MODELS = {job: tuple(row['models']) for job, row in _IG.VIDEO_JOBS.items()}
JOB_KINDS = {job: _IG.job_kind(job) for job in _IG.VIDEO_JOBS}
JOB_RATINGS = {job: tuple(_IG.job_ratings(job)) for job in _IG.VIDEO_JOBS}
JOB_LABELS = {job: row.get('label') or job.title()
              for job, row in _IG.VIDEO_JOBS.items()}
JOB_NOTES = {job: row.get('note') or ''
             for job, row in _IG.VIDEO_JOBS.items()}
JOB_NEEDS = {job: tuple(row.get('needs') or ())
             for job, row in _IG.VIDEO_JOBS.items()}
DEFAULT_JOB = 'reel'

# Frame shape is free: a 9:16 reel and a 16:9 cut of the same scene run the same
# pixels through the same model, so the picker changes what a clip looks like
# without changing what it costs.
ASPECTS = tuple(_IG.ASPECTS)
DEFAULT_ASPECT = _IG.DEFAULT_ASPECT
AUDIO_MODES = tuple(_IG.AUDIO_MODES)
EXTEND_MODES = tuple(_IG.EXTEND_MODES)

# The models a swap may run on, read off the job table rather than restated.
# They do opposite things with the clip they are given: replace keeps the video
# and changes who is in it, Wan 2.7 regenerates the video from her references in
# a similar motion. Both are offered because only the operator can say which one
# a given clip wants.
SWAP_MODELS = JOB_MODELS['swap']
DEFAULT_SWAP_MODEL = SWAP_MODELS[0]
# Where an explicit persona goes: the only model that both replaces rather than
# regenerates and serves explicit work. It runs on ModelsLab, not Runware --
# see imagegen.provider_name_for.
EXPLICIT_SWAP_MODEL = 'ml-face-swap'

# The legacy Google/Imagen path is priced from the same peg (~$0.02 a call), so
# it cannot be used as a free way around the token system.
GOOGLE_IMAGE_TOKENS = credits_for_cost(0.02)

# Add-ons. Identity is not one of them: Seedream conditions on reference images
# inside the one call it already charges for, so holding her face costs nothing
# on top.
#
# Upscale and the NSFW check used to be line items at 2 and 1 credits. At this
# scale each would round to a whole token -- a 10x overcharge on a $0.004
# operation -- so they are folded into the base price and are no longer sold
# separately. Audio survives because it is a real second pass: the provider
# bills a video-to-audio call flat, and a model that emits sound in the same
# pass bills nothing extra at all. Priced as if it always costs us the dearer of
# the two, because the cheaper case cannot be told apart at quote time.
ADDON_PRICES = {
    'audio': credits_for_cost(ADDON_COST_USD['audio']),
}


def image_cost_usd(model, resolution, batch=1):
    row = PROVIDER_COST_USD.get(model) or {}
    per = row.get(resolution)
    return None if per is None else per * max(1, int(batch))


def video_cost_usd(model, resolution, seconds):
    row = VIDEO_COST_USD_PER_SECOND.get(model) or {}
    per = row.get(resolution)
    return None if per is None else per * max(1, int(seconds or 0))


def cost_table():
    """The provider-cost menu. Admin only: it is our margin written out."""
    return {'images': PROVIDER_COST_USD,
            'video_per_second': VIDEO_COST_USD_PER_SECOND,
            'addons': ADDON_COST_USD,
            'measured': PROVIDER_COST_MEASURED}


# Human labels for the model picker. Closed video models (Kling, Veo, Seedance)
# are deliberately absent everywhere in this file: they are moderated and cannot
# serve this feature, so nothing may offer them for an NSFW slot.
MODEL_LABELS = {
    'seedream-4-5': 'Seedream 4.5',
    'seedream-5-pro': 'Seedream 5.0 Pro',
    'nano-banana-pro': 'Nano Banana Pro',
    'nano-banana-2': 'Nano Banana 2',
    'wan-2-5': 'Wan 2.5',
    'wan-2-7': 'Wan 2.7',
    'seedance-2-5': 'Seedance 2.5',
    'p-video-replace': 'Replace her in the clip',
    'ml-face-swap': 'Replace her in the clip — explicit',
    'p-video-animate': 'Her photo performs the clip',
    'seedance-2-0': 'Seedance 2.0',
    'seedance-2-0-fast': 'Seedance 2.0 Fast',
    'minimax-h3': 'MiniMax H3',
    'minimax-h3-fast': 'MiniMax H3 Fast',
    'wan-3-0': 'Wan 3.0',
    'kling-2-6-mc': 'Kling 2.6 motion control',
    'kling-3-0-mc': 'Kling 3.0 motion control',
}

# Which ratings each model actually serves, measured against the provider rather
# than assumed. The picker filters on this: a model that would be moved to
# another one at submit should never have been offered in the first place,
# because the creator reads the swap as the model having lied.
#
# Only Seedream 4.5 serves explicit work. 5.0 Pro refuses it at ByteDance's end,
# and the Google models refuse it at any safety level.
MODEL_RATINGS = {
    'seedream-4-5': ('sfw', 'nsfw'),
    'seedream-5-pro': ('sfw',),
    'nano-banana-pro': ('sfw',),
    'nano-banana-2': ('sfw',),
}

# Video, measured the same way. Wan 2.7 served the explicit probe; Seedance 2.5
# refused it at ByteDance's end. Wan 2.5 has not been probed explicit, so it is
# offered safe-for-work only rather than assumed permissive.
VIDEO_MODEL_RATINGS = {
    'wan-2-5': ('sfw',),
    'wan-2-7': ('sfw', 'nsfw'),
    'seedance-2-5': ('sfw',),
    # Settled by the provider, not assumed: an explicit clip came back as a
    # crash whose own traceback could not be deserialized because the safety
    # module raised it. The crash is the refusal, so this model is safe work.
    'p-video-replace': ('sfw',),
    # Mainstream moderated providers, never probed explicit.
    'p-video-animate': ('sfw',),
    'seedance-2-0': ('sfw',),
    'seedance-2-0-fast': ('sfw',),
    'minimax-h3': ('sfw',),
    'minimax-h3-fast': ('sfw',),
    'wan-3-0': ('sfw',),
    'kling-2-6-mc': ('sfw',),
    'kling-3-0-mc': ('sfw',),
    # ModelsLab's face swap, not Runware -- an uncensored provider running an
    # actual swap rather than a regeneration. Runware carries no explicit
    # replace model at all (confirmed against its own catalogue), so this is the
    # one place a swap job leaves the default provider.
    'ml-face-swap': ('sfw', 'nsfw'),
}


def models_for_rating(rating):
    want = 'nsfw' if rating == 'nsfw' else 'sfw'
    return [m for m in IMAGE_MODELS if want in MODEL_RATINGS.get(m, ('sfw',))]


def video_models_for_rating(rating):
    want = 'nsfw' if rating == 'nsfw' else 'sfw'
    return [m for m in VIDEO_MODELS
            if want in VIDEO_MODEL_RATINGS.get(m, ('sfw',))]


# ── Currency ──────────────────────────────────────────────────────────────────
# Euro is the base: prices are *set* in euro, matching the existing plan prices,
# and the dollar and pound ladders are hand-set round points alongside rather
# than conversions of it. A fixed price point never costs EUR 13.47 and never
# moves because the exchange rate did.
BASE_CURRENCY = 'eur'
CURRENCIES = ('eur', 'usd', 'gbp')
CURRENCY_SYMBOLS = {'eur': '€', 'usd': '$', 'gbp': '£'}

# USD per unit, deliberately pessimistic rather than market. Used **only** by
# the floor assertion -- provider bills arrive in dollars, so a euro price can
# only be proved safe by converting it -- and never to compute a displayed
# price. A pessimistic rate means an ordinary FX swing cannot quietly push a
# pack under cost.
REFERENCE_RATES = {'eur': 1.02, 'usd': 1.0, 'gbp': 1.18}


def currency_of(name):
    name = (name or '').strip().lower()
    return name if name in CURRENCIES else BASE_CURRENCY


def symbol_for(currency):
    return CURRENCY_SYMBOLS[currency_of(currency)]


# Included allowance per calendar month, keyed on the tier keys in app.TIERS.
# Read in clips rather than tokens, the old allowances were indefensible:
# Starter was EUR 49 a month for two video clips. Generation costs us very
# little, so tripling them costs 8-11% of plan revenue and buys back the upgrade
# ladder that dropping the pack discount bands gives up.
# The Demo plan is deliberately absent: it carries unlimited generation in
# app._BASE_TIERS, so it has no allowance to state. These are the numbers
# app.py's tier capabilities are built from -- stated once, here, so a bullet
# and the cap it describes cannot drift apart.
MONTHLY_TOKENS = {
    'demo': 25,
    'starter': 100,
    'pro': 350,
    'agency': 1000,
}
DEFAULT_MONTHLY_TOKENS = 100

# Top-up packs. One price for everyone -- there are no tier bands. The ladder
# already falls 40% from the smallest pack to the largest, which is the same
# incentive bought by volume rather than by subscription tier; stacking the old
# Pro and Agency bands on top of it would put the 5,000 pack at roughly 1.7x
# cost.
PACK_SIZES = (100, 500, 1000, 2000, 5000)

PACK_PRICES = {
    100:   {'eur': 15,  'usd': 16,  'gbp': 13},
    500:   {'eur': 70,  'usd': 75,  'gbp': 62},
    1000:  {'eur': 130, 'usd': 139, 'gbp': 115},
    2000:  {'eur': 220, 'usd': 235, 'gbp': 195},
    5000:  {'eur': 450, 'usd': 479, 'gbp': 395},
}

# Stripe refuses a charge under these, whatever the price table says, so a pack
# priced below one is a pack that fails at the checkout page rather than in a
# test. https://docs.stripe.com/currencies -- "Minimum charge amount by currency".
STRIPE_MIN_CHARGE = {'eur': 0.50, 'usd': 0.50, 'gbp': 0.30}

# A deliberately underpriced pack, for proving the live Stripe path without
# spending 130 euro to do it. It is kept out of PACK_SIZES and PACK_PRICES on
# purpose: _assert_floor would refuse to import with it in the ladder, and that
# refusal is exactly the protection every real pack still needs. At these
# amounts it sells roughly 40 dollars of provider spend for half a euro, so the
# caller gates it on both an env flag and an admin session -- see
# app._token_test_pack_enabled.
TEST_PACK_ID = 'test'
TEST_PACK_TOKENS = 1000
TEST_PACK_PRICES = dict(STRIPE_MIN_CHARGE)


class PricingError(ValueError):
    """An unpriced generation was requested. Never let one reach the provider:
    a spec we cannot quote is a spec we cannot bill for."""


def image_price(model, resolution, addons=(), batch=1):
    try:
        base = IMAGE_PRICES[model][resolution]
    except KeyError:
        raise PricingError(f'no price for image {model!r} at {resolution!r}')
    per = base + sum(_addon(a) for a in addons)
    return per * max(1, int(batch))


def video_price(resolution, seconds, addons=(), model=None):
    """Per second at the model's rung, rounded up **once for the whole clip**.

    The single ceil is the point: at a token scale a per-second rounding would
    sell a five-second Wan 2.5 clip at 15 tokens where it costs 11.3."""
    model = model or DEFAULT_VIDEO_MODEL
    rates = VIDEO_RATE_PER_SECOND.get(model) or {}
    rate = rates.get(resolution)
    try:
        secs = int(seconds)
    except (ValueError, TypeError):
        secs = 0
    if not rate or not VIDEO_SECONDS_MIN <= secs <= VIDEO_MAX_SECONDS:
        raise PricingError(
            f'no price for video {model!r} {resolution!r} at {seconds!r}s')
    return max(1, int(math.ceil(rate * secs))) + sum(_addon(a) for a in addons)


def job_price(job, resolution, seconds, addons=(), model=None):
    """Tokens for one video job. The job decides which of the two shapes it is
    priced as: a swap is billed for the clip it was handed, everything else for
    the clip it will make. Aspect is not an argument because it is not a cost --
    the same pixels through the same model come out a different shape for the
    same money."""
    models = JOB_MODELS.get(job)
    if models is None:
        raise PricingError(f'unknown video job {job!r}')
    if model is not None and model not in models:
        raise PricingError(f'{model!r} does not serve the {job} job')
    model = model or models[0]
    if JOB_KINDS.get(job) == 'swap':
        # No default duration here, unlike a generated clip: a swap is billed
        # for the clip it was handed, so a spec carrying no measured duration is
        # one we cannot price rather than one we guess at.
        return swap_price(resolution, seconds, addons, model)
    return video_price(resolution, seconds or DEFAULT_VIDEO_DURATION,
                       addons, model)


def swap_price(resolution, seconds, addons=(), model=None):
    model = model if model in SWAP_MODELS else DEFAULT_SWAP_MODEL
    rates = VIDEO_RATE_PER_SECOND.get(model) or {}
    rate = rates.get(resolution)
    secs = int(seconds or 0)
    if not rate or not VIDEO_SECONDS_MIN <= secs <= VIDEO_MAX_SECONDS:
        raise PricingError(f'no price for a swap at {resolution!r} / {seconds!r}s')
    return max(1, int(math.ceil(rate * secs))) + sum(_addon(a) for a in addons)


def _addon(name):
    try:
        return ADDON_PRICES[name]
    except KeyError:
        raise PricingError(f'no price for add-on {name!r}')


def quote(spec):
    """Tokens for a generation spec, as the job API and the UI both see it.

    spec: {kind: image|video, model, resolution, seconds, batch, addons[]}
    """
    spec = spec or {}
    job = (spec.get('job') or '').strip().lower()
    addons = tuple(spec.get('addons') or ())
    # A job names its own models, so it is the stricter of the two routes and
    # takes precedence: a model that does not serve the job asked for is a spec
    # we refuse to quote rather than one we quietly reprice.
    if job:
        return job_price(job, spec.get('resolution') or DEFAULT_VIDEO_RESOLUTION,
                         spec.get('seconds'), addons, spec.get('model'))
    kind = spec.get('kind') or 'image'
    if kind == 'swap':
        return swap_price(spec.get('resolution') or DEFAULT_VIDEO_RESOLUTION,
                          spec.get('seconds'), addons, spec.get('model'))
    if kind == 'video':
        model = spec.get('model')
        if model not in VIDEO_RATE_PER_SECOND:
            model = DEFAULT_VIDEO_MODEL
        return video_price(spec.get('resolution') or DEFAULT_VIDEO_RESOLUTION,
                           spec.get('seconds') or DEFAULT_VIDEO_DURATION,
                           addons, model)
    if kind != 'image':
        raise PricingError(f'unknown generation kind {kind!r}')
    return image_price(spec.get('model') or DEFAULT_IMAGE_MODEL,
                       spec.get('resolution') or DEFAULT_RESOLUTION,
                       addons, spec.get('batch') or 1)


# Precomputed per-preset prices, for the UI's menu. Derived from video_price so
# the table and the charge can never disagree.
VIDEO_PRICES = {
    model: {res: {secs: video_price(res, secs, (), model)
                  for secs in VIDEO_DURATIONS}
            for res in rates}
    for model, rates in VIDEO_RATE_PER_SECOND.items()
}


def monthly_tokens(tier):
    return MONTHLY_TOKENS.get(tier or '', DEFAULT_MONTHLY_TOKENS)


def pack_price(size, currency=BASE_CURRENCY):
    """Price of a pack in one currency, or None if there is no such pack."""
    row = PACK_PRICES.get(int(size))
    return row.get(currency_of(currency)) if row else None


def _pack_row(size, price, cur, save_pct=0, test=False):
    """One row of the buy menu. Both the real ladder and the test pack render
    through here, so a card the checkout will honour and a card it will refuse
    can never end up different shapes on the page."""
    return {
        # What the client sends back to buy this row. A size is not enough:
        # the test pack grants 1,000 tokens, which is also a real pack.
        'id': TEST_PACK_ID if test else str(size),
        'tokens': size,
        'currency': cur,
        'symbol': symbol_for(cur),
        'price': price,
        'price_cents': int(round(price * 100)),
        'per_token': round(price / size, 5),
        'save_pct': save_pct,
        'test': test,
    }


def packs_for(currency=BASE_CURRENCY):
    """The buy-tokens menu, already resolved to one currency so the client needs
    no pricing logic of its own. There are no tier bands: everyone sees this."""
    cur = currency_of(currency)
    out = []
    for size in PACK_SIZES:
        price = PACK_PRICES[size][cur]
        full = PACK_PRICES[PACK_SIZES[0]][cur] / PACK_SIZES[0] * size
        out.append(_pack_row(
            size, price, cur,
            save_pct=int(round((1 - price / full) * 100)) if full > price else 0))
    return out


def test_pack_for(currency=BASE_CURRENCY):
    """The underpriced pack, for an admin proving the live Stripe path. It is
    not reachable through pack_price(), so the ordinary checkout cannot sell it
    however the size is spelled -- the caller has to ask for it by name."""
    cur = currency_of(currency)
    return _pack_row(TEST_PACK_TOKENS, TEST_PACK_PRICES[cur], cur, test=True)


def token_rate(currency=BASE_CURRENCY):
    """What one token costs in cash, for showing a price beside a token count.
    It is the smallest pack's rate -- the marginal price of buying more -- rather
    than an average over packs nobody bought, so "12 tokens" reads as what the
    next twelve would actually cost."""
    return pack_price(PACK_SIZES[0], currency) / PACK_SIZES[0]


def cash_for_tokens(tokens, currency=BASE_CURRENCY):
    """A token count priced in cash: {currency, symbol, amount}."""
    cur = currency_of(currency)
    return {'currency': cur, 'symbol': symbol_for(cur),
            'amount': round(max(0, int(tokens or 0)) * token_rate(cur), 2)}


def equivalents(tokens):
    """What a balance is worth in the two things creators actually make, for the
    'about 350 photos or 29 clips' line in the header."""
    photo = image_price(DEFAULT_IMAGE_MODEL, DEFAULT_RESOLUTION)
    clip = video_price(DEFAULT_VIDEO_RESOLUTION, DEFAULT_VIDEO_DURATION)
    n = max(0, int(tokens or 0))
    return {'photos': n // photo, 'clips': n // clip}


def _imagegen_takes_duration(model):
    try:
        return bool(_IG.takes_duration(model))
    except Exception:
        return True


def _imagegen_takes_aspect(model):
    try:
        return bool(_IG.takes_aspect(model))
    except Exception:
        return True


def _imagegen_durations(model):
    try:
        return _IG.model_durations(model)
    except Exception:
        return None


def _imagegen_rungs(model):
    try:
        return _IG.model_rungs(model) or list(VIDEO_RESOLUTIONS)
    except Exception:
        return list(VIDEO_RESOLUTIONS)


def model_caps(model):
    """What a model lets the operator choose. One that runs the length and the
    frame of the clip it was given has neither to offer, and a picker in front
    of it would be a control that changes nothing."""
    return {'duration': _imagegen_takes_duration(model),
            'aspect': _imagegen_takes_aspect(model),
            'resolutions': _imagegen_rungs(model),
            'durations': _imagegen_durations(model)}


def all_video_models():
    """Every model any job can reach, generated and swap alike."""
    seen = list(VIDEO_MODELS)
    for models in JOB_MODELS.values():
        for m in models:
            if m not in seen:
                seen.append(m)
    return seen


def price_table():
    """The whole menu, for the UI's live cost estimate."""
    return {
        'images': IMAGE_PRICES,
        'videos': VIDEO_PRICES,
        'video_labels': {m: MODEL_LABELS.get(m, m) for m in VIDEO_MODELS},
        'video_ratings': {m: list(r) for m, r in VIDEO_MODEL_RATINGS.items()},
        'video_models': list(VIDEO_MODELS),
        'video_edit_model': VIDEO_EDIT_MODEL,
        'swap_models': list(SWAP_MODELS),
        'default_swap_model': DEFAULT_SWAP_MODEL,
        # The five jobs, and what each one is made of. Everything the studio
        # renders comes from here rather than a list in the page: a job that
        # gains a model or a mode gains it in one place.
        'jobs': list(JOB_MODELS),
        'job_models': {j: list(m) for j, m in JOB_MODELS.items()},
        'job_kinds': dict(JOB_KINDS),
        'job_ratings': {j: list(r) for j, r in JOB_RATINGS.items()},
        'job_labels': dict(JOB_LABELS),
        'job_notes': dict(JOB_NOTES),
        'job_needs': {j: list(n) for j, n in JOB_NEEDS.items()},
        'default_job': DEFAULT_JOB,
        'aspects': list(ASPECTS),
        'default_aspect': DEFAULT_ASPECT,
        'audio_modes': list(AUDIO_MODES),
        'extend_modes': list(EXTEND_MODES),
        # What each model lets the operator choose. A model that runs the length
        # and the frame of the clip it is given has neither to offer. Still
        # called swap_model_caps because it is the same map the swap panel
        # already read; it now covers every model a job can reach.
        'swap_model_caps': {m: model_caps(m) for m in all_video_models()},
        'explicit_swap_model': EXPLICIT_SWAP_MODEL,
        # The lengths each video model actually serves, so the picker cannot
        # offer one the provider will refuse.
        'video_model_durations': {m: _imagegen_durations(m)
                                  for m in VIDEO_MODELS},
        # Fractional tokens a second. The client must ceil the whole clip, the
        # same as video_price does, or its estimate will not match the charge.
        'video_rates': VIDEO_RATE_PER_SECOND,
        'video_px': _IG.VIDEO_PX,
        'video_max_seconds': VIDEO_MAX_SECONDS,
        'addons': ADDON_PRICES,
        'labels': MODEL_LABELS,
        'ratings': {m: list(r) for m, r in MODEL_RATINGS.items()},
        'resolutions': list(RESOLUTIONS),
        'video_resolutions': list(VIDEO_RESOLUTIONS),
        'video_durations': list(VIDEO_DURATIONS),
        'video_seconds_min': VIDEO_SECONDS_MIN,
        'defaults': {
            'model': DEFAULT_IMAGE_MODEL,
            'resolution': DEFAULT_RESOLUTION,
            'video_resolution': DEFAULT_VIDEO_RESOLUTION,
            'video_duration': DEFAULT_VIDEO_DURATION,
            'video_model': DEFAULT_VIDEO_MODEL,
        },
    }


def margin_report():
    """Per-token price against per-token cost for every pack in every currency.
    The floor assertion below reads this; the admin billing page shows it."""
    rows = []
    for size in PACK_SIZES:
        for cur in CURRENCIES:
            price = PACK_PRICES[size][cur]
            per = price / size
            per_usd = per * REFERENCE_RATES[cur]
            rows.append({'tokens': size, 'currency': cur, 'price': price,
                         'per_token': per, 'per_token_usd': per_usd,
                         'multiple': per_usd / TOKEN_COST_USD,
                         'margin_pct': (1 - TOKEN_COST_USD / per_usd) * 100})
    return rows


def generation_margin_report():
    """Tokens against provider cost for every generation the picker can ask for.
    The pack floor never saw this table, which is how a clip came to be sold at
    30 credits while costing 46 -- so it is walked too."""
    rows = []
    for job, models in JOB_MODELS.items():
        for model in models:
            rungs = _imagegen_rungs(model)
            for res in rungs:
                for secs in sorted(set(VIDEO_DURATIONS +
                                       (VIDEO_SECONDS_MIN, VIDEO_MAX_SECONDS))):
                    for addons in ((), ('audio',)):
                        try:
                            price = job_price(job, res, secs, addons, model)
                        except PricingError:
                            continue
                        cost = (video_cost_usd(model, res, secs) or 0) + sum(
                            ADDON_COST_USD.get(a, 0) for a in addons)
                        rows.append({'job': job, 'model': model,
                                     'resolution': res, 'seconds': secs,
                                     'addons': addons, 'tokens': price,
                                     'sells_for': price * TOKEN_COST_USD,
                                     'cost_usd': cost})
    return rows


def _assert_generation_floor():
    """A generation may never sell under what it costs us. Unlike the pack floor
    this is a 1x test, not a margin one: the margin is taken when the tokens are
    bought, and taking it twice would price us out of our own table."""
    for row in generation_margin_report():
        if row['sells_for'] + 1e-9 < row['cost_usd']:
            raise AssertionError(
                f"{row['job']} on {row['model']} at {row['resolution']} for "
                f"{row['seconds']}s{' + ' + ', '.join(row['addons']) if row['addons'] else ''} "
                f"sells for ${row['sells_for']:.4f} and costs ${row['cost_usd']:.4f}")
    for name, cost in ADDON_COST_USD.items():
        price = ADDON_PRICES.get(name)
        if price is None or price * TOKEN_COST_USD + 1e-9 < cost:
            raise AssertionError(
                f'add-on {name!r} sells for {price} tokens and costs ${cost}')


def _assert_floor():
    floor = MIN_MARGIN_MULTIPLE * TOKEN_COST_USD
    for row in margin_report():
        if row['per_token_usd'] + 1e-9 < floor:
            raise AssertionError(
                f"pack {row['tokens']} in {row['currency']} sells tokens at "
                f"${row['per_token_usd']:.5f}, under the ${floor:.5f} floor "
                f"({MIN_MARGIN_MULTIPLE}x cost)")
    missing = [s for s in PACK_SIZES
               if set(PACK_PRICES.get(s, {})) < set(CURRENCIES)]
    if missing:
        raise AssertionError(f'pack sizes missing a currency: {missing}')
    # A bigger pack must never cost more per token than a smaller one, or the
    # ladder tells a creator to buy twice rather than once.
    for small, big in zip(PACK_SIZES, PACK_SIZES[1:]):
        for cur in CURRENCIES:
            if pack_price(big, cur) / big > pack_price(small, cur) / small:
                raise AssertionError(
                    f'pack {big} in {cur} costs more per token than {small}')
    # The test pack grants the same 1,000 tokens a real pack does, so a size is
    # not enough to tell them apart -- it is asked for by name (TEST_PACK_ID)
    # and priced from TEST_PACK_PRICES, never from the ladder. What has to hold
    # is that asking by size still charges the real price: otherwise the by-name
    # gate could be walked straight around with tokens=1000.
    for cur in CURRENCIES:
        if pack_price(TEST_PACK_TOKENS, cur) != PACK_PRICES[TEST_PACK_TOKENS][cur]:
            raise AssertionError(
                f'the {TEST_PACK_TOKENS}-token size in {cur} no longer resolves '
                'to its ladder price -- the test pack has leaked into it')
    # A pack Stripe will not charge is as broken as one that loses money: the
    # customer reaches the checkout page and it fails there rather than here.
    for size in PACK_SIZES:
        for cur in CURRENCIES:
            if pack_price(size, cur) < STRIPE_MIN_CHARGE[cur]:
                raise AssertionError(
                    f'pack {size} in {cur} is under Stripe\'s '
                    f'{STRIPE_MIN_CHARGE[cur]} minimum charge')


_assert_floor()
_assert_generation_floor()
