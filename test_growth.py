"""Tests for the growth layer: beta gate, CTA choice, attribution, the content
register and the win-back ladder.

Pure logic — no API key, no database, no network.
Run with: python test_growth.py
"""
import json

import growth as G

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ok   {name}')
    else:
        print(f'  FAIL {name} {detail}')
        FAILURES.append(name)


print('beta gate')
check('list form', G.in_beta(['Lilith', 'nova'], 'lilith'))
check('comma string form', G.in_beta('lilith, nova', 'nova'))
check('off by default', not G.in_beta('', 'lilith'))
check('wildcard', G.in_beta('*', 'anyone'))
check('json list, as the setting stores it', G.in_beta('["lilith", "nova"]', 'nova'))
check('json round trip', G.beta_slugs(json.dumps(sorted(G.beta_slugs('Lilith, Nova'))))
      == {'lilith', 'nova'})
check('unknown slug', not G.in_beta('lilith', 'nova'))
check('no slug', not G.in_beta('*', ''))

print()
print('cta choice')
CTA = {'cta_url': 'https://fanvue.com/lil', 'cta_label': 'come find me',
       'trial_url': 'https://fanvue.com/lil?trial=1', 'trial_label': 'free for 3 days',
       'promo_code': 'LIL20', 'promo_expires': '2026-12-01'}
fresh = G.cta_choice(CTA, {})
check('first touch is the paid link', fresh['kind'] == 'paid'
      and fresh['url'] == CTA['cta_url'] and not fresh['promo'], fresh)
clicked = G.cta_choice(CTA, {'cta_sent': 1, 'cta_clicked': 2})
check('a fan who clicked stays on paid', clicked['kind'] == 'paid', clicked)
hes = G.cta_choice(CTA, {'cta_sent': 1}, today='2026-09-09')
check('hesitating gets the trial link', hes['kind'] == 'trial'
      and hes['url'] == CTA['trial_url'], hes)
check('trial carries its own label', hes['label'] == 'free for 3 days', hes)
check('trial carries the promo', hes['promo'] == 'LIL20', hes)
check('expired promo drops off',
      G.cta_choice(CTA, {'cta_sent': 1}, today='2027-01-01')['promo'] == '')
check('off the gate it is the old behaviour',
      G.cta_choice(CTA, {'cta_sent': 1}, beta=False)['kind'] == 'paid')
no_trial = dict(CTA, trial_url='')
check('promo alone still fires on the paid link',
      G.cta_choice(no_trial, {'cta_sent': 1}, today='2026-09-09')['promo'] == 'LIL20')
bare = {'cta_url': 'https://x.example', 'cta_label': 'here'}
check('nothing configured is unchanged',
      G.cta_choice(bare, {'cta_sent': 1}) == {'url': 'https://x.example',
                                              'label': 'here', 'kind': 'paid', 'promo': ''})
check('suffix folds the code in',
      G.cta_suffix(hes) == 'free for 3 days (code LIL20)', G.cta_suffix(hes))
check('suffix without a code', G.cta_suffix(fresh) == 'come find me')

print()
print('cta validation')
c = G.clean_cta({'cta_url': ' https://a ', 'promo_expires': 'soon', 'junk': 'x'})
check('trims', c['cta_url'] == 'https://a')
check('drops a non-date expiry', c['promo_expires'] == '')
check('drops unknown keys', 'junk' not in c)
check('keeps a real date',
      G.clean_cta({'promo_expires': '2026-12-01'})['promo_expires'] == '2026-12-01')
check('missing key keeps the stored value',
      G.clean_cta({'cta_label': 'new'}, {'cta_url': 'https://old'})['cta_url'] == 'https://old')
check('empty string clears it',
      G.clean_cta({'cta_url': ''}, {'cta_url': 'https://old'})['cta_url'] == '')
check('promo live with no clock', G.promo_live(CTA) == 'LIL20')
check('promo live with no expiry', G.promo_live({'promo_code': 'X'}, '2030-01-01') == 'X')

print()
print('attribution')
check('utm_source wins', G.source_from({'utm_source': 'Instagram'})['source'] == 'instagram')
check('ref is accepted', G.source_from({'ref': 'tiktok'})['source'] == 'tiktok')
check('medium and campaign ride along',
      G.source_from({'src': 'x', 'utm_medium': 'bio', 'utm_campaign': 'Sept Drop'})
      == {'source': 'x', 'medium': 'bio', 'campaign': 'sept-drop'})
check('referrer fallback',
      G.source_from({}, 'https://www.instagram.com/p/abc')['source'] == 'instagram')
check('t.co maps to x', G.source_from_referrer('https://t.co/abc') == 'x')
check('unknown referrer keeps the host',
      G.source_from_referrer('https://weird.example/x') == 'weird.example')
check('nothing at all', G.source_from({}, '') == {})
check('junk is scrubbed', G.normalise_source('  Insta gram!! ') == 'insta-gram',
      G.normalise_source('  Insta gram!! '))
check('empty stays empty', G.normalise_source(None) == '')

packed = G.pack_source({'source': 'instagram', 'medium': 'bio', 'campaign': 'sept'})
check('packs', packed == 'instagram_bio_sept', packed)
check('round trips', G.unpack_source(packed)
      == {'source': 'instagram', 'medium': 'bio', 'campaign': 'sept'})
check('packs a bare source', G.pack_source({'source': 'x'}) == 'x')
check('holds the gap open',
      G.pack_source({'source': 'x', 'campaign': 'sept'}) == 'x_-_sept')
check('unpacks around the gap',
      G.unpack_source('x_-_sept') == {'source': 'x', 'campaign': 'sept'})
check('packs nothing', G.pack_source({'medium': 'bio'}) == '')
check('unpacks nothing', G.unpack_source('') == {})
check('start payload is telegram-safe',
      set(G.join_start_payload('ab12', packed)) <= set(
          'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-'))
check('splits a payload', G.split_start_payload('ab12--instagram_bio')
      == ('ab12', 'instagram_bio'))
check('bare code still splits', G.split_start_payload('ab12') == ('ab12', ''))
check('joins without a source', G.join_start_payload('ab12', '') == 'ab12')

print()
print('content register')
reg = []
reg = G.register_add(reg, 'x', 'monday gym selfie, feeling strong', ts=1)
reg = G.register_add(reg, 'threads', 'who else cannot sleep tonight', ts=2)
check('newest first', reg[0]['text'].startswith('who else'))
check('platform recorded', reg[0]['platform'] == 'threads')
check('empty text is ignored', G.register_add(reg, 'x', '   ') == reg)
check('filters by platform', len(G.register_recent(reg, 'x')) == 1)
check('all platforms', len(G.register_recent(reg, '')) == 2)
check('spots a near-repeat',
      G.is_repeat(reg, 'x', 'gym selfie on monday, feeling really strong'))
check('a different post is fine',
      not G.is_repeat(reg, 'x', 'made pasta from scratch and burned my hand'))
check('same words, other platform',
      not G.is_repeat(reg, 'reddit', 'monday gym selfie, feeling strong'))
check('too short to judge', not G.is_repeat(reg, 'x', 'hi'))
block = G.register_block(reg, 'threads')
check('block names the post', 'cannot sleep' in block)
check('block leaves out other platforms', 'gym selfie' not in block)
check('no block when nothing posted', G.register_block([], 'x') == '')
big = []
for i in range(G.REGISTER_CAP + 10):
    big = G.register_add(big, 'x', f'post number {i}', ts=i)
check('capped', len(big) == G.REGISTER_CAP)
check('kept the newest', big[0]['text'].endswith(str(G.REGISTER_CAP + 9)))

print()
print('win-back ladder')
check('nothing on day zero', G.winback_step(0, 0) is None)
check('first touch on day one', G.winback_step(1, 0) == {'touch': 1, 'offer': False})
check('not twice on day one', G.winback_step(1, 1) is None)
check('second touch on day four', G.winback_step(4, 1) == {'touch': 2, 'offer': False})
check('third touch carries the offer', G.winback_step(12, 2) == {'touch': 3, 'offer': True})
check('fourth', G.winback_step(30, 3) == {'touch': 4, 'offer': True})
check('then quarterly', G.winback_step(120, 4) == {'touch': 5, 'offer': True})
check('and it stops', G.winback_step(400, 5) is None)
check('next rung after none', G.winback_next_day(0) == 1)
check('next rung after two', G.winback_next_day(2) == 12)
check('next rung goes quarterly', G.winback_next_day(4) == 120)
check('no rung once spent', G.winback_next_day(5) is None)
rungs = G.winback_rungs()
check('one row per rung', len(rungs) == 5, rungs)
check('rungs agree with the ladder',
      all(G.winback_step(r['day'], r['touch'] - 1) == {'touch': r['touch'], 'offer': r['offer']}
          for r in rungs), rungs)
check('a day early fires nothing',
      all(G.winback_step(r['day'] - 1, r['touch'] - 1) is None for r in rungs))
check('the early touch asks for no offer',
      'without any offer or link' in G.winback_instruction(False))
check('offer wording carries the label',
      'come find me' in G.winback_instruction(True, 'come find me'))

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILED: {FAILURES}')
    raise SystemExit(1)
print('all growth tests passed')
