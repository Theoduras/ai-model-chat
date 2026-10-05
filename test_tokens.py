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
        for res in CR.image_resolutions_for(model):
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
    import imagegen as IG
    check('imagegen runs explicit on exactly the models credits offers for it',
          set(CR.models_for_rating('nsfw')) == set(IG.EXPLICIT_MODELS))

    # The whole-job rounding is the reason a clip is 12 and not 15. A
    # per-second integer rate would round 2.269 up to 3 and overcharge by 25%.
    check('a 5s 720p clip rounds once for the whole job, not per second',
          CR.quote({'kind': 'video', 'resolution': '720p', 'seconds': 5}) == 17)
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
          and eq['clips'] == 20)
    check('an empty balance reads as nothing',
          CR.equivalents(0) == {'photos': 0, 'clips': 0})
    check('a plan card counts photos on the cheapest still',
          CR.plan_equivalents(150) == {'photos': 150, 'clips': 8})
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


def test_video_negative_prompt():
    print('video negative prompt')
    import imagegen as IG
    sent = []
    real_post = IG._post

    def fake_post(url, payload, headers, timeout=IG.TIMEOUT):
        sent.append(payload[1])
        return {'data': [{'videoURL': 'https://example.test/clip.mp4'}]}

    IG._post = fake_post
    try:
        prov = IG.RunwareProvider(key='test')
        spec = {'job': 'reel', 'prompt': 'a walk', 'seconds': 5,
                'reference_urls': ['data:image/png;base64,AAAA']}
        for model in ('wan-3-0', 'seedance-2-0', 'seedance-2-0-fast'):
            sent.clear()
            prov.submit_video(dict(spec, model=model))
            check(f'{model} is sent no negativePrompt',
                  'negativePrompt' not in sent[0])
            check(f'{model} still carries her references',
                  bool(sent[0].get('inputs', {}).get('referenceImages')))
        sent.clear()
        prov.submit_video(dict(spec, model='wan-2-5', reference_urls=[]))
        check('a flat model keeps its negativePrompt',
              'negativePrompt' in sent[0])

        attempts = []

        def refusing(url, payload, headers, timeout=IG.TIMEOUT):
            task = payload[1]
            attempts.append(sorted(task))
            if 'fps' in task:
                return {'errors': [{'message':
                        "Unsupported use of 'fps' parameter."}]}
            return {'data': [{'videoURL': 'https://example.test/clip.mp4'}]}

        IG._post = refusing
        prov._send([{'taskType': 'videoInference', 'fps': 24}])
        check('a refused optional key is stripped and the task resent',
              len(attempts) == 2 and 'fps' not in attempts[1])

        def refuses_refs(url, payload, headers, timeout=IG.TIMEOUT):
            return {'errors': [{'message':
                    "Unsupported use of 'referenceImages' parameter."}]}

        IG._post = refuses_refs
        try:
            prov._send([{'taskType': 'videoInference',
                         'inputs': {'referenceImages': ['x']}}])
            raised = False
        except IG.GenerationError:
            raised = True
        check('a refused identity key fails the job instead of running without it',
              raised)
    finally:
        IG._post = real_post


def test_video_prompt_is_not_cut():
    print('video prompt length')
    os.environ.setdefault('GEMINI_API_KEY', 'test')
    import app as A
    script = ' '.join(f'Shot {n}: she walks and looks back.' for n in range(1, 50))
    check('the script is longer than a still may carry',
          A.GEN_PROMPT_MAX < len(script) < A.GEN_VIDEO_PROMPT_MAX)
    reel = A._gen_spec('lilith', {'job': 'reel', 'prompt': script,
                                  'rating': 'sfw'}, None)
    check('a reel keeps the whole script', reel['prompt_extra'] == script)
    still = A._gen_spec('lilith', {'kind': 'image', 'prompt': script,
                                   'rating': 'sfw'}, None)
    check('a still is still capped at its own limit',
          len(still['prompt_extra']) == A.GEN_PROMPT_MAX)
    huge = A._gen_spec('lilith', {'job': 'reel', 'prompt': 'x' * 5000,
                                  'rating': 'sfw'}, None)
    check('a clip prompt is capped at the long limit',
          len(huge['prompt_extra']) == A.GEN_VIDEO_PROMPT_MAX)
    text = A.imagegen.build_reel_prompt(script, character=True)
    check('the built reel prompt carries the script to the end',
          script in text and text.endswith('realistic motion.'))


def test_swap_identity():
    print('swap identity')
    os.environ.setdefault('GEMINI_API_KEY', 'test')
    import app as A
    IG = A.imagegen
    p = IG.build_swap_prompt('she waves', roles=['face', 'body'])
    check('the identity clause is in the prompt', IG.IDENTITY_CLAUSE in p)
    check('the images are named by position',
          'Reference image 1 shows her face; reference image 2 shows her full body' in p)
    check('the creator\'s words come last', p.endswith('she waves'))
    check('no roles, no clause', IG.IDENTITY_CLAUSE not in IG.build_swap_prompt('x'))
    check('the replace model locks identity', IG.locks_identity('p-video-replace'))
    check('Kling does not', not IG.locks_identity('kling-3-0-mc'))
    check('replace sends body as well as face', not IG.wants_face_only('p-video-replace'))
    check('reference caps follow the model',
          IG.ref_cap('p-video-replace') == 4 and IG.ref_cap('kling-3-0-mc') == 1
          and IG.ref_cap('wan-3-0') == IG.PICK_REF_MAX)
    check('Kling is offered on a reel', 'kling-3-0-mc' in CR.JOB_MODELS['reel'])
    char = {'body_type': 'female', 'views': {k: {} for k in
            ('body_front', 'face_front', 'face_profile')}}
    keys, roles = A._clip_views(char, ['body_front', 'face_profile', 'face_front'],
                                'p-video-replace')
    check('face leads, body follows', roles == ['face', 'face', 'body'], roles)
    check('Kling gets the body view alone',
          A._clip_views(char, ['face_front', 'body_front'], 'kling-3-0-mc')[0]
          == ['body_front'])


def test_clip_library_and_places():
    print('clip library and places')
    os.environ.setdefault('GEMINI_API_KEY', 'test')
    import app as A
    IG = A.imagegen
    check('clips are stored outside the staging sweep',
          A.VIDEO_SOURCE_PREFIX.startswith('kept/'))
    check('P-Video-Animate is only the explicit motion-clip Animate',
          IG.EXPLICIT_MOTION_MODEL == 'p-video-animate'
          and [j for j, m in CR.JOB_MODELS.items() if 'p-video-animate' in m] == ['animate']
          and 'p-video-animate' in CR.VIDEO_PRICES)
    check('Wan 2.2 Animate, not in the catalogue, is offered nowhere',
          all('wan-2-2-animate' not in m for m in CR.JOB_MODELS.values()))
    check('Omni is a swap and reel option, not the default',
          'kling-3-0-omni' in CR.JOB_MODELS['swap'] and CR.DEFAULT_SWAP_MODEL != 'kling-3-0-omni'
          and IG.ref_cap('kling-3-0-omni') == 4)
    p = IG.build_swap_prompt('x', preserve=True, roles=['face', 'body', 'location'],
                             place='a rooftop bar')
    check('a place drops the keep-the-background wording',
          IG.PRESERVE_CLAUSE not in p and 'Set in: a rooftop bar.' in p
          and 'shows the location' in p and 'lighting exactly' not in p)
    check('no place keeps it', IG.PRESERVE_CLAUSE in IG.build_swap_prompt('x', preserve=True))
    check('Omni names images with @', '@Image1' in IG.build_swap_prompt(
        '', roles=['face'], at_images=True))
    sent = []
    real = IG._post
    IG._post = lambda url, payload, headers, timeout=IG.TIMEOUT: (
        sent.append(payload[1]) or {'data': [{'videoURL': 'https://example.test/c.mp4'}]})
    try:
        prov = IG.RunwareProvider(key='test')
        base = {'job': 'swap', 'model': 'kling-3-0-mc', 'source_url': 'https://x/c.mp4',
                'reference_urls': ['data:image/png;base64,AAAA']}
        prov.submit_video(dict(base))
        kling = sent[-1]['providerSettings']['klingai']
        check('Kling 3.0 keeps the clip sound', kling.get('keepOriginalSound') is True)
        check('no background choice without a place', 'backgroundSource' not in kling)
        prov.submit_video(dict(base, place='a beach'))
        check('a place takes the background from her photo',
              sent[-1]['providerSettings']['klingai'].get('backgroundSource') == 'input_image')
        calls = []

        def refuse(url, payload, headers, timeout=IG.TIMEOUT):
            t = payload[1]
            calls.append(t)
            if 'backgroundSource' in t.get('providerSettings', {}).get('klingai', {}):
                return {'errors': [{'message': "Unsupported use of 'backgroundSource' parameter."}]}
            return {'data': [{'videoURL': 'https://example.test/c.mp4'}]}

        IG._post = refuse
        prov.submit_video(dict(base, place='a beach'))
        check('a refused provider setting is dropped and resent',
              len(calls) == 2 and 'backgroundSource' not in
              calls[-1]['providerSettings']['klingai'])
    finally:
        IG._post = real


def test_character_plus_vault_photos():
    print('character views plus vault photos')
    os.environ.setdefault('GEMINI_API_KEY', 'test')
    import app as A
    char = {'body_type': 'female', 'views': {'face_front': {}, 'body_front': {}}}
    real_snap, real_row = A._character_snapshot, A._media_row
    A._character_snapshot = lambda slug: char
    A._media_row = lambda slug, i: {'approved': i != 'bad'}
    try:
        spec = {}
        A._gen_identity('lilith', {'identity': 'character',
                                   'character_views': ['face_front', 'body_front'],
                                   'identity_media': ['a', 'bad', 'b', 'c']}, spec, 3)
        check('views and vault photos are both kept', spec['character_views']
              == ['face_front', 'body_front'] and spec['identity_media'] == ['a', 'b', 'c'][:3])
        check('an unapproved photo is dropped', 'bad' not in spec['identity_media'])
    finally:
        A._character_snapshot, A._media_row = real_snap, real_row


def test_wan22_on_runpod():
    print('wan 2.2 on runpod')
    import importlib
    import imagegen as IG
    import credits as CR
    check('Wan 2.2 is not offered without a RunPod key',
          bool(IG.RUNPOD_API_KEY) or all('wan-2-2' not in m for m in CR.JOB_MODELS.values()))
    os.environ['RUNPOD_API_KEY'] = 'test'
    try:
        IG2 = importlib.reload(IG)
        check('with the key, Wan 2.2 is an Animate model only',
              [j for j, r in IG2.VIDEO_JOBS.items() if 'wan-2-2' in r['models']] == ['animate'])
        check('Wan 2.2 runs on RunPod',
              IG2.provider_name_for({'kind': 'video', 'job': 'animate', 'model': 'wan-2-2'}) == 'runpod')
        prov = IG2.RunPodProvider()
        spec = {'reference_url': 'https://x/still.jpg', 'prompt': 'p', 'aspect': '9:16'}
        check('an explicit clip runs with the safety checker off',
              prov.payload(dict(spec, explicit=True))['input']['enable_safety_checker'] is False)
        check('a safe clip runs with it on',
              prov.payload(spec)['input']['enable_safety_checker'] is True)
        check('Wan 2.6 runs on RunPod too',
              IG2.provider_name_for({'kind': 'video', 'job': 'animate', 'model': 'wan-2-6-rp'}) == 'runpod')
        w26 = prov.payload(dict(spec, model='wan-2-6-rp', explicit=True, seconds=10))['input']
        check('an explicit Wan 2.6 clip runs with the checker off at a size it serves',
              w26['enable_safety_checker'] is False and w26['size'] == '720p'
              and w26['duration'] == 10)
        check('a job id finds its way back to its own endpoint',
              prov._endpoint('wan-2-6-rp|abc') == (IG2.RUNPOD_ENDPOINTS['wan-2-6-rp'], 'abc')
              and prov._endpoint('old') == (IG2.RUNPOD_ENDPOINTS['wan-2-2'], 'old'))
        check('a negative term the prompt asks for is dropped, the underage ones never',
              IG2.negative_for({'negative': 'zoom in, pan, teen, blurry',
                                'prompt': 'slow zoom in while panning; teen'}) == 'teen, blurry')
        lo = prov.payload(dict(spec, model='wan-2-2-lora', seconds=8, loras=[
            {'name': 'n', 'high': 'https://h/a.safetensors', 'low': '', 'scale': 0.8}]))['input']
        check('a LoRA clip sends each LoRA to its own noise stage at its strength',
              lo['high_noise_loras'] == [{'path': 'https://h/a.safetensors', 'scale': 0.8}]
              and lo['low_noise_loras'] == [] and lo['duration'] == 8)
        gv = prov.payload(dict(spec, model='wan-2-2-gv', seconds=5, seed=7, loras=[
            {'name': 'n', 'high': 'https://h/a.safetensors', 'low': 'https://h/b.safetensors',
             'scale': 0.8}]))['input']
        check('our own endpoint gets the same LoRA links, one pair per LoRA',
              gv['lora_pairs'] == [{'high': 'https://h/a.safetensors', 'low': 'https://h/b.safetensors',
                                    'high_weight': 0.8, 'low_weight': 0.8}]
              and gv['seed'] == 7 and gv['length'] == 81
              and IG2.LORA_VIDEO_MODELS == ('wan-2-2-lora', 'wan-2-2-gv', 'h3-gv'))
        h3 = IG2.h3_payload(dict(spec, model='h3-gv', seconds=5, seed=7, loras=[
            {'name': 'n', 'high': 'https://h/c.safetensors', 'low': '', 'scale': 0.6}]), 'img')['input']
        wf = h3['workflow']
        check('H3 gets a whole workflow: its LoRA after turbo, 17n+5 frames, audio in the clip',
              wf['lora0']['inputs'] == {'model': ['turbo', 0], 'lora_name': 'https://h/c.safetensors',
                                        'strength_model': 0.6}
              and wf['guider']['inputs']['model'] == ['lora0', 0]
              and wf['cond']['inputs']['length'] == 124 and wf['noise']['inputs']['noise_seed'] == 7
              and wf['cond']['inputs']['width'] % 32 == 0 and wf['cond']['inputs']['height'] % 32 == 0
              and wf['video']['inputs']['audio'] == ['audio', 0]
              and h3['images'] == [{'name': 'still.png', 'image': 'img'}]
              and 'h3-gv' not in IG2.SILENT_MODELS)
        IG2.CIVITAI_TOKEN = 'tok'
        check('a Civitai link carries the token, any other link does not',
              IG2.lora_url('https://civitai.com/api/download/models/1') ==
              'https://civitai.com/api/download/models/1?token=tok'
              and IG2.lora_url('https://h/a') == 'https://h/a')
        h3 = IG2.h3_payload({'aspect': '9:16', 'seconds': 15}, 'A', 'B')['input']
        check('a later H3 part starts on the last frame and holds her photo as <Picture 1>',
              [i['name'] for i in h3['images']] == ['still.png', 'ref.png']
              and h3['workflow']['guide']['inputs']['frame_idx'] == 0
              and h3['workflow']['guider']['inputs']['conditioning'] == ['guide', 0])
        if IG2.HAS_FFMPEG:
            check('a long clip splits into whole parts the model serves',
                  IG2.chain_plan('wan-2-2', 30) == [15, 15]
                  and IG2.chain_plan('wan-2-2', 20) == [10, 10]
                  and IG2.chain_plan('wan-2-2-lora', 24) == [8, 8, 8]
                  and IG2.chain_plan('wan-2-2-gv', 10) == [5, 5]
                  and IG2.chain_plan('wan-2-2-gv', 32) == [8, 8, 8, 8]
                  and IG2.chain_plan('h3-gv', 120) == [15] * 8
                  and IG2.chain_plan('h3-gv', 45) == [15, 15, 15]
                  and IG2.chain_plan('wan-2-2', 10) is None
                  and IG2.chain_plan('seedance-2-0-fast', 30) is None)
        check('the payload carries the fields RunPod requires',
              'num_inference_steps' in prov.payload(spec)['input'])
        check('the clip runs the length it was priced at',
              prov.payload(dict(spec, seconds=10))['input']['duration'] == 10)
        check('a finished job hands back the video URL',
              prov._read({'status': 'COMPLETED', 'output': {'video_url': 'https://v/a.mp4'}}).urls
              == ['https://v/a.mp4'])
        check('a failed job fails',
              prov._read({'status': 'FAILED', 'error': 'x'}).status == 'failed')
    finally:
        os.environ.pop('RUNPOD_API_KEY', None)
        importlib.reload(IG)
    import requests as _rq
    real_post = _rq.post

    class _Resp:
        status_code = 200

        def __init__(self, text):
            self.text = text

        def json(self):
            return {'choices': [{'message': {'content': self.text}}]}

    os.environ['RUNPOD_API_KEY'] = 'test'
    try:
        _rq.post = lambda *a, **k: _Resp('<think>hm</think>["slow wave", "smile", 3]')
        check('motion ideas are parsed from the answer, junk dropped',
              IG.suggest_motions('seated on a bed') == ['slow wave', 'smile'])
        _rq.post = lambda *a, **k: _Resp('<think>plan</think> She waves slowly.')
        check('a written prompt loses its thinking',
              IG.write_motion_prompt('wave') == 'She waves slowly.')
        style = IG.lora_style({'loras': [{'trigger': 'abc, xyz', 'examples': 'ex one'}]})
        check('a LoRA job carries its trigger words and examples',
              style == {'triggers': ['abc', 'xyz'], 'examples': 'ex one'}
              and IG.lora_style({'loras': [{'trigger': '', 'examples': ''}]}) is None)
        _rq.post = lambda *a, **k: _Resp('xyz, she waves slowly')
        check('a styled prompt gets the trigger words it left out',
              IG.write_motion_prompt('wave', style=style) == 'abc, xyz, she waves slowly')
        _rq.post = lambda *a, **k: _Resp("I'm sorry, I can't help with that.")
        why = {}
        check('a refusal is reported, not used as the prompt',
              IG.write_motion_prompt('wave', why=why) == ''
              and why.get('reason', '').startswith('refused'))
        calls = []

        def _slow_then_ok(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise _rq.exceptions.ReadTimeout('cold start')
            return _Resp('She waves slowly.')
        _rq.post = _slow_then_ok
        check('a cold-start timeout is retried once',
              IG.write_motion_prompt('wave') == 'She waves slowly.' and len(calls) == 2)
        class _Err:
            status_code, text = 500, '{"status":500}'

        class _Native:
            status_code, text = 200, ''

            def json(self):
                return {'output': [{'choices': [{'tokens': ['She ', 'waves.']}]}]}

        seen = []

        def _flaky(url, *a, **k):
            seen.append(url)
            return _Native() if url.endswith('/runsync') else _Err()
        _rq.post = _flaky
        check('two server errors fall back to the native route',
              IG.write_motion_prompt('wave') == 'She waves.'
              and len(seen) == 3 and seen[-1].endswith('/runsync'))
        check('the native prompt carries the system and user text',
              '<|im_start|>system' in IG._qwen_chat_text([{'role': 'system', 'content': 'S'},
                                                          {'role': 'user', 'content': 'U'}]))
        _rq.post = lambda *a, **k: (_ for _ in ()).throw(OSError('down'))
        check('a failed helper gives nothing rather than failing the clip',
              IG.write_motion_prompt('wave') == '' and IG.suggest_motions('u') == [])
    finally:
        _rq.post = real_post
        os.environ.pop('RUNPOD_API_KEY', None)
    check('a 30s chained clip is priced, a 30s single clip is not',
          CR.video_price('720p', 30, model='wan-2-2') * CR.TOKEN_COST_USD >= 1.80 - 1e-9)
    try:
        CR.video_price('720p', 30, model='seedance-2-0-fast')
        check('a 30s single clip is not priced', False)
    except CR.PricingError:
        check('a 30s single clip is not priced', True)
    for secs, cost in ((5, 0.50), (10, 1.00), (15, 1.50)):
        check(f'a {secs}s Wan 2.6 clip is priced at no less than RunPod charges for it',
              CR.video_price('720p', secs, model='wan-2-6-rp') * CR.TOKEN_COST_USD >= cost - 1e-9)
    for secs, cost in ((5, 0.30), (8, 0.48), (10, 0.60), (15, 0.90)):
        check(f'a {secs}s Wan 2.2 clip is priced at no less than RunPod charges for it',
              CR.video_price('720p', secs, model='wan-2-2') * CR.TOKEN_COST_USD >= cost - 1e-9)


if __name__ == '__main__':
    for fn in (test_margin_floor, test_currency_ladder,
               test_quote_covers_everything, test_prices_track_cost,
               test_nothing_is_free, test_job_quotes, test_allowances,
               test_ledger, test_equivalents,
               test_stripe_minimums, test_test_pack_is_not_for_sale,
               test_video_negative_prompt, test_video_prompt_is_not_cut,
               test_swap_identity, test_clip_library_and_places,
               test_character_plus_vault_photos, test_wan22_on_runpod):
        fn()
    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILED: {", ".join(FAILURES)}')
        sys.exit(1)
    print('all token tests passed')
