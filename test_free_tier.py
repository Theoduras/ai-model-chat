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
