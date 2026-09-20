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
            for addons in ((), ('faceswap',), ('faceswap', 'upscale')):
                try:
                    CR.quote({'kind': 'image', 'model': model, 'resolution': res,
                              'addons': addons, 'batch': 4})
                except CR.PricingError:
                    missing.append((model, res, addons))
    for res in CR.VIDEO_RESOLUTIONS:
        for secs in CR.VIDEO_DURATIONS:
            try:
                CR.quote({'kind': 'video', 'resolution': res, 'seconds': secs})
            except CR.PricingError:
                missing.append((res, secs))
    check('every model / resolution / duration the UI offers is priced',
          not missing, str(missing))

    for bad in ({'kind': 'image', 'model': 'nope', 'resolution': '1024x1024'},
                {'kind': 'video', 'resolution': '4k', 'seconds': 5},
                {'kind': 'video', 'resolution': '720p', 'seconds': 7},
                {'kind': 'hologram'}):
        try:
            CR.quote(bad)
            check(f'unpriced spec {bad} is refused', False)
        except CR.PricingError:
            check(f'unpriced spec {bad.get("kind")} is refused', True)


def test_prices_track_cost():
    print('prices track cost')
    check('a credit is the rounded-up cost slice',
          CR.credits_for_cost(0.0013) == 1 and CR.credits_for_cost(0.0021) == 2)
    check('batch multiplies, add-ons are per image',
          CR.quote({'kind': 'image', 'model': 'sdxl',
                    'resolution': '1024x1536', 'addons': ('faceswap',),
                    'batch': 4}) == 12)
    check('a 720p 5s clip is 150 credits',
          CR.quote({'kind': 'video', 'resolution': '720p', 'seconds': 5}) == 150)
    check('the legacy Google path is priced too, so it is not a free bypass',
          CR.GOOGLE_IMAGE_CREDITS > 0)


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
               test_prices_track_cost, test_ledger, test_equivalents):
        fn()
    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILED: {", ".join(FAILURES)}')
        sys.exit(1)
    print('all credit tests passed')
