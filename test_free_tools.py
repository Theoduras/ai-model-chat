"""Free-tool tests. Run with `python test_free_tools.py`.

The generators are a public endpoint that spends Gemini money and writes flirty
copy, so the parts that matter are the input whitelist, the youth-term refusal
and the rate limiter.
"""
import os
import sys

import free_tools as FT

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ok   {name}')
    else:
        print(f'  FAIL {name} {detail}')
        FAILURES.append(name)


def refused(data):
    try:
        FT.build_prompt(data)
    except FT.InputError as e:
        return str(e)
    return None


def test_validation():
    print('validation')
    good = {'tool': 'ppv-caption', 'about': 'Red lingerie set, 12 photos',
            'content_type': FT.CONTENT_TYPES[0], 'platform': 'Fanvue', 'vibe': FT.VIBES[0]}
    system, prompt, key = FT.build_prompt(good)
    check('valid caption request builds', key == 'captions' and 'Red lingerie' in prompt)
    check('system keeps it adult and non-explicit', '18+' in system and 'never explicit' in system)
    check('unknown tool refused', refused({'tool': 'essay'}))
    check('platform outside the list refused', refused({**good, 'platform': 'MySpace'}))
    check('vibe outside the list refused', refused({**good, 'vibe': 'Evil'}))
    check('caption needs a description', refused({**good, 'about': '   '}))
    for word in ('teen', 'Schoolgirl outfit', 'barely legal', 'she is 17 years old', 'underage'):
        check(f'youth term refused: {word}', refused({**good, 'about': word}))
    check('ordinary words are not caught', not refused({**good, 'about': 'kidding around at the gym'}))
    long = FT.build_prompt({**good, 'about': 'x' * 1000})[1]
    check('free text is capped', 'x' * FT.TEXT_MAX in long and 'x' * (FT.TEXT_MAX + 1) not in long)
    opener = FT.build_prompt({'tool': 'dm-opener', 'platform': 'OnlyFans', 'vibe': FT.VIBES[1]})
    check('opener works without a description', opener[2] == 'openers')


def test_parse():
    print('parse_result')
    r = FT.parse_result('{"captions": ["a one", "b two", "c three"], '
                        '"price": {"low": 8, "high": 15, "why": "short set"}}', 'captions')
    check('json captions', r['captions'] == ['a one', 'b two', 'c three'])
    check('json price', r.get('price') == {'low': 8, 'high': 15, 'why': 'short set'})
    r = FT.parse_result('```json\n{"openers": ["hey you", "hi there"]}\n```', 'openers')
    check('fenced json', r['openers'] == ['hey you', 'hi there'])
    r = FT.parse_result('1. first line here\n2. second line here\n- third line here', 'openers')
    check('plain lines fallback', r['openers'] == ['first line here', 'second line here', 'third line here'])
    r = FT.parse_result('{"captions": ["x1 caption"], "price": {"low": 20, "high": 5}}', 'captions')
    check('inverted price dropped', 'price' not in r)


def test_limiter():
    print('limiter')
    lim = FT.Limiter(per_key=3, window=60, total=5, total_window=3600)
    check('under the per-key limit', all(lim.allow('a', now=t) for t in (0, 1, 2)))
    check('fourth call in the window refused', not lim.allow('a', now=3))
    check('window slides', lim.allow('a', now=61))
    check('another key has its own allowance', lim.allow('b', now=62))
    check('instance total caps everyone', not lim.allow('c', now=63))
    check('total resets after its window', lim.allow('c', now=3700))


def test_tools():
    print('tools list')
    slugs = [t['slug'] for t in FT.TOOLS]
    check('slugs unique', len(slugs) == len(set(slugs)))
    tracks = [t['track'] for t in FT.TOOLS if t['track']]
    check('four tracked tools', len(tracks) == 4 and len(set(tracks)) == 4)
    check('track codes fit a register link', all(len(c) <= 32 and c.replace('-', '').isalnum()
                                                  for c in tracks))
    here = os.path.dirname(os.path.abspath(__file__))
    for s in slugs:
        check(f'template for {s}', os.path.exists(os.path.join(here, 'templates', 'tools', s + '.html')))


if __name__ == '__main__':
    test_validation()
    test_parse()
    test_limiter()
    test_tools()
    if FAILURES:
        print(f'\n{len(FAILURES)} failed')
        sys.exit(1)
    print('\nall free tool tests passed')
