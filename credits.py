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

# What one credit is allowed to cost us at the provider. Every generation is
# priced at ceil(provider_cost / CREDIT_COST_USD), so rounding always favours us.
CREDIT_COST_USD = 0.002

# No pack may sell credits for less than this multiple of their cost. Asserted
# at import, so a discount that would lose money fails the build rather than a
# month of invoices.
MIN_MARGIN_MULTIPLE = 4.0

IMAGE_MODELS = ('flux-krea', 'flux-dev', 'flux-schnell', 'sdxl')
RESOLUTIONS = ('768x1024', '1024x1024', '1024x1536', '1536x2048')
VIDEO_RESOLUTIONS = ('480p', '720p', '1080p')
VIDEO_DURATIONS = (3, 5, 10)

DEFAULT_IMAGE_MODEL = 'flux-krea'
DEFAULT_RESOLUTION = '1024x1536'
DEFAULT_VIDEO_RESOLUTION = '720p'
DEFAULT_VIDEO_DURATION = 5

# Credits per generation, by model and resolution. Every number here is the
# rounded-up credit slice of a cost measured against the live provider at
# 1024x1024 / 28 steps, scaled by pixel count — not a list price.
#
#   flux-krea / flux-dev   $0.0045    sdxl  $0.0013    flux-schnell  ~$0.0015
IMAGE_PRICES = {
    'flux-krea':    {'768x1024': 3, '1024x1024': 3, '1024x1536': 4, '1536x2048': 7},
    'flux-dev':     {'768x1024': 3, '1024x1024': 3, '1024x1536': 4, '1536x2048': 7},
    'flux-schnell': {'768x1024': 1, '1024x1024': 1, '1024x1536': 2, '1536x2048': 3},
    'sdxl':         {'768x1024': 1, '1024x1024': 1, '1024x1536': 1, '1536x2048': 2},
}

VIDEO_PRICES = {
    '480p':  {3: 30, 5: 50, 10: 100},
    '720p':  {3: 90, 5: 150, 10: 300},
    '1080p': {3: 225, 5: 375, 10: 750},
}

# The legacy Google/Imagen path is priced from the same peg (~$0.02 a call), so
# it cannot be used as a free way around the credit system.
GOOGLE_IMAGE_CREDITS = 10

# Add-ons, in the same measured slices. Identity is not one mechanism but
# three, and they do not cost the same: PuLID carries a clothed shot on its own
# ($0.0060), an explicit shot holds the body with the Flux IP-Adapter ($0.0013)
# and then pays a second pass on SDXL to put her face back ($0.0026).
ADDON_PRICES = {
    'identity': 4,
    'reference': 1,
    'restore': 2,
    'upscale': 2,
    'nsfw_check': 1,
}

# Human labels for the model picker. Closed video models (Kling, Veo, Seedance)
# are deliberately absent everywhere in this file: they are moderated and cannot
# serve this feature, so nothing may offer them for an NSFW slot.
MODEL_LABELS = {
    'flux-krea': 'Most realistic',
    'flux-dev': 'Highest detail',
    'flux-schnell': 'Fast draft',
    'sdxl': 'Cheapest',
}

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


def video_price(resolution, seconds, addons=()):
    try:
        base = VIDEO_PRICES[resolution][int(seconds)]
    except (KeyError, ValueError, TypeError):
        raise PricingError(f'no price for video {resolution!r} at {seconds!r}s')
    return base + sum(_addon(a) for a in addons)


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
    if kind == 'video':
        return video_price(spec.get('resolution') or DEFAULT_VIDEO_RESOLUTION,
                           spec.get('seconds') or DEFAULT_VIDEO_DURATION, addons)
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


def equivalents(credits):
    """What a balance is worth in the two things creators actually make, for the
    'about 4,200 photos or 28 clips' line in the header."""
    photo = image_price(DEFAULT_IMAGE_MODEL, DEFAULT_RESOLUTION, ('identity',))
    clip = video_price(DEFAULT_VIDEO_RESOLUTION, DEFAULT_VIDEO_DURATION)
    n = max(0, int(credits or 0))
    return {'photos': n // photo, 'clips': n // clip}


def price_table():
    """The whole menu, for the UI's live cost estimate."""
    return {
        'images': IMAGE_PRICES,
        'videos': VIDEO_PRICES,
        'addons': ADDON_PRICES,
        'labels': MODEL_LABELS,
        'resolutions': list(RESOLUTIONS),
        'video_resolutions': list(VIDEO_RESOLUTIONS),
        'video_durations': list(VIDEO_DURATIONS),
        'defaults': {
            'model': DEFAULT_IMAGE_MODEL,
            'resolution': DEFAULT_RESOLUTION,
            'video_resolution': DEFAULT_VIDEO_RESOLUTION,
            'video_duration': DEFAULT_VIDEO_DURATION,
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
