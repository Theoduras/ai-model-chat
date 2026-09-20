"""Credit pricing and ledger tests. Run with `python test_credits.py`.

The margin floor test is the one that matters commercially: it walks every pack
against every tier band, so a discount that would sell credits below cost fails
here rather than on a month of invoices.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())

import credits as CR  # noqa: E402
import db  # noqa: E402

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ok   {name}')
    else:
        print(f'  FAIL {name} {detail}')
        FAILURES.append(name)


def test_margin_floor():
    print('margin floor')
    floor = CR.MIN_MARGIN_MULTIPLE * CR.CREDIT_COST_USD
    worst = min(CR.margin_report(), key=lambda r: r['per_credit'])
    for row in CR.margin_report():
        check(f"{row['credits']:>5} credits / {row['band']:<6} "
              f"= {row['multiple']:.1f}x cost, {row['margin_pct']:.0f}% margin",
              row['per_credit'] >= floor,
              f"${row['per_credit']:.5f} < ${floor:.5f}")
    check('worst cell still clears the floor', worst['per_credit'] >= floor)
    print(f"  worst: {worst['credits']} on {worst['band']} — "
          f"{worst['multiple']:.1f}x cost, {worst['margin_pct']:.0f}% margin")


def test_tier_discount():
    print('tier discount')
    for size in CR.PACK_SIZES:
        base = CR.pack_price_usd(size, 'starter')
        pro = CR.pack_price_usd(size, 'pro')
        agency = CR.pack_price_usd(size, 'agency')
        check(f'{size} credits gets cheaper with tier ({base} > {pro} > {agency})',
              base > pro > agency)
    check('a bigger pack is cheaper per credit',
          all(CR.pack_price_usd(a, 'pro') / a > CR.pack_price_usd(b, 'pro') / b
              for a, b in zip(CR.PACK_SIZES, CR.PACK_SIZES[1:])))
    check('an unknown pack size has no price',
          CR.pack_price_usd(777, 'pro') is None)


def test_quote_covers_everything():
    """Nothing the UI can offer may be unpriced: an unquotable spec is one we
    cannot bill for, so it must never reach the provider."""
    print('quote coverage')
    missing = []
    for model in CR.IMAGE_MODELS:
        for res in CR.RESOLUTIONS:
            for addons in ((), ('upscale',), ('nsfw_check',),
                           ('upscale', 'nsfw_check')):
                try:
                    CR.quote({'kind': 'image', 'model': model, 'resolution': res,
                              'addons': addons, 'batch': 4})
                except CR.PricingError:
                    missing.append((model, res, addons))
    for model in CR.VIDEO_MODELS:
        for res in CR.VIDEO_RESOLUTIONS:
            for secs in CR.VIDEO_DURATIONS:
                try:
                    CR.quote({'kind': 'video', 'model': model,
                              'resolution': res, 'seconds': secs})
                except CR.PricingError:
                    missing.append((model, res, secs))
    check('every model / resolution / duration the UI offers is priced',
          not missing, str(missing))

    for bad in ({'kind': 'image', 'model': 'nope', 'resolution': '1024x1024'},
                {'kind': 'video', 'resolution': '4k', 'seconds': 5},
                # Not a duration: any whole number in range is priced now,
                # since a clip is billed per second rather than by preset.
                {'kind': 'video', 'resolution': '720p', 'seconds': 99},
                {'kind': 'hologram'}):
        try:
            CR.quote(bad)
            check(f'unpriced spec {bad} is refused', False)
        except CR.PricingError:
            check(f'unpriced spec {bad.get("kind")} is refused', True)


IMAGE_BASE = 20


def test_prices_track_cost():
    print('prices track cost')
    check('a credit is the rounded-up cost slice',
          CR.credits_for_cost(0.0013) == 1 and CR.credits_for_cost(0.0021) == 2)
    check('batch multiplies, add-ons are per image',
          CR.quote({'kind': 'image', 'model': 'seedream-4-5',
                    'resolution': '2k', 'addons': ('upscale',),
                    'batch': 4}) == (20 + 2) * 4)
    check('4k costs what 2k costs, because the provider bills them the same',
          CR.quote({'kind': 'image', 'model': 'seedream-4-5', 'resolution': '4k'}) ==
          CR.quote({'kind': 'image', 'model': 'seedream-4-5', 'resolution': '2k'}))
    check('holding her face is free — identity is inside the one call',
          CR.quote({'kind': 'image', 'model': 'seedream-4-5', 'resolution': '2k'}) ==
          IMAGE_BASE)
    check('a 720p 5s clip is priced at the measured per-second rate',
          CR.quote({'kind': 'video', 'resolution': '720p', 'seconds': 5}) == 46 * 5)
    # What a 5s 720p clip actually billed on the provider, per model. Seedance
    # refused the probe before it billed, so it is held to the dearer of the
    # two that did.
    measured = {'wan-2-5': 0.4538, 'wan-2-7': 0.5038, 'seedance-2-5': 0.5038}
    for model, cost in measured.items():
        check(f'a {model} clip never sells under what the provider charges',
              CR.quote({'kind': 'video', 'model': model,
                        'resolution': '720p', 'seconds': 5})
              * CR.CREDIT_COST_USD >= cost)
    check('a swap is priced on the model it will actually run on',
          all(CR.quote({'kind': 'swap', 'model': m,
                        'resolution': '720p', 'seconds': 5}) ==
              CR.VIDEO_RATE_PER_SECOND[m]['720p'] * 5
              for m in CR.SWAP_MODELS))
    check('a swap on no model named is priced on the default one',
          CR.quote({'kind': 'swap', 'resolution': '720p', 'seconds': 5}) ==
          CR.quote({'kind': 'swap', 'model': CR.DEFAULT_SWAP_MODEL,
                    'resolution': '720p', 'seconds': 5}))
    check('a swap is billed for every second of the clip it was given',
          CR.quote({'kind': 'swap', 'resolution': '720p', 'seconds': 7}) ==
          CR.VIDEO_RATE_PER_SECOND[CR.DEFAULT_SWAP_MODEL]['720p'] * 7)
    for m in CR.SWAP_MODELS:
        cost = CR.VIDEO_COST_USD_PER_SECOND[m]['720p'] * 5
        check(f'a swap on {m} never sells under what the provider charges',
              CR.quote({'kind': 'swap', 'model': m, 'resolution': '720p',
                        'seconds': 5}) * CR.CREDIT_COST_USD >= cost)
    for bad in ({'kind': 'swap', 'resolution': '720p',
                 'seconds': CR.VIDEO_MAX_SECONDS + 1},
                {'kind': 'swap', 'resolution': '720p', 'seconds': 0}):
        try:
            CR.quote(bad)
            check(f'a swap of {bad["seconds"]}s is refused', False)
        except CR.PricingError:
            check(f'a swap of {bad["seconds"]}s is refused', True)
    check('the legacy Google path is priced too, so it is not a free bypass',
          CR.GOOGLE_IMAGE_CREDITS > 0)


def test_job_quotes():
    """Every combination the five job tiles can produce has a price, and none
    of them sells under what the provider bills. The picker reads its options
    out of the same tables this walks, so an option that appears there and
    cannot be quoted fails here rather than at submit."""
    print('video jobs')
    missing, under = [], []
    for job, models in CR.JOB_MODELS.items():
        for model in models:
            rungs = CR._imagegen_rungs(model)
            durations = CR._imagegen_durations(model) or [
                CR.VIDEO_SECONDS_MIN, 5, CR.VIDEO_MAX_SECONDS]
            for aspect in CR.ASPECTS:
                for res in rungs:
                    for secs in durations:
                        for addons in ((), ('audio',)):
                            spec = {'job': job, 'model': model,
                                    'resolution': res, 'seconds': secs,
                                    'aspect': aspect, 'addons': addons}
                            try:
                                price = CR.quote(spec)
                            except CR.PricingError:
                                missing.append((job, model, aspect, res, secs,
                                                addons))
                                continue
                            cost = (CR.video_cost_usd(model, res, secs) or 0) \
                                + sum(CR.ADDON_COST_USD.get(a, 0) for a in addons)
                            if price * CR.CREDIT_COST_USD + 1e-9 < cost:
                                under.append((job, model, aspect, res, secs,
                                              addons, price))
    check('every job / model / aspect / rung / duration is priced',
          not missing, str(missing[:6]))
    check('no job / model / aspect / rung / duration sells under cost',
          not under, str(under[:6]))

    check('the import-time generation floor walked a real table',
          len(CR.generation_margin_report()) > 0)

    check('a frame shape costs nothing — the same pixels, a different crop',
          len({CR.quote({'job': 'reel', 'resolution': '720p', 'seconds': 5,
                         'aspect': a}) for a in CR.ASPECTS}) == 1)

    check('sound is flat per clip, not per second',
          CR.quote({'job': 'reel', 'resolution': '720p', 'seconds': 10,
                    'addons': ('audio',)}) -
          CR.quote({'job': 'reel', 'resolution': '720p', 'seconds': 10}) ==
          CR.quote({'job': 'reel', 'resolution': '720p', 'seconds': 3,
                    'addons': ('audio',)}) -
          CR.quote({'job': 'reel', 'resolution': '720p', 'seconds': 3}) ==
          CR.ADDON_PRICES['audio'])

    check('sound never sells under what a pass costs us',
          CR.ADDON_PRICES['audio'] * CR.CREDIT_COST_USD >=
          CR.ADDON_COST_USD['audio'])

    # A model that does not serve a job must not be quotable on it: the picker
    # offers the job's own list, so anything else arrived from a hand-made
    # request and would be billed on a model that never ran.
    for job, model in (('reel', 'p-video-replace'), ('extend', 'wan-2-5'),
                       ('multiref', 'seedance-2-5'), ('nonesuch', 'wan-2-7')):
        try:
            CR.quote({'job': job, 'model': model, 'resolution': '720p',
                      'seconds': 5})
            check(f'{model} is refused on the {job} job', False)
        except CR.PricingError:
            check(f'{model} is refused on the {job} job', True)

    check('a swap job is priced for the clip it was handed, not a default',
          CR.quote({'job': 'swap', 'resolution': '720p', 'seconds': 7}) ==
          CR.quote({'kind': 'swap', 'model': CR.DEFAULT_SWAP_MODEL,
                    'resolution': '720p', 'seconds': 7}))
    try:
        CR.quote({'job': 'swap', 'resolution': '720p'})
        check('a swap with no measured duration is refused', False)
    except CR.PricingError:
        check('a swap with no measured duration is refused', True)

    check('every job the studio can show names at least one model',
          all(CR.JOB_MODELS[j] for j in CR.JOB_MODELS))
    check('a reel is safe work only, so a prompt-only clip claims nobody',
          CR.JOB_RATINGS['reel'] == ('sfw',))
    check('every job the picker offers has a kind the quote understands',
          set(CR.JOB_KINDS.values()) <= {'video', 'swap'})


def test_ledger():
    print('ledger')
    db.init_db()
    s = db.SessionLocal()
    ws = 'ws-' + os.urandom(4).hex()
    soon = datetime.utcnow() + timedelta(days=20)

    db.credit_grant(s, ws, 600, '2026-09', soon)
    check('grant posts', db.credit_balance(s, ws) == 600)
    db.credit_grant(s, ws, 600, '2026-09', soon)
    check('the same period cannot grant twice', db.credit_balance(s, ws) == 600)

    db.credit_purchase(s, ws, 2000, 'pay-1')
    db.credit_purchase(s, ws, 2000, 'pay-1')
    check('a replayed webhook cannot credit twice',
          db.credit_balance(s, ws) == 2600)

    db.credit_debit(s, ws, 150, 'job-1')
    check('a spend lowers the balance', db.credit_balance(s, ws) == 2450)

    # The point of the split: the allowance pays first, so when the month turns
    # the purchased balance is untouched.
    expired = db.credit_balance(s, ws, at=soon + timedelta(days=1))
    check('spend drains the expiring allowance before purchased credits',
          expired == 2000, f'got {expired}, wanted 2000')

    db.credit_refund(s, ws, 'job-1')
    check('a refund restores the balance', db.credit_balance(s, ws) == 2600)
    check('a refund returns credits to the bucket they left',
          db.credit_balance(s, ws, at=soon + timedelta(days=1)) == 2000)
    db.credit_refund(s, ws, 'job-1')
    check('a second refund pays nothing', db.credit_balance(s, ws) == 2600)

    check('an overdraw is refused and posts nothing',
          db.credit_debit(s, ws, 99999, 'job-2') is False
          and db.credit_balance(s, ws) == 2600)

    db.credit_debit(s, ws, 900, 'job-3')
    check('a spend larger than the allowance takes the rest from purchased',
          db.credit_balance(s, ws) == 1700
          and db.credit_balance(s, ws, at=soon + timedelta(days=1)) == 1700)
    s.close()


def test_equivalents():
    print('equivalents')
    eq = CR.equivalents(4200)
    check(f'4,200 credits reads as {eq["photos"]} photos or {eq["clips"]} clips',
          eq['photos'] > 0 and eq['clips'] > 0)
    check('an empty balance reads as nothing',
          CR.equivalents(0) == {'photos': 0, 'clips': 0})


if __name__ == '__main__':
    for fn in (test_margin_floor, test_tier_discount, test_quote_covers_everything,
               test_prices_track_cost, test_job_quotes, test_ledger,
               test_equivalents):
        fn()
    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILED: {", ".join(FAILURES)}')
        sys.exit(1)
    print('all credit tests passed')
