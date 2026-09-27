"""Token pricing and ledger tests. Run with `python test_tokens.py`.

The margin floor test is the one that matters commercially: it walks every pack
in every currency, so a price that would sell tokens below cost fails here
rather than on a month of invoices.
"""
import math
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
    floor = CR.MIN_MARGIN_MULTIPLE * CR.TOKEN_COST_USD
    rows = CR.margin_report()
    worst = min(rows, key=lambda r: r['per_token_usd'])
    for row in rows:
        check(f"{row['tokens']:>5} tokens / {row['currency']} "
              f"= {row['multiple']:.2f}x cost, {row['margin_pct']:.0f}% margin",
              row['per_token_usd'] + 1e-9 >= floor,
              f"${row['per_token_usd']:.5f} < ${floor:.5f}")
    check('worst cell still clears the floor',
          worst['per_token_usd'] + 1e-9 >= floor)
    print(f"  worst: {worst['tokens']} in {worst['currency']} — "
          f"{worst['multiple']:.2f}x cost, {worst['margin_pct']:.0f}% margin")


def test_currency_ladder():
    """Euro is the base, but a hole in any currency renders a blank price tag,
    and a ladder that stops falling tells a creator to buy twice rather than
    once."""
    print('currency ladder')
    for size in CR.PACK_SIZES:
        have = [c for c in CR.CURRENCIES if CR.pack_price(size, c)]
        check(f'{size} tokens has a price in all three currencies',
              len(have) == len(CR.CURRENCIES), str(have))
    for cur in CR.CURRENCIES:
        per = [CR.pack_price(s, cur) / s for s in CR.PACK_SIZES]
        check(f'a bigger pack is cheaper per token in {cur}',
              all(a > b for a, b in zip(per, per[1:])),
              str([round(p, 4) for p in per]))
    check('an unknown pack size has no price', CR.pack_price(777) is None)
    check('an unknown currency falls back to the base one',
          CR.pack_price(100, 'jpy') == CR.pack_price(100, CR.BASE_CURRENCY))
    check('the base currency is euro', CR.BASE_CURRENCY == 'eur')
    check('the quoted rate is the smallest pack, the marginal price of more',
          CR.token_rate('eur') ==
          CR.pack_price(CR.PACK_SIZES[0], 'eur') / CR.PACK_SIZES[0])


def test_quote_covers_everything():
    """Nothing the UI can offer may be unpriced: an unquotable spec is one we
    cannot bill for, so it must never reach the provider."""
    print('quote coverage')
    missing = []
    for model in CR.IMAGE_MODELS:
        for res in CR.RESOLUTIONS:
            for addons in ((), ('audio',)):
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
                # Not a duration: any whole number in range is priced now, since
                # a clip is billed per second rather than by preset.
                {'kind': 'video', 'resolution': '720p', 'seconds': 99},
                {'kind': 'image', 'model': 'seedream-4-5', 'resolution': '2k',
                 'addons': ('upscale',)},
                {'kind': 'hologram'}):
        try:
            CR.quote(bad)
            check(f'unpriced spec {bad} is refused', False)
        except CR.PricingError:
            check(f'unpriced spec {bad.get("kind")} '
                  f'{bad.get("addons") or bad.get("seconds") or ""} is refused',
                  True)


def test_prices_track_cost():
    print('prices track cost')
    check('one token is one standard photo — the whole point of the scale',
          CR.quote({'kind': 'image', 'model': 'seedream-4-5',
                    'resolution': '2k'}) == 1)
    check('a token is the rounded-up cost slice',
          CR.credits_for_cost(0.01) == 1 and CR.credits_for_cost(0.041) == 2)
    check('batch multiplies',
          CR.quote({'kind': 'image', 'model': 'seedream-4-5',
                    'resolution': '2k', 'batch': 4}) == 4)
    check('4k costs what 2k costs, because the provider bills them the same',
          CR.quote({'kind': 'image', 'model': 'seedream-4-5', 'resolution': '4k'}) ==
          CR.quote({'kind': 'image', 'model': 'seedream-4-5', 'resolution': '2k'}))
    check('holding her face is free — identity is inside the one call',
          CR.IMAGE_PRICES['seedream-4-5']['2k'] == 1)
    check('a premium still costs more, in single digits',
          CR.IMAGE_PRICES['nano-banana-pro'] == {'2k': 4, '4k': 7})

    # The whole-job rounding is the reason a clip is 12 and not 15. A
    # per-second integer rate would round 2.269 up to 3 and overcharge by 25%.
    check('a 5s 720p clip rounds once for the whole job, not per second',
          CR.quote({'kind': 'video', 'resolution': '720p', 'seconds': 5}) == 12)
    check('the per-second rate is fractional, so the client can ceil the same',
          isinstance(CR.VIDEO_RATE_PER_SECOND['wan-2-5']['720p'], float))
    for model in CR.VIDEO_RATE_PER_SECOND:
        for res in CR.VIDEO_RATE_PER_SECOND[model]:
            for secs in (2, 3, 5, 7, 10, 15):
                want = max(1, math.ceil(
                    CR.VIDEO_RATE_PER_SECOND[model][res] * secs))
                got = CR.video_price(res, secs, (), model)
                if got != want:
                    check(f'{model} {res} {secs}s rounds the whole job',
                          False, f'got {got}, wanted {want}')
                    break
            else:
                continue
            break
    else:
        check('every rung rounds the whole job, never per second', True)

    measured = {'wan-2-5': 0.4538, 'wan-2-7': 0.5038, 'seedance-2-5': 0.60}
    for model, cost in measured.items():
        check(f'a {model} clip never sells under what the provider charges',
              CR.quote({'kind': 'video', 'model': model,
                        'resolution': '720p', 'seconds': 5})
              * CR.TOKEN_COST_USD >= cost)
    check('a swap is priced on the model it will actually run on',
          all(CR.quote({'kind': 'swap', 'model': m,
                        'resolution': '720p', 'seconds': 5}) ==
              math.ceil(CR.VIDEO_RATE_PER_SECOND[m]['720p'] * 5)
              for m in CR.SWAP_MODELS))
    check('a swap on no model named is priced on the default one',
          CR.quote({'kind': 'swap', 'resolution': '720p', 'seconds': 5}) ==
          CR.quote({'kind': 'swap', 'model': CR.DEFAULT_SWAP_MODEL,
                    'resolution': '720p', 'seconds': 5}))
    check('a swap is billed for every second of the clip it was given',
          CR.quote({'kind': 'swap', 'resolution': '720p', 'seconds': 7}) >
          CR.quote({'kind': 'swap', 'resolution': '720p', 'seconds': 5}))
    for m in CR.SWAP_MODELS:
        cost = CR.VIDEO_COST_USD_PER_SECOND[m]['720p'] * 5
        check(f'a swap on {m} never sells under what the provider charges',
              CR.quote({'kind': 'swap', 'model': m, 'resolution': '720p',
                        'seconds': 5}) * CR.TOKEN_COST_USD >= cost)
    for bad in ({'kind': 'swap', 'resolution': '720p',
                 'seconds': CR.VIDEO_MAX_SECONDS + 1},
                {'kind': 'swap', 'resolution': '720p', 'seconds': 0}):
        try:
            CR.quote(bad)
            check(f'a swap of {bad["seconds"]}s is refused', False)
        except CR.PricingError:
            check(f'a swap of {bad["seconds"]}s is refused', True)
    check('the legacy Google path is priced too, so it is not a free bypass',
          CR.GOOGLE_IMAGE_TOKENS > 0)


def test_nothing_is_free():
    """max(1, ...) does far more work at a 20x coarser unit: a rung that rounds
    to nothing is a generation the creator is never charged for."""
    print('nothing is free')
    zero = []
    for model, rows in CR.IMAGE_PRICES.items():
        for res, price in rows.items():
            if price < 1:
                zero.append(('image', model, res, price))
    for model, rows in CR.VIDEO_PRICES.items():
        for res, durations in rows.items():
            for secs, price in durations.items():
                if price < 1:
                    zero.append(('video', model, res, secs, price))
    check('no image or video rung prices at zero tokens', not zero, str(zero))
    check('every add-on costs at least one token',
          all(v >= 1 for v in CR.ADDON_PRICES.values()))


def test_job_quotes():
    """Every combination the five job tiles can produce has a price, and none of
    them sells under what the provider bills. The picker reads its options out
    of the same tables this walks, so an option that appears there and cannot be
    quoted fails here rather than at submit."""
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
                            if price * CR.TOKEN_COST_USD + 1e-9 < cost:
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
          CR.ADDON_PRICES['audio'] * CR.TOKEN_COST_USD >=
          CR.ADDON_COST_USD['audio'])

    # A model that does not serve a job must not be quotable on it: the picker
    # offers the job's own list, so anything else arrived from a hand-made
    # request and would be billed on a model that never ran.
    for job, model in (('reel', 'ml-face-swap'), ('extend', 'wan-2-5'),
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


def test_allowances():
    """The marketing bullet and the enforced number must not drift apart, and an
    entry tier that cannot make a handful of clips is not sellable."""
    print('allowances')
    clip = CR.video_price(CR.DEFAULT_VIDEO_RESOLUTION, CR.DEFAULT_VIDEO_DURATION)
    for tier in ('starter', 'pro', 'agency'):
        tokens = CR.monthly_tokens(tier)
        check(f'{tier} includes {tokens} tokens '
              f'= {tokens} photos or {tokens // clip} clips', tokens > 0)
    check('a bigger plan always includes more',
          CR.monthly_tokens('starter') < CR.monthly_tokens('pro')
          < CR.monthly_tokens('agency'))
    check('starter can make more than a couple of clips a month',
          CR.monthly_tokens('starter') // clip >= 5)
    check('an unknown tier falls back rather than crashing',
          CR.monthly_tokens('nonesuch') == CR.DEFAULT_MONTHLY_TOKENS)


def test_ledger():
    print('ledger')
    db.init_db()
    s = db.SessionLocal()
    ws = 'ws-' + os.urandom(4).hex()
    soon = datetime.utcnow() + timedelta(days=20)

    db.token_grant(s, ws, 350, '2026-09', soon)
    check('grant posts', db.token_balance(s, ws) == 350)
    db.token_grant(s, ws, 350, '2026-09', soon)
    check('the same period cannot grant twice', db.token_balance(s, ws) == 350)

    db.token_purchase(s, ws, 1000, 'pay-1')
    db.token_purchase(s, ws, 1000, 'pay-1')
    check('a replayed webhook cannot credit twice',
          db.token_balance(s, ws) == 1350)

    db.token_debit(s, ws, 100, 'job-1')
    check('a spend lowers the balance', db.token_balance(s, ws) == 1250)

    # The point of the split: the allowance pays first, so when the month turns
    # the purchased balance is untouched.
    expired = db.token_balance(s, ws, at=soon + timedelta(days=1))
    check('spend drains the expiring allowance before purchased tokens',
          expired == 1000, f'got {expired}, wanted 1000')

    db.token_refund(s, ws, 'job-1')
    check('a refund restores the balance', db.token_balance(s, ws) == 1350)
    check('a refund returns tokens to the bucket they left',
          db.token_balance(s, ws, at=soon + timedelta(days=1)) == 1000)
    db.token_refund(s, ws, 'job-1')
    check('a second refund pays nothing', db.token_balance(s, ws) == 1350)

    check('an overdraw is refused and posts nothing',
          db.token_debit(s, ws, 99999, 'job-2') is False
          and db.token_balance(s, ws) == 1350)

    db.token_debit(s, ws, 500, 'job-3')
    check('a spend larger than the allowance takes the rest from purchased',
          db.token_balance(s, ws) == 850
          and db.token_balance(s, ws, at=soon + timedelta(days=1)) == 850)

    # A batch of four that came back with two: the two missing are owed back.
    db.token_debit(s, ws, 4, 'job-4')
    back = db.token_settle(s, ws, 'job-4', 2)
    check('a short batch returns the images that never arrived',
          back == 2 and db.token_balance(s, ws) == 848, f'returned {back}')
    check('settling the same job again pays nothing',
          db.token_settle(s, ws, 'job-4', 2) == 0 and db.token_balance(s, ws) == 848)
    check('a settle never charges more than was spent',
          db.token_settle(s, ws, 'job-4', 10) == 0 and db.token_balance(s, ws) == 848)
    s.close()


def test_equivalents():
    print('equivalents')
    eq = CR.equivalents(350)
    check(f'350 tokens reads as {eq["photos"]} photos or {eq["clips"]} clips',
          eq['photos'] == 350 // CR.image_price(CR.DEFAULT_IMAGE_MODEL, CR.DEFAULT_RESOLUTION)
          and eq['clips'] == 29)
    check('an empty balance reads as nothing',
          CR.equivalents(0) == {'photos': 0, 'clips': 0})
    check('a plan card counts photos on the cheapest still',
          CR.plan_equivalents(150) == {'photos': 150, 'clips': 12})
    check('an empty plan reads as nothing',
          CR.plan_equivalents(0) == {'photos': 0, 'clips': 0})
    cash = CR.cash_for_tokens(12, 'eur')
    check(f'a clip prices at {cash["symbol"]}{cash["amount"]}',
          cash['amount'] > 0 and cash['symbol'] == '€')


def test_stripe_minimums():
    print('stripe minimums')
    for size in CR.PACK_SIZES:
        for cur in CR.CURRENCIES:
            price = CR.pack_price(size, cur)
            check(f'pack {size} at {price} {cur} clears Stripe\'s minimum',
                  price >= CR.STRIPE_MIN_CHARGE[cur])
    for cur in CR.CURRENCIES:
        check(f'the test pack itself clears Stripe\'s {cur} minimum',
              CR.TEST_PACK_PRICES[cur] >= CR.STRIPE_MIN_CHARGE[cur])


def test_test_pack_is_not_for_sale():
    print('test pack')
    # The whole safety of the underpriced pack is that it cannot be reached by
    # asking for a token count -- only by asking for it by name.
    for cur in CR.CURRENCIES:
        check(f'the {CR.TEST_PACK_TOKENS} size still charges the real price in {cur}',
              CR.pack_price(CR.TEST_PACK_TOKENS, cur)
              == CR.PACK_PRICES[CR.TEST_PACK_TOKENS][cur])
        check(f'the real {cur} price is far above the test price',
              CR.pack_price(CR.TEST_PACK_TOKENS, cur) > CR.TEST_PACK_PRICES[cur] * 50)
    row = CR.test_pack_for('eur')
    real = CR.packs_for('eur')[0]
    check('the test pack renders in the same shape as a real one',
          set(row) == set(real))
    check('the test pack is flagged as one', row['test'] is True)
    check('a real pack is not', real['test'] is False)
    check('the test pack is asked for by name', row['id'] == CR.TEST_PACK_ID)
    check('no real pack answers to that name',
          CR.TEST_PACK_ID not in [p['id'] for p in CR.packs_for('eur')])
    check('every real pack id is its size',
          all(p['id'] == str(p['tokens']) for p in CR.packs_for('eur')))


if __name__ == '__main__':
    for fn in (test_margin_floor, test_currency_ladder,
               test_quote_covers_everything, test_prices_track_cost,
               test_nothing_is_free, test_job_quotes, test_allowances,
               test_ledger, test_equivalents,
               test_stripe_minimums, test_test_pack_is_not_for_sale):
        fn()
    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILED: {", ".join(FAILURES)}')
        sys.exit(1)
    print('all token tests passed')
