"""Tests for the Free plan: its one-time credits, the nothing-goes-live gates,
and the welcome offer's window and checkout coupon.

Runs against a throwaway SQLite database; no Stripe calls leave the process.
Run with: python test_free_tier.py
"""
import os
import tempfile
import time
from datetime import timedelta

os.environ['DATABASE_URL'] = 'sqlite:///' + tempfile.mkdtemp() + '/free.db'
os.environ.setdefault('GEMINI_API_KEY', 'test')
os.environ['STRIPE_SECRET_KEY'] = 'sk_test_dummy'

import app as A
import db as D
import credits as CR

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ok   {name}')
    else:
        print(f'  FAIL {name} {detail}')
        FAILURES.append(name)


D.init_db()


def mkuser(email, tier='', status='unpaid'):
    s = D.SessionLocal()
    try:
        u = D.create_user(s, email, 'x' * 20)
        u.tier, u.status = tier, status
        s.commit()
        return u.id
    finally:
        s.close()


def client(uid, login_ago=0):
    c = A.app.test_client()
    with c.session_transaction() as sess:
        sess['user_id'] = uid
        sess['login_at'] = time.time() - login_ago
    return c


def balance(uid):
    s = D.SessionLocal()
    try:
        return D.token_balance(s, uid)
    finally:
        s.close()


print('tier')
check('free is a tier', A.FREE_TIER_KEY in A.TIERS)
check('no annual twin', A.FREE_TIER_KEY + A.ANNUAL_SUFFIX not in A.TIERS)
check('no monthly refill', CR.monthly_tokens(A.FREE_TIER_KEY) == 0)
check('not sold at checkout', A.FREE_TIER_KEY not in A.DEFAULT_TIER_ORDER)

print('activation and credits')
uid = mkuser('free@example.com')
c = client(uid)
r = c.post('/api/billing/free')
check('activates', r.status_code == 200 and r.get_json().get('ok'), r.get_data(as_text=True))
check('grants 15 credits once', balance(uid) == CR.FREE_CREDITS, balance(uid))
c.post('/api/billing/free')
me = c.get('/api/me').get_json()
check('second activation grants nothing', balance(uid) == CR.FREE_CREDITS, balance(uid))
check('/api/me shows the balance', me['usage']['tokens']['balance'] == CR.FREE_CREDITS, me['usage'])
with A.app.test_request_context():
    from flask import session
    session['user_id'] = uid
    u = A._current_user()
    check('no X on free', 'x' not in (A.user_capabilities(u).get('platforms') or []))
    check('spends from the grant', A._spend_tokens(u, 15, 'job-1'))
    check('16th credit refused', not A._spend_tokens(u, 1, 'job-2'))
check('checkout refuses free', c.post('/api/billing/checkout', json={
    'tier': 'free', 'provider': 'stripe'}).status_code == 400)

paid = mkuser('paid@example.com', tier='pro', status='active')
check('paid account cannot drop to free',
      client(paid).post('/api/billing/free').status_code == 409)

print('nothing goes live')
r = c.post('/api/fanvue/auth-url', json={})
check('platform write refused', r.status_code == 402 and r.get_json().get('free'), r.status_code)
check('platform page opens', c.get('/fanvue').status_code in (200, 302))
s = D.SessionLocal()
s.add(D.SavedPersona(slug='freegirl', name='Free Girl', owner_id=uid, config_json='{}',
                     prompt='You are Free Girl.'))
s.commit()
s.close()
fan = A.app.test_client()
r = fan.post('/chat', json={'message': 'hi', 'persona': 'freegirl'})
check('public chat refused', r.status_code == 403, r.status_code)
with A.app.test_request_context():
    from flask import session
    session['user_id'] = uid
    check('owner can test', not A._persona_live_blocked('freegirl'))

print('content creation only')
cid = mkuser('creator@example.com')
cc = client(cid)
cc.post('/api/billing/free')
ids = []
for n in range(3):
    r = cc.post('/api/characters', json={'name': f'Girl {n}', 'age': 24})
    ids.append((r.get_json() or {}).get('character', {}).get('id'))
    check(f'character {n + 1} created', r.status_code == 200 and ids[-1], r.get_data(as_text=True)[:200])
with A.app.test_request_context():
    from flask import session
    session['user_id'] = cid
    u = A._current_user()
    check('characters use no persona slot', A._persona_count(u) == 0, A._persona_count(u))
    check('free has no chatbot', not A.user_capabilities(u).get('chatbot'))
homes = [p for p in A.db_list_personas(owner_id=cid) if p['config'].get('studio_only')]
check('each character has a hidden home', len(homes) == 3, len(homes))
check('homes stay out of the persona list',
      cc.get('/api/personas').get_json() in ([], {'personas': []})
      or not any(p.get('slug') in {h['slug'] for h in homes}
                 for p in (cc.get('/api/personas').get_json() or [])), 'listed')
check('studio still lists them', len(cc.get('/api/personas?studio=1').get_json()) >= 3)
r = cc.post('/api/personas/new-girl', json={'name': 'New Girl'})
check('free cannot build a persona', r.status_code == 402 and r.get_json().get('free'), r.status_code)
check('free cannot write a real persona', cc.post('/api/personas/freegirl',
      json={'name': 'x'}).status_code in (402, 404))
check('free may write a hidden home', cc.post('/api/personas/' + homes[0]['slug'] + '/references',
      json={}).status_code != 402)
for path in ('/api/discord/status', '/api/instagram/status', '/api/growth/queue'):
    r = cc.post(path, json={})
    check(f'{path} refused for free', r.status_code == 402, r.status_code)
for page in ('/planner', '/discord', '/instagram', '/embed-setup'):
    check(f'{page} sends free to pricing',
          cc.get(page).status_code == 302 and '/pricing' in cc.get(page).headers['Location'])
check('free can open the studio', cc.get('/studio').status_code in (200, 302))
check('free can open characters', cc.get('/characters').status_code == 200)
tk = cc.get('/api/tokens').get_json()
check('free can buy tokens', tk.get('can_buy') is True, tk)
feat = cc.get('/api/features').get_json()
for key, tier in (feat.get('tiers') or feat).items():
    row = [f for f in tier['features'] if f['label'] == 'Character Creator'][0]
    check(f'{key} sells unlimited characters',
          row['detail'] == 'Unlimited Character Creator' and row['included'], row)
    check(f'{key} leads with a characters tile',
          tier['highlights'][0] == {'value': '\u221e', 'label': 'Characters'})
check('free row for AI personas is off',
      not [f for f in feat['tiers']['free']['features'] if f['label'] == 'AI personas'][0]['included'])

import base64, io
from PIL import Image
_blobs = {}
A.storage.put = lambda slug, data, mime, prefix='': _blobs.setdefault(f'{slug}/{prefix}/{len(_blobs)}', data) and f'{slug}/{prefix}/{len(_blobs) - 1}'
A.storage.signed_url = lambda path: None
A.storage.get = lambda path: _blobs.get(path)
A.storage.delete = lambda path: _blobs.pop(path, None)
buf = io.BytesIO(); Image.new('RGB', (40, 30), (200, 120, 60)).save(buf, 'PNG')
png = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
home = homes[0]['slug']
r = cc.post('/api/generate/locations', json={'persona': home, 'image': png})
locs = (r.get_json() or {}).get('locations') or []
check('location photo uploads', r.status_code == 200 and len(locs) == 1, r.get_data(as_text=True)[:200])
check('location photo is served back', cc.get(locs[0]['url']).status_code in (200, 302))
check('outfit list is separate', cc.get(f'/api/generate/outfits?persona={home}').get_json()['outfits'] == [])
check('big upload refused', cc.post('/api/generate/locations',
      json={'persona': home, 'image': 'x' * 16_000_001}).status_code == 413)
check('not-an-image refused', cc.post('/api/generate/locations',
      json={'persona': home, 'image': 'data:image/png;base64,AAAA'}).status_code == 400)
r = cc.post('/api/generate/prompt', json={'persona': home, 'shot': 'portrait', 'location': 'kitchen',
      'scene': 'kitchen', 'zoom': 'wide', 'location_ref': locs[0]['id'], 'direction': 'x'})
pr = r.get_json().get('prompt', '')
check('prompt route uses the location photo', 'exact place shown in the last reference image' in pr, pr[:300])
check('prompt route uses the zoom', 'framed wide' in pr)
r = cc.post('/api/generate/prompt', json={'persona': home, 'shot': 'portrait', 'location': 'kitchen',
      'scene': 'kitchen', 'location_ref': 'not-mine', 'direction': 'x'})
check('a foreign location id is ignored', 'exact place shown' not in r.get_json().get('prompt', ''))
r = cc.post('/api/generate/direction', json={'persona': home, 'mode': 'auto', 'shot': 'half',
      'scene': 'kitchen', 'location': 'kitchen', 'style': 'pov-selfie', 'expression': 'kissy-pout'})
check('auto direction follows the dropdowns',
      r.get_json().get('direction') == 'taking a pov selfie in the kitchen, kissy pout', r.get_data(as_text=True))
check('location photo deleted', cc.delete(locs[0]['url'].split('/image')[0] + f'?persona={home}').status_code == 200)

chars = {c['id']: c for c in cc.get('/api/characters').get_json()['characters']}
check('a hidden home is not shown as a linked persona', all(c['persona'] == '' for c in chars.values()))
r = cc.post(f'/api/characters/{ids[0]}/studio', json={})
check('studio route returns the hidden home', r.status_code == 200 and r.get_json()['slug'] == homes[0]['slug'])
r = cc.post(f'/api/characters/{ids[0]}/studio', json={'persona': True})
check('free cannot promote a character to a persona', r.status_code == 402 and r.get_json().get('free'), r.get_data(as_text=True))

pro = mkuser('prolimit@example.com', tier='pro', status='active')
pc = client(pro)
for n in range(7):
    r = pc.post('/api/characters', json={'name': f'Pro {n}', 'age': 24})
    check(f'pro character {n + 1} not capped', r.status_code == 200, r.status_code)
with A.app.test_request_context():
    from flask import session
    session['user_id'] = pro
    check('pro persona slots untouched', A._persona_count(A._current_user()) == 0)
pids = [c['id'] for c in pc.get('/api/characters').get_json()['characters']]
r = pc.post(f'/api/characters/{pids[0]}/studio', json={'persona': True})
check('pro can promote a character to a persona', r.status_code == 200, r.get_data(as_text=True))
with A.app.test_request_context():
    from flask import session
    session['user_id'] = pro
    check('promoted character now counts', A._persona_count(A._current_user()) == 1)
st = mkuser('startercap@example.com', tier='starter', status='active')
sc = client(st)
sids = [sc.post('/api/characters', json={'name': f'St {n}', 'age': 24}).get_json()['character']['id']
        for n in range(2)]
check('starter promotes its first', sc.post(f'/api/characters/{sids[0]}/studio',
      json={'persona': True}).status_code == 200)
r = sc.post(f'/api/characters/{sids[1]}/studio', json={'persona': True})
check('starter is capped at one real persona', r.status_code == 402, r.status_code)

print('offer window')
c = client(uid, login_ago=10)
o = c.get('/api/me').get_json()['offer']
check('pending before two minutes', o['state'] == 'pending' and o['show_in_seconds'] > 100, o)
check('cannot start early', c.post('/api/offer/start').status_code == 409)
c = client(uid, login_ago=A.OFFER_DELAY_SEC + 1)
o = c.post('/api/offer/start').get_json()
check('starts after two minutes', o['state'] == 'active' and o['seconds_left'] > 1790, o)
s = D.SessionLocal()
first = s.get(D.User, uid).offer_started_at
s.close()
c.post('/api/offer/start')
s = D.SessionLocal()
check('stamped once', s.get(D.User, uid).offer_started_at == first)
s.close()
check('never offered to paid', client(paid).get('/api/me').get_json()['offer']['state'] == 'ineligible')

print('checkout coupon')
sent = []


def fake_post(path, form):
    sent.append((path, dict(form)))
    if path == '/coupons':
        return {'id': 'co_offer'}
    return {'url': 'https://stripe.test/pay', 'id': 'cs_1'}


A._stripe_post = fake_post
r = c.post('/api/billing/checkout', json={'tier': 'starter', 'provider': 'stripe'})
form = [f for p, f in sent if p == '/checkout/sessions'][-1]
check('offer coupon attached', form.get('discounts[0][coupon]') == 'co_offer', form)
check('coupon is 15% once', any(p == '/coupons' and f['percent_off'] == '15'
                                and f['duration'] == 'once' for p, f in sent))
sent.clear()
c.post('/api/billing/checkout', json={'tier': 'starter' + A.ANNUAL_SUFFIX, 'provider': 'stripe'})
form = [f for p, f in sent if p == '/checkout/sessions'][-1]
check('annual not discounted', 'discounts[0][coupon]' not in form)

s = D.SessionLocal()
s.get(D.User, uid).offer_started_at = first - timedelta(minutes=A.OFFER_WINDOW_MIN + 1)
s.commit()
s.close()
check('expired after 30 minutes', c.get('/api/me').get_json()['offer']['state'] == 'expired')
sent.clear()
c.post('/api/billing/checkout', json={'tier': 'starter', 'provider': 'stripe'})
form = [f for p, f in sent if p == '/checkout/sessions'][-1]
check('no coupon once expired', 'discounts[0][coupon]' not in form)

print()
if FAILURES:
    print(f'{len(FAILURES)} failed')
    raise SystemExit(1)
print('all passed')
