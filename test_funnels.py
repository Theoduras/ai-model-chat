"""Tests for the PPV funnel engine, fan scoring and the guardrails.

Pure logic — no API key, no database, no network.
Run with: python test_funnels.py
"""
import random

import funnels as F

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ok   {name}')
    else:
        print(f'  FAIL {name} {detail}')
        FAILURES.append(name)


print('classification')
c = F.parse_classification('{"type":"GF","confidence":82,"why":"asks about her day"}')
check('parses json', c and c['type'] == 'GF' and c['confidence'] == 82, c)
check('parses bare code', (F.parse_classification('I think SK') or {}).get('type') == 'SK')
check('refuses ambiguity', F.parse_classification('could be GF or CO') is None)
check('refuses junk', F.parse_classification('who knows') is None)
check('clamps confidence',
      F.parse_classification('{"type":"WH","confidence":900}')['confidence'] == 100)
check('classify at 3 msgs', F.needs_classification('', 0, 3))
check('not before', not F.needs_classification('', 0, 2))
check('reclassify every 10', F.needs_classification('GF', 5, 15))
check('not mid-window', not F.needs_classification('GF', 5, 12))
check('reclassify on purchase', F.needs_classification('GF', 5, 6, purchased=True))

print('assignment')
rng = random.Random(7)
check('matrix picks the prior', F.assign_by_matrix('CO', rng=rng) == 'F7')
check('matrix for DO', F.assign_by_matrix('DO', rng=rng) == 'F4')
check('matrix for SK', F.assign_by_matrix('SK', rng=rng) == 'F10')
check('zero prior never assigned',
      'F5' not in F.eligible_funnels('SK') and 'F9' not in F.eligible_funnels('WH'))
check('F6 is not an arm', 'F6' not in F.ARMS and 'F6' not in F.eligible_funnels('GF'))
check('unlocked list respected',
      F.eligible_funnels('GF', unlocked=['F1', 'F2']) == ['F1', 'F2'])
check('no funnel when nothing unlocked', F.assign_by_matrix('GF', unlocked=['F5xx']) == '')

# Evidence has to be able to beat a strong prior.
post = {f: (1, 1) for f in F.ARMS}
post['F7'] = (1, 60)      # the matrix favourite, losing badly
post['F3'] = (40, 2)      # a cell that keeps paying
picks = [F.assign_by_thompson('CO', post, rng=random.Random(i)) for i in range(200)]
check('thompson abandons a losing favourite', picks.count('F7') < 20, picks.count('F7'))
check('thompson finds the winner', picks.count('F3') > 120, picks.count('F3'))
flat = {f: (1, 1) for f in F.ARMS}
explore = {F.assign_by_thompson('GF', flat, rng=random.Random(i)) for i in range(60)}
check('thompson still explores', len(explore) >= 4, explore)
check('thompson respects zero priors',
      all(F.assign_by_thompson('WH', flat, rng=random.Random(i)) != 'F9'
          for i in range(80)))

print('reward + posteriors')
check('revenue is the reward', F.reward_value(2500, False, 1000) == 2500)
check('churn is penalised', F.reward_value(2500, True, 1000) == 1500)
check('churn can make it negative', F.reward_value(500, True, 1000) == -500)
check('crs jump counts against it', F.reward_value(1000, False, 1000, crs_jump=30) == 500)
check('win updates alpha', F.posterior_update(3, 2, 100) == (4, 2))
check('loss updates beta', F.posterior_update(3, 2, -100) == (3, 3))
check('zero is a loss', F.posterior_update(1, 1, 0) == (1, 2))
check('n=29 is not trusted', not F.cell_is_trusted(29))
check('n=30 is', F.cell_is_trusted(30))

print('fan rank score')
whale = F.fan_rank_score(spend_30d=30000, lifetime_spend=90000, tips=20000,
                         msgs_7d=20, avg_msg_len=90, days_since_msg=0,
                         ppv_sent=10, ppv_bought=8)
lurker = F.fan_rank_score(spend_30d=0, lifetime_spend=0, tips=0, msgs_7d=2,
                          avg_msg_len=8, days_since_msg=9, ppv_sent=6, ppv_bought=0)
check('whale outranks lurker', whale > lurker + 40, (whale, lurker))
check('scores stay in range', 0 <= whale <= 100 and 0 <= lurker <= 100, (whale, lurker))
quiet_whale = F.fan_rank_score(spend_30d=0, lifetime_spend=90000, tips=0,
                               msgs_7d=0, avg_msg_len=0, days_since_msg=30,
                               ppv_sent=10, ppv_bought=8)
check('recent beats lifetime', quiet_whale < whale - 30, (quiet_whale, whale))
one_big = F.fan_rank_score(spend_30d=50000, lifetime_spend=50000, tips=0,
                           msgs_7d=1, avg_msg_len=20, days_since_msg=1)
steady = F.fan_rank_score(spend_30d=12000, lifetime_spend=40000, tips=6000,
                          msgs_7d=12, avg_msg_len=80, days_since_msg=1,
                          ppv_sent=8, ppv_bought=5)
check('one big custom does not own the top spot', steady > one_big, (steady, one_big))
check('chargeback freezes at 0',
      F.fan_rank_score(spend_30d=90000, lifetime_spend=90000, chargeback=True) == 0)
check('conversion needs 3 sent',
      F.fan_rank_score(ppv_sent=2, ppv_bought=2) == F.fan_rank_score(ppv_sent=0))

print('tiers')
check('S needs rank and spend', F.tier_for(90, 20000, 1) == 'S')
check('S denied on spend', F.tier_for(90, 5000, 1) == 'A')
check('A tier', F.tier_for(70, 5000, 12) == 'A')
check('B needs a recent reply', F.tier_for(50, 500, 40) == 'B')
check('no reply drops to C', F.tier_for(50, 500, 40, replied_within_7d=False) == 'C')
check('unsubbed is D', F.tier_for(90, 20000, 1, subscribed=False) == 'D')
check('promotion is immediate', F.apply_hysteresis('B', 'S', 0) == 'S')
check('demotion waits 14 days', F.apply_hysteresis('A', 'C', 3) == 'A')
check('demotion lands after 14', F.apply_hysteresis('A', 'C', 14) == 'C')
check('C keeps only the low-ask funnel', F.TIER_FUNNELS['C'] == ['F9'])
check('B cannot be quoted a custom', 'F4' not in F.TIER_FUNNELS['B'])

print('churn')
sig = F.churn_signals(latency_now_h=10, latency_prev_h=4, len_now=8,
                      len_baseline=40, consecutive_opens_no_buy=3,
                      tipped_before=True, tips_recent=0, auto_renew=False,
                      renews_in_days=3, days_since_msg=6)
check('signals fire', all(sig[k] for k in ('latency_doubled', 'length_shrunk',
                                           'opens_no_buy', 'tips_stopped',
                                           'auto_renew_off', 'rebill_soon_quiet')), sig)
check('high crs means save', F.churn_mode(F.churn_risk_score(sig)) == 'save')
calm = F.churn_signals(latency_now_h=2, latency_prev_h=2, len_now=40,
                       len_baseline=40, auto_renew=True, days_since_msg=0)
check('calm fan is normal', F.churn_mode(F.churn_risk_score(calm)) == 'normal')
check('crs caps at 100', F.churn_risk_score({k: True for k in F.CRS_WEIGHTS}) == 100)
check('watch band', F.churn_mode(45) == 'watch')
check('no baseline, no false alarm',
      not F.churn_signals(len_now=5, len_baseline=0)['length_shrunk'])
check('winback day 1', F.winback_due(1, 0))
check('winback not early', not F.winback_due(3, 1))
check('winback day 4', F.winback_due(4, 1))
check('winback stops at 5', not F.winback_due(500, 5))
check('first two are not offers', not F.winback_is_offer(0) and not F.winback_is_offer(1))
check('day 12 is an offer', F.winback_is_offer(2))

print('guardrails')
ok, why, _ = F.pitch_gate()
check('default allows', ok, why)
check('two ignored stops', not F.pitch_gate(ignored_pitches=2)[0])
check('one ignored does not', F.pitch_gate(ignored_pitches=1)[0])
check('cap stops', not F.pitch_gate(spent_today_cents=15000)[0])
check('under cap allows', F.pitch_gate(spent_today_cents=14999)[0])
check('distress stops', not F.pitch_gate(distress_until_ts=100, now_ts=50)[0])
check('distress expires', F.pitch_gate(distress_until_ts=100, now_ts=101)[0])
check('save mode stops', not F.pitch_gate(crs=60)[0])
ok, why, mx = F.pitch_gate(crs=40)
check('watch mode caps at T1', ok and mx == 'T1', (ok, why, mx))
check('hostile stops', not F.pitch_gate(hostile_stop=True)[0])
check('lapsed stops', not F.pitch_gate(tier='D')[0])
ok, why, mx = F.pitch_gate(chargeback=True)
check('chargeback caps at T1', ok and mx == 'T1', (ok, mx))
check('F10 sells nothing first session',
      not F.pitch_gate(funnel_id='F10', first_session=True)[0])
check('F10 sells later', F.pitch_gate(funnel_id='F10', first_session=False)[0])
check('F9 caps at T2', F.pitch_gate(funnel_id='F9')[2] == 'T2')
check('C tier is F9 only', not F.pitch_gate(tier='C', funnel_id='F2')[0])
check('C tier allows F9', F.pitch_gate(tier='C', funnel_id='F9')[0])
# Order matters: a hard stop must win over a cap.
check('distress beats chargeback cap',
      not F.pitch_gate(distress_until_ts=100, now_ts=1, chargeback=True)[0])
check('tier clamps down', F.cap_tier('T4', 'T1') == 'T1')
check('tier untouched under the cap', F.cap_tier('T1', 'T2') == 'T1')
check('no cap, no change', F.cap_tier('T4', None) == 'T4')

print('distress detection')
for phrase in ["i can't afford that right now", 'just lost my job mate',
               'honestly i want to die', 'i am so lonely', "i'm so drunk lol",
               'my rent is due and i have nothing', 'relapsed last night',
               'i have a gambling problem']:
    check(f'flags: {phrase[:28]}', F.detect_distress(phrase))
for phrase in ['what can i afford on the bundle', 'i love your job',
               'that was a killer set', 'i died laughing', 'work is busy',
               "i'll buy it tomorrow", 'my job is going well']:
    check(f'ignores: {phrase[:28]}', not F.detect_distress(phrase))
check('hostile detected', F.detect_hostile('this is a scam, refund me'))
check('hostile: bot accusation', F.detect_hostile("you're a bot"))
check('not hostile', not F.detect_hostile('you are unreal'))
check('review: whale', F.review_reason(crossed_to_whale=True) == 'crossed to whale')
check('review: hostile skeptic',
      F.review_reason('SK', hostile_skeptic=True) == 'skeptic turned hostile')
check('review: nothing', F.review_reason('GF') == '')

print('loyalty')
due = F.due_rewards(months_subscribed=3, lifetime_spend=26000, replies_this_week=11)
check('tenure milestones', ('tenure', '1') in due and ('tenure', '3') in due, due)
check('spend milestones', ('spend', '10000') in due and ('spend', '25000') in due, due)
check('engagement', ('engagement', 'replies10') in due, due)
check('not yet reached', ('tenure', '6') not in due and ('spend', '50000') not in due)
check('already delivered is skipped',
      ('tenure', '1') not in F.due_rewards(months_subscribed=3, already={'tenure:1'}))
check('rewards are not same-hour', F.REWARD_DELAY_DAYS[0] >= 1)

print('funnel table')
check('ten funnels', len(F.FUNNELS) == 10)
check('every funnel has a steer and a ladder',
      all(f.get('steer') and f.get('ladder') for f in F.FUNNELS.values()))
check('every funnel has an exit', all('exit' in f for f in F.FUNNELS.values()))
check('every arm has priors for every type',
      all(set(F.PRIORS[a]) == set(F.FAN_TYPES) for a in F.ARMS))
check('exit targets exist',
      all((f.get('exit_to') or '') in F.FUNNELS or not f.get('exit_to')
          for f in F.FUNNELS.values()))
check('variant key is stable',
      F.variant_key({'opener': 'a', 'delay': 'next_day'}) == 'delay=next_day|opener=a')
check('empty variant key', F.variant_key({}) == '')
check('one axis at a time', len(F.pick_variant('opener', random.Random(1))) == 1)

print()
def test_nothing_failed():
    assert not FAILURES, FAILURES


if __name__ == '__main__':
    if FAILURES:
        print(f'{len(FAILURES)} FAILED: {FAILURES}')
        raise SystemExit(1)
    print('all funnel tests passed')
