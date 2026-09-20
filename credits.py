"""Credit pricing for AI image and video generation.

Every retail price in this file is derived from one number: what a generation
costs us at the provider. A credit is a fixed slice of that cost, so gross
margin is the same whatever the creator generates — they cannot arbitrage us by
favouring an expensive model, and adding a model is a table entry rather than a
pricing decision. If a provider raises prices, CREDIT_COST_USD is the only
number to move; every pack rate follows it and the margin floor re-asserts at
import.
"""
import math
import os

# What one credit is allowed to cost us at the provider. Every generation is
# priced at ceil(provider_cost / CREDIT_COST_USD), so rounding always favours us.
CREDIT_COST_USD = 0.002

# No pack may sell credits for less than this multiple of their cost. Asserted
# at import, so a discount that would lose money fails the build rather than a
# month of invoices.
MIN_MARGIN_MULTIPLE = 4.0

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

# Credits per generation, by model and resolution. Measured against the live
# provider, not a list price: Seedream bills $0.04 a still whatever the size,
# so 4k costs the same as 2k and both round to 20 credits at CREDIT_COST_USD.
#
# That is five times what the old Flux rung cost. It buys native identity —
# a reference-conditioned single call, where Flux needed an adapter, a LoRA and
# a second restore pass to hold the same face.
# Seedream bills flat by size; the Google models do not, so their tiers differ.
# Measured at 2k: Nano Banana 2 $0.10255, Nano Banana Pro $0.138. The 4k figure
# came back before the provider reported a cost, so 4k is priced at twice 2k —
# a deliberate over-estimate, because the floor protects us only while the
# price is above the cost. Measure it and bring these down.
IMAGE_PRICES = {
    'seedream-4-5':    {'2k': 20, '4k': 20},
    'seedream-5-pro':  {'2k': 20, '4k': 20},
    'nano-banana-2':   {'2k': 52, '4k': 104},
    'nano-banana-pro': {'2k': 69, '4k': 138},
}

# Video, priced per second against a measured clip, per model. A 5s 720p clip
# on Wan 2.5 costs $0.4538, which is 46 credits a second — the old table charged
# 30 a second and so sold every clip below cost. The floor assertion never
# caught it because it only walks the packs, not the generation table.
#
# Wan 2.7 measured $0.5038 on the same clip, so 52. Seedance 2.5 refused the
# probe at ByteDance's moderation end before it billed anything, so its rate is
# a deliberate over-estimate: a guess that is too low loses money on every clip
# and nothing reports it. Only 720p is measured; 480p is charged at the same
# rate for the same reason, and 1080p keeps the old table's 2.5x shape. Measure
# them and bring these down.
VIDEO_MODELS = ('wan-2-5', 'wan-2-7', 'seedance-2-5')
DEFAULT_VIDEO_MODEL = 'wan-2-5'

# Wan 2.7 is the only video model that takes an input clip, so a face swap into
# an uploaded video is always priced and run on it whatever the picker says.
VIDEO_EDIT_MODEL = 'wan-2-7'

# The two models a swap may run on. They do opposite things with the clip they
# are given: replace keeps the video and changes who is in it, Wan 2.7
# regenerates the video from her references in a similar motion. Both are
# offered because only the operator can say which one a given clip wants.
SWAP_MODELS = ('p-video-replace', 'wan-2-2-animate', 'wan-2-7')
DEFAULT_SWAP_MODEL = 'p-video-replace'
# Where an explicit persona goes: the only model that both replaces rather
# than regenerates and serves explicit work.
EXPLICIT_SWAP_MODEL = 'wan-2-2-animate'

VIDEO_RATE_PER_SECOND = {
    'wan-2-5':      {'480p': 46, '720p': 46, '1080p': 115},
    'wan-2-7':      {'480p': 52, '720p': 52, '1080p': 130},
    'seedance-2-5': {'480p': 60, '720p': 60, '1080p': 150},
    # Unmeasured, so deliberately high: a guess under cost loses money on every
    # clip and nothing reports it. Measure it and bring this down.
    'p-video-replace': {'480p': 60, '720p': 60, '1080p': 150},
    'wan-2-2-animate': {'480p': 60, '720p': 60, '1080p': 150},
}

# A swap runs the length of the clip it is given, so it is priced per second
# from the same rates rather than off the fixed durations a generated clip
# offers. The cap matches what the upload route will take.
# Wan 2.7's own ceiling. A longer upload cannot be swapped, so it is refused
# at the upload rather than truncated after it is paid for.
VIDEO_MAX_SECONDS = 15

VIDEO_PRICES = {
    model: {res: {secs: rate * secs for secs in VIDEO_DURATIONS}
            for res, rate in rates.items()}
    for model, rates in VIDEO_RATE_PER_SECOND.items()
}

# What the provider actually bills us, in USD. The credit tables above are
# derived from these by ceil(cost / CREDIT_COST_USD) and then rounded up again
# where a figure is a guess, so credits * CREDIT_COST_USD reads high and cannot
# be used to answer "what did that cost me". This is the number to show an
# operator before they press Generate.
#
# `PROVIDER_COST_MEASURED` names the rungs that came off a live bill. Everything
# else is an over-estimate carried from the credit table's own reasoning: a
# guess that is too low loses money quietly, so the guesses are deliberately
# high. Measure one and move its key into the measured set.
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
    'wan-2-2-animate': {'480p': 0.12, '720p': 0.12, '1080p': 0.30},
}

PROVIDER_COST_MEASURED = {
    'images': {'seedream-4-5': ('2k', '4k'),
               'nano-banana-2': ('2k',),
               'nano-banana-pro': ('2k',)},
    'videos': {'wan-2-5': ('720p',), 'wan-2-7': ('720p',)},
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
            'measured': PROVIDER_COST_MEASURED}


# The legacy Google/Imagen path is priced from the same peg (~$0.02 a call), so
# it cannot be used as a free way around the credit system.
GOOGLE_IMAGE_CREDITS = 10

# Add-ons, in the same measured slices. Identity is no longer one of them:
# Seedream conditions on reference images inside the one call it already
# charges for, so holding her face now costs nothing on top.
ADDON_PRICES = {
    'upscale': 2,
    'nsfw_check': 1,
}

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
    'wan-2-2-animate': 'Replace her in the clip — explicit',
}

# Which ratings each model actually serves, measured against the provider
# rather than assumed. The picker filters on this: a model that would be moved
# to another one at submit should never have been offered in the first place,
# because the creator reads the swap as the model having lied.
#
# Only Seedream 4.5 serves explicit work. 5.0 Pro refuses it at ByteDance's
# end, and the Google models refuse it at any safety level.
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
    # The Wan family is the one measured to serve explicit work here, and 2.2
    # Animate replaces rather than regenerates.
    'wan-2-2-animate': ('sfw', 'nsfw'),
}


def models_for_rating(rating):
    want = 'nsfw' if rating == 'nsfw' else 'sfw'
    return [m for m in IMAGE_MODELS if want in MODEL_RATINGS.get(m, ('sfw',))]


def video_models_for_rating(rating):
    want = 'nsfw' if rating == 'nsfw' else 'sfw'
    return [m for m in VIDEO_MODELS
            if want in VIDEO_MODEL_RATINGS.get(m, ('sfw',))]

# Included allowance per calendar month, keyed on the tier keys in app.TIERS.
MONTHLY_CREDITS = {
    'demo': 200,
    'starter': 600,
    'pro': 2500,
    'agency': 7500,
}
DEFAULT_MONTHLY_CREDITS = 200

# Top-up packs. Price in whole dollars per (pack size, tier band) — the higher
# the subscription, the cheaper the credit, which is a real upgrade incentive
# that costs nothing because every rate still clears the floor by at least 5x.
PACK_SIZES = (500, 2000, 10000, 50000)

# Tiers that share a price column. Anything not listed bills at the base rate.
PACK_BANDS = {'demo': 'base', 'starter': 'base', 'pro': 'pro', 'agency': 'agency'}
DEFAULT_PACK_BAND = 'base'

PACK_PRICES_USD = {
    500:   {'base': 15,  'pro': 13,  'agency': 11},
    2000:  {'base': 50,  'pro': 42,  'agency': 34},
    10000: {'base': 200, 'pro': 165, 'agency': 130},
    50000: {'base': 850, 'pro': 700, 'agency': 550},
}


class PricingError(ValueError):
    """An unpriced generation was requested. Never let one reach the provider:
    a spec we cannot quote is a spec we cannot bill for."""


def credits_for_cost(usd):
    """The credit price of something that costs us `usd`, rounded up."""
    return max(1, int(math.ceil(float(usd) / CREDIT_COST_USD)))


def image_price(model, resolution, addons=(), batch=1):
    try:
        base = IMAGE_PRICES[model][resolution]
    except KeyError:
        raise PricingError(f'no price for image {model!r} at {resolution!r}')
    per = base + sum(_addon(a) for a in addons)
    return per * max(1, int(batch))


def video_price(resolution, seconds, addons=(), model=None):
    """Per second at the model's rung, so any length in range has a price --
    the three presets are no longer the only lengths a clip may be."""
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
    return rate * secs + sum(_addon(a) for a in addons)


def swap_price(resolution, seconds, addons=(), model=None):
    model = model if model in SWAP_MODELS else DEFAULT_SWAP_MODEL
    rates = VIDEO_RATE_PER_SECOND.get(model) or {}
    rate = rates.get(resolution)
    secs = int(seconds or 0)
    if not rate or not VIDEO_SECONDS_MIN <= secs <= VIDEO_MAX_SECONDS:
        raise PricingError(f'no price for a swap at {resolution!r} / {seconds!r}s')
    return rate * secs + sum(_addon(a) for a in addons)


def _addon(name):
    try:
        return ADDON_PRICES[name]
    except KeyError:
        raise PricingError(f'no price for add-on {name!r}')


def quote(spec):
    """Credits for a generation spec, as the job API and the UI both see it.

    spec: {kind: image|video, model, resolution, seconds, batch, addons[]}
    """
    kind = (spec or {}).get('kind') or 'image'
    addons = tuple(spec.get('addons') or ())
    if kind == 'swap':
        # No default duration here, unlike a generated clip: a swap is billed
        # for the clip it was handed, so a spec that carries no measured
        # duration is one we cannot price rather than one we guess at.
        return swap_price(spec.get('resolution') or DEFAULT_VIDEO_RESOLUTION,
                          spec.get('seconds'), addons, spec.get('model'))
    if kind == 'video':
        model = spec.get('model')
        if model not in VIDEO_PRICES:
            model = DEFAULT_VIDEO_MODEL
        return video_price(spec.get('resolution') or DEFAULT_VIDEO_RESOLUTION,
                           spec.get('seconds') or DEFAULT_VIDEO_DURATION,
                           addons, model)
    if kind != 'image':
        raise PricingError(f'unknown generation kind {kind!r}')
    return image_price(spec.get('model') or DEFAULT_IMAGE_MODEL,
                       spec.get('resolution') or DEFAULT_RESOLUTION,
                       addons, spec.get('batch') or 1)


def monthly_credits(tier):
    return MONTHLY_CREDITS.get(tier or '', DEFAULT_MONTHLY_CREDITS)


def pack_band(tier):
    return PACK_BANDS.get(tier or '', DEFAULT_PACK_BAND)


def pack_price_usd(size, tier):
    """Dollar price of a pack for this tier, or None if there is no such pack."""
    row = PACK_PRICES_USD.get(int(size))
    return row.get(pack_band(tier)) if row else None


def packs_for(tier):
    """The buy-credits menu, already tier-resolved so the client needs no
    discount logic of its own."""
    band = pack_band(tier)
    base = DEFAULT_PACK_BAND
    out = []
    for size in PACK_SIZES:
        price = PACK_PRICES_USD[size][band]
        full = PACK_PRICES_USD[size][base]
        out.append({
            'credits': size,
            'price_usd': price,
            'price_cents': int(round(price * 100)),
            'per_credit': round(price / size, 5),
            'save_pct': int(round((1 - price / full) * 100)) if full > price else 0,
        })
    return out


def credit_rate_usd(tier):
    """What one credit costs this tier in cash, for showing a price beside a
    credit count. It is the smallest pack's rate — the marginal price of buying
    more — rather than an average over packs nobody bought, so "20 credits"
    reads as what the next twenty would actually cost."""
    return pack_price_usd(PACK_SIZES[0], tier) / PACK_SIZES[0]


# Euros are shown only when a rate is configured. A hard-coded one would be
# wrong within a week and wrong silently, which is worse than showing dollars.
def eur_per_usd():
    raw = (os.getenv('EUR_PER_USD') or '').strip()
    try:
        rate = float(raw)
    except ValueError:
        return None
    return rate if rate > 0 else None


def cash_for_credits(credits, tier):
    """A credit count priced in cash: {usd, eur}. `eur` is None unless a rate
    is set."""
    usd = round(max(0, int(credits or 0)) * credit_rate_usd(tier), 2)
    rate = eur_per_usd()
    return {'usd': usd, 'eur': round(usd * rate, 2) if rate else None}


def equivalents(credits):
    """What a balance is worth in the two things creators actually make, for the
    'about 4,200 photos or 28 clips' line in the header."""
    photo = image_price(DEFAULT_IMAGE_MODEL, DEFAULT_RESOLUTION)
    clip = video_price(DEFAULT_VIDEO_RESOLUTION, DEFAULT_VIDEO_DURATION)
    n = max(0, int(credits or 0))
    return {'photos': n // photo, 'clips': n // clip}


def _imagegen_takes_duration(model):
    # Imported here rather than at module scope: credits.py is the one module
    # test_credits.py loads on its own, and it must not need the provider stack.
    try:
        import imagegen
        return bool(imagegen.takes_duration(model))
    except Exception:
        return True


def _imagegen_durations(model):
    try:
        import imagegen
        return imagegen.model_durations(model)
    except Exception:
        return None


def _imagegen_rungs(model):
    try:
        import imagegen
        return imagegen.model_rungs(model) or list(VIDEO_RESOLUTIONS)
    except Exception:
        return list(VIDEO_RESOLUTIONS)


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
        # What each swap model lets the operator choose. A model that runs the
        # length of the clip it is given has no seconds to offer.
        'swap_model_caps': {m: {'duration': _imagegen_takes_duration(m),
                                'resolutions': _imagegen_rungs(m)}
                            for m in SWAP_MODELS},
        'explicit_swap_model': EXPLICIT_SWAP_MODEL,
        # The lengths each video model actually serves, so the picker cannot
        # offer one the provider will refuse.
        'video_model_durations': {m: _imagegen_durations(m)
                                  for m in VIDEO_MODELS},
        'video_rates': VIDEO_RATE_PER_SECOND,
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
    """Per-credit price against per-credit cost for every pack and band. The
    floor assertion below reads this; the admin billing page shows it."""
    rows = []
    for size in PACK_SIZES:
        for band, price in PACK_PRICES_USD[size].items():
            per = price / size
            rows.append({'credits': size, 'band': band, 'price_usd': price,
                         'per_credit': per,
                         'multiple': per / CREDIT_COST_USD,
                         'margin_pct': (1 - CREDIT_COST_USD / per) * 100})
    return rows


def _assert_floor():
    floor = MIN_MARGIN_MULTIPLE * CREDIT_COST_USD
    for row in margin_report():
        if row['per_credit'] < floor:
            raise AssertionError(
                f"pack {row['credits']} on band {row['band']} sells credits at "
                f"${row['per_credit']:.5f}, under the ${floor:.5f} floor "
                f"({MIN_MARGIN_MULTIPLE}x cost)")
    missing = [s for s in PACK_SIZES
               if set(PACK_PRICES_USD.get(s, {})) < set(PACK_BANDS.values())]
    if missing:
        raise AssertionError(f'pack sizes missing a band price: {missing}')


_assert_floor()
