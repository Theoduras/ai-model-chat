"""Tests for referral links, the 20%-off-first-month discount, the 5%
commission and admin-issued trials.

Runs against a throwaway SQLite database; no Stripe calls leave the process.
Run with: python test_referrals.py
"""
import os
import tempfile

os.environ['DATABASE_URL'] = 'sqlite:///' + tempfile.mkdtemp() + '/ref.db'
os.environ.setdefault('GEMINI_API_KEY', 'test')
os.environ['STRIPE_SECRET_KEY'] = 'sk_test_dummy'

import app as A
import db as D

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ok   {name}')
    else:
        print(f'  FAIL {name} {detail}')
        FAILURES.append(name)


D.init_db()


def mkuser(email, tier='pro', status='active'):
    s = D.SessionLocal()
    try:
        u = D.create_user(s, email, 'x' * 20)
        u.tier, u.status = tier, status
        s.commit()
        return u.id
    finally:
        s.close()


ref_id = mkuser('referrer@example.com')
new_id = mkuser('newbie@example.com', tier='', status='unpaid')
free_id = mkuser('free@example.com', tier=A.DEMO_TIER_KEY, status='active')

print('referral codes')
s = D.SessionLocal()
ref = s.get(D.User, ref_id)
code = A._referral_code_for(s, ref)
s.commit()
check('minted for a paid member', bool(code))
check('stable across calls', A._referral_code_for(s, ref) == code)
check('demo accounts cannot refer', not A._ref_eligible(s.get(D.User, free_id)))
check('unpaid accounts cannot refer', not A._ref_eligible(s.get(D.User, new_id)))
s.close()

print('discount scope')
check('monthly plan discounts', A._ref_discountable('starter'))
check('annual plan does not', not A._ref_discountable('starter' + A.ANNUAL_SUFFIX))
check('demo does not', not A._ref_discountable(A.DEMO_TIER_KEY))

print('the link')
c = A.app.test_client()
r = c.get('/r/' + code)
check('redirects to pricing', r.status_code == 302 and '/pricing' in r.headers['Location'])
check('sets the cookie', 'ref=' in (r.headers.get('Set-Cookie') or ''))
c.get('/r/' + code)
c.get('/r/nosuchcode')
s = D.SessionLocal()
stats = D.referral_click_stats(s, code)
s.close()
check('counts the clicks', stats['clicks'] == 2, stats)
check('unknown codes are not counted', D.referral_click_stats(D.SessionLocal(), 'nosuchcode')['clicks'] == 0)

print('pricing page')
body = c.get('/pricing').get_data(as_text=True)
check('shows the discount on a referred visit', 'off your first month' in body)
plain = A.app.test_client().get('/pricing').get_data(as_text=True)
check('full price without a referral', 'off your first month' not in plain)

print('the commission')
s = D.SessionLocal()
u = s.get(D.User, new_id)
pay = D.Payment(user_id=u.id, tier='starter', provider='stripe', amount='49',
                currency='EUR', order_id='o1', ref_code=code, status='paid')
s.add(pay)
s.flush()
A._activate_plan(s, u, 'starter')
A._award_referral(s, u, pay, 3920)   # 49.00 less 20%, in cents
s.commit()
earnings = D.list_referral_earnings(s, ref_id)
check('one earning recorded', len(earnings) == 1, earnings)
check('5% of what was paid', earnings and earnings[0].amount_cents == 196,
      earnings and earnings[0].amount_cents)
check('referred account is tagged', s.get(D.User, new_id).referred_by == code)
check('pending without a Stripe customer', earnings[0].status == 'pending')

A._award_referral(s, u, pay, 3920)
s.commit()
check('never paid twice', len(D.list_referral_earnings(s, ref_id)) == 1)

summary = A._referral_summary(s, s.get(D.User, ref_id))
s.close()
check('counter shows the clicks', summary['clicks'] == 2, summary['clicks'])
check('counter shows the signup', summary['signups'] == 1)
check('counter shows the money', summary['earned'] == 1.96, summary['earned'])
check('counter shows it as pending', summary['pending'] == 1.96)

print('trials')
trial_id = mkuser('trial@example.com', tier='', status='unpaid')
s = D.SessionLocal()
t = s.get(D.User, trial_id)
check('granted', A._grant_trial(s, t) == '')
s.commit()
check('on the starter plan', t.tier == A.TRIAL_TIER and t.status == 'active')
days = (t.expires_at - t.created_at).days
check('for seven days', days == A.TRIAL_DAYS, days)
check('refused a second time', A._grant_trial(s, t) != '')
paid = s.get(D.User, ref_id)
check('refused on a paying account', A._grant_trial(s, paid) != '')
s.close()

print()
if FAILURES:
    print(f'{len(FAILURES)} failed: ' + ', '.join(FAILURES))
    raise SystemExit(1)
print('all referral tests passed')
