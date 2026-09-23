"""Regression tests for the two things that must never reach a fan: being
addressed by the persona's own name, and a raw [placeholder].

Run with: python test_fanvue.py   (no API key needed — nothing here calls out)
"""
import json
import os
import statistics
import threading
import time

os.environ.setdefault('GEMINI_API_KEY', 'test')

import app

FAILURES = []

# Tests stub app functions in place and do not all put them back. Snapshotting
# once and restoring before each test keeps a later test from quietly running
# against an earlier one's stubs — which is how a real 403 came back as "no
# error" from a test that never reached the code it was checking.
_PRISTINE = {k: v for k, v in vars(app).items() if callable(v)}


def restore_app():
    for k, v in _PRISTINE.items():
        setattr(app, k, v)


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  ' + str(detail)) if not ok else ''))
    if not ok:
        FAILURES.append(name)


def test_direction():
    """Who said what. Guessing here is what made her greet a fan by her own name."""
    fan, me = 'fan-1', 'me-1'
    d = app._fv_direction_of
    check('flat senderUuid, fan', d({'senderUuid': fan}, fan, me) == 'in')
    check('flat senderUuid, us', d({'senderUuid': me}, fan, me) == 'out')
    check('nested sender.uuid, us', d({'sender': {'uuid': me}}, fan, me) == 'out')
    check('a third uuid is unresolved', d({'senderUuid': 'other'}, fan, me) == '')
    check('no sender, fromMe flag', d({'fromMe': True, 'text': 'x'}, fan, me) == 'out')
    check('no sender, matches what we just sent',
          d({'text': 'hey you'}, fan, me, {'hey you'}) == 'out')
    check('no sender and no flag is unresolved, never "the fan"',
          d({'text': 'hello'}, fan, me) == '')
    check('her own intro is never filed as his',
          d({'body': "hey babe I'm Lilly, 22, from Maastricht"}, fan, me) != 'in')


def test_import():
    """One unrecognised message used to make the whole history read as the fan's."""
    fan, me = 'fan-1', 'me-1'
    traced, logged = [], []
    app._fv_trace = lambda p, s, dt='', fan='': traced.append((s, dt))
    app._log_x_message = lambda p, k, h, dr, t: logged.append((dr, t))
    app._fanvue_scope = lambda p: '/creators/me'

    def run(msgs, me_uuid=me):
        logged.clear()
        traced.clear()
        app._fanvue_call = lambda p, m, path, body=None: {'data': msgs}
        return app._fanvue_import_history('lilly', fan, 'markyboy', me_uuid)

    n, said = run([
        {'senderUuid': me, 'text': "hey babe I'm Lilly, 22 x", 'createdAt': '1'},
        {'senderUuid': fan, 'text': 'hi! im Mark, 34, from Rotterdam', 'createdAt': '2'},
        {'senderUuid': fan, 'text': 'welder, long shifts', 'createdAt': '3'},
    ])
    check('imports the whole chat', n == 3, n)
    check('her intro is stored as ours', logged[0][0] == 'out', logged)
    check('only his words seed his profile', len(said) == 2, said)

    n, _ = run([
        {'senderUuid': me, 'text': "I'm Lilly, 22", 'createdAt': '1'},
        {'weirdShape': 1, 'text': 'who dis', 'createdAt': '2'},
    ])
    check('one unresolvable message aborts the import', n == 0 and logged == [], logged)
    check('and says why in the activity log',
          any(s == 'error' and 'could not tell who sent' in t for s, t in traced), traced)

    n, _ = run([{'senderUuid': fan, 'text': 'hi'}], me_uuid=None)
    check('refuses to import without our own uuid', n == 0 and logged == [], logged)


def test_identity_never_crosses():
    """A profile saying the fan is called Lilly persists until something
    rewrites it, so it must not be storable at all."""
    mine = {'name': 'Lilly', 'age': '22', 'location': 'Maastricht, Netherlands'}
    cleaned = app._fan_mem_clean(dict(mine, job='welder'), mine)
    check('her name is not storable as his', 'name' not in cleaned, cleaned)
    check('nor her age', 'age' not in cleaned, cleaned)
    check('nor her location', 'location' not in cleaned, cleaned)
    check('his own details survive', cleaned.get('job') == 'welder', cleaned)
    check('case does not get round it', 'name' not in app._fan_mem_clean({'name': 'lILLY'}, mine))
    check('a fan with a different name is kept',
          app._fan_mem_clean({'name': 'Mark'}, mine).get('name') == 'Mark')

    app.load_persona_config = lambda slug: dict(mine)
    store = {}
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    store[app._fan_mem_key('lilly', 'fv:x')] = json.dumps({'name': 'Lilly', 'job': 'welder'})
    healed = app._fan_memory('lilly', 'fv:x')
    check('a profile poisoned before the guard heals on read', 'name' not in healed, healed)

    blk = app._fan_memory_block(healed, 'lilly')
    check('the prompt says who she is', 'YOU are Lilly' in blk)
    check('the prompt forbids using her name for him', 'Never address him as Lilly' in blk)
    check('and admits when his name is unknown', 'do not use one' in blk)
    check('the prompt names him when known',
          'his name is Mark' in app._fan_memory_block({'name': 'Mark'}, 'lilly'))


def test_placeholders():
    strip, has = app._strip_placeholders, app._has_placeholder
    for bad in ("hey [fan_name], how was work?", "hey [fan's name]!", "you're [age]?",
                "how's [his job] going?", "[insert compliment] babe",
                "so [location] then?", "miss you [name]"):
        check('stripped: %s' % bad, not has(strip(bad)), strip(bad))
    check('ordinary text is untouched',
          strip('hey, how was work today?') == 'hey, how was work today?')
    check('an emoji reply is untouched', strip('\U0001F60F') == '\U0001F60F')
    check('clean text is not flagged', not has('hey you, how was work?'))

    sent, traced = [], []
    app._fv_trace = lambda p, s, dt='', fan='': traced.append((s, dt))
    app._fanvue_call = lambda p, m, path, body=None: sent.append(body) or {}
    app._fv_send_text('lilly', '/s', 'fan', 'hey you, how was the shift?')
    check('a clean reply goes out', len(sent) == 1, sent)
    sent.clear()
    try:
        app._fv_send_text('lilly', '/s', 'fan', 'hey [fan_name]!')
        refused = False
    except RuntimeError:
        refused = True
    check('a placeholder never reaches the wire', refused and sent == [], sent)
    check('the refusal is logged',
          any(s == 'error' and 'placeholder' in t for s, t in traced), traced)


def test_pacing():
    """A fan sat in the conversation should not wait two minutes for "haha yeah"."""
    reply = 'haha yeah that sounds rough, what time do you finish tonight?'
    incoming = 'just got off a 12 hour shift, absolutely wrecked'
    cfg = {'humanize': True, 'typing_speed': 14, 'react_rate': 0}

    def total(active):
        acc = [0.0]
        real = app.time.sleep
        app.time.sleep = lambda s: acc.__setitem__(0, acc[0] + s)
        app._fv_send_text = lambda *a, **k: None
        try:
            app._fv_send_human('p', '/s', 'fan', reply, incoming=incoming,
                               cfg=cfg, active=active)
        finally:
            app.time.sleep = real
        return acc[0]

    hot = sorted(total(True) for _ in range(200))
    cold = sorted(total(False) for _ in range(200))
    check('an engaged fan waits about 40s, not 105s',
          30 <= statistics.median(hot) <= 50, statistics.median(hot))
    check('and never longer than FV_REPLY_CAP', hot[-1] <= app.FV_REPLY_CAP, hot[-1])
    check('a cold chat stays unhurried',
          statistics.median(cold) > statistics.median(hot) + 25,
          (statistics.median(hot), statistics.median(cold)))


def test_backlog():
    """Each reply holds its worker for the whole pause, so a saturated pool makes
    replies run later and later. The log has to say so."""
    traced, store = [], {}
    app._fv_trace = lambda p, s, dt='', fan='': traced.append((s, dt))
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    os.environ['FANVUE_REPLY_WORKERS'] = '4'
    app._fv_reply_pool[0] = None
    app._fv_inflight[0] = 0

    gate = threading.Event()
    original = app._fv_deliver
    app._fv_deliver = lambda *a, **k: gate.wait(5)
    try:
        depths = [app._fv_submit('lilly', 'lilly') for _ in range(12)]
        time.sleep(0.2)
        check('queue depth is tracked', max(depths) == 12, depths)
        late = [t for s, t in traced if s == 'delayed']
        check('the backlog is logged once, not once per reply', len(late) == 1, len(late))
        check('the line says how deep and what to do',
              late and 'queued' in late[0] and 'FANVUE_REPLY_WORKERS' in late[0], late)
        gate.set()
        app._fv_pool().shutdown(wait=True)
        check('every slot is released', app._fv_inflight[0] == 0, app._fv_inflight[0])
        app._fv_reply_pool[0] = None
        app._fv_submit('lilly', 'lilly')
        check('recovery is logged too',
              any('caught up' in t for s, t in traced if s == 'delayed'), traced[-2:])
        app._fv_pool().shutdown(wait=True)
    finally:
        app._fv_deliver = original
        os.environ.pop('FANVUE_REPLY_WORKERS', None)


def test_scopes():
    """One optional scope Fanvue will not grant used to kill the whole connect
    flow with a bare invalid_scope."""
    store = {}
    orig_get, orig_set = app._get_setting, app._set_setting
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    os.environ.pop('FANVUE_SCOPES', None)
    try:
        full = app._fanvue_scopes()
        check('the full set is asked for first', 'read:agency' in full.split(), full)

        bad = app._fanvue_parse_bad_scopes(
            "The OAuth 2.0 Client is not allowed to request scope 'read:agency'.", full)
        check('names the refused scope', bad == ['read:agency'], bad)
        check('prose is never mistaken for a scope',
              app._fanvue_parse_bad_scopes('The requested scope is invalid.', full) == [], 'x')
        check('an unrequested scope is ignored',
              app._fanvue_parse_bad_scopes("scope 'read:payouts'", full) == [], 'x')

        app._fanvue_remember_denied(bad)
        after = app._fanvue_scopes().split()
        check('the refused scope is dropped', 'read:agency' not in after, after)
        check('and stays dropped on the next connect',
              'read:agency' not in app._fanvue_scopes().split(), store)
        check('everything else is still asked for', 'read:media' in after, after)

        app._fanvue_remember_denied(['read:chat', 'write:chat', 'openid', 'read:self'])
        floor = app._fanvue_scopes().split()
        for s in app.FANVUE_REQUIRED_SCOPES.split():
            check('%s can never be dropped' % s, s in floor, floor)

        tiers = app._fanvue_scope_tiers()
        sets = [set(t.split()) for t in tiers]
        check('each tier is strictly narrower',
              all(sets[i + 1] < sets[i] for i in range(len(sets) - 1)), tiers)
        check('the last tier is the bare minimum',
              sets[-1] == set(app.FANVUE_REQUIRED_SCOPES.split()), tiers)

        store['fanvue_denied_scopes'] = '[]'
        check('a reset asks for everything again',
              set(app._fanvue_scopes().split()) == set(full.split()), app._fanvue_scopes())
    finally:
        app._get_setting, app._set_setting = orig_get, orig_set


def test_api_errors():
    """A 403 used to reach the screen as "Fanvue API 403:" and nothing else —
    the body had already been consumed to write the log line."""
    import io
    import urllib.error as url_error

    def raising(code, body, headers=None):
        def _open(req, timeout=None):
            raise url_error.HTTPError(req.full_url, code, 'Forbidden', headers or {},
                                      io.BytesIO(body.encode()))
        return _open

    orig_open = app.urllib.request.urlopen
    orig_tokens = app._fanvue_tokens
    try:
        app.urllib.request.urlopen = raising(403, '{"message":"Insufficient permissions"}')
        app._fanvue_tokens = lambda p: {'access_token': 'x', 'scope': app.FANVUE_CORE_SCOPES}
        try:
            app._fanvue_call('lilly', 'GET', '/media?page=1')
            check('a 403 raises', False, 'no error')
        except url_error.HTTPError as e:
            text = str(e)
            check("Fanvue's own words survive", 'Insufficient permissions' in text, text)
            check('the error body can still be read', b'Insufficient' in e.read(), e.read())
            check('the missing scope is named', 'read:media' in text, text)
            check('and what was granted is shown', 'read:chat' in text, text)

        app.urllib.request.urlopen = raising(403, '')
        try:
            app._fanvue_call('lilly', 'GET', '/chats?limit=30')
            check('an empty 403 raises', False, 'no error')
        except url_error.HTTPError as e:
            text = str(e)
            check('a bodyless 403 still says something', len(text) > 30, text)
            check('and does not blame a granted scope', 'read:chat"' not in text, text)

        check('creator-scoped paths map to the real endpoint',
              app._fv_scope_for_path('/creators/abc-123/chats/x/messages') == 'read:chat',
              app._fv_scope_for_path('/creators/abc-123/chats/x/messages'))
        check('media maps to read:media', app._fv_scope_for_path('/media?size=50') == 'read:media')
    finally:
        app.urllib.request.urlopen = orig_open
        app._fanvue_tokens = orig_tokens


def test_planner_posts():
    """The planner reads and writes the Fanvue feed on v1, where the list is cut
    by an opaque cursor rather than a page number."""
    calls = []

    pages = [
        {'data': [{'uuid': 'p1', 'text': 'one', 'publishedAt': '2026-09-14T09:00:00Z'},
                  {'uuid': 'p2', 'text': 'two', 'publishAt': '2026-09-15T11:00:00Z'}],
         'nextCursor': 'CUR2'},
        {'data': [{'uuid': 'p2'}, {'uuid': 'p3', 'text': 'three'}], 'nextCursor': None},
    ]

    def fake_call(persona, method, path, body=None):
        calls.append((method, path, body))
        if method == 'GET':
            return pages[min(len([c for c in calls if c[0] == 'GET']) - 1,
                             len(pages) - 1)]
        return {'uuid': 'new-post'}

    orig_call, orig_scope = app._fanvue_call, app._fanvue_scope
    try:
        app._fanvue_call = fake_call
        app._fanvue_scope = lambda p: ''

        rows = app._fv_posts('lilly', 1757800000, 1758400000)
        check('every page is collected once', [r['uuid'] for r in rows] == ['p1', 'p2', 'p3'],
              [r['uuid'] for r in rows])
        check('the window is asked for on the first page',
              'startDate=2025-09-13T21' in calls[0][1]
              and 'endDate=' in calls[0][1]
              and 'includeUnpublished=true' in calls[0][1],
              calls[0][1])
        check('and later pages carry the cursor alone',
              'cursor=CUR2' in calls[1][1] and 'startDate' not in calls[1][1], calls[1][1])
        check('posts are read on v1', calls[0][1].startswith('/v1/posts?'), calls[0][1])

        calls.clear()
        uid = app._fv_create_post('lilly', 'hi', media_uuids=['m1'],
                                  price_cents=500, audience='subscribers')
        body = calls[0][2]
        check('the new post comes back by uuid', uid == 'new-post', uid)
        check('the audience is sent', body['audience'] == 'subscribers', body)
        check('the price rides with its media',
              body['price'] == 500 and body['mediaUuids'] == ['m1'], body)
        check('and no publishAt, because the queue holds the slot',
              'publishAt' not in body, body)

        for why, kwargs in (('a price with no media', {'price_cents': 500}),
                            ('a price under the floor',
                             {'media_uuids': ['m1'], 'price_cents': 100})):
            try:
                app._fv_create_post('lilly', 'hi', **kwargs)
                check(why + ' is refused', False, 'no error')
            except RuntimeError as e:
                check(why + ' is refused', True, str(e))

        body = None
        calls.clear()
        app._fv_create_post('lilly', 'hi', audience='nonsense')
        check('an audience Fanvue does not know falls back to the open one',
              calls[0][2]['audience'] == 'followers-and-subscribers', calls[0][2])
    finally:
        app._fanvue_call, app._fanvue_scope = orig_call, orig_scope

    check('a vault id is recognised', app._fv_media_id('fv:abc') == 'abc')
    check('and a library id is not', app._fv_media_id('m1') == '')
    check('a vault item posts by uuid, with nothing uploaded',
          app._growth_media_check('lilly', 'fanvue', 'fv:abc') == ('fv:abc', ''),
          app._growth_media_check('lilly', 'fanvue', 'fv:abc'))
    check('and X is told why it cannot have it',
          'Fanvue vault' in app._growth_media_check('lilly', 'x', 'fv:abc')[1],
          app._growth_media_check('lilly', 'x', 'fv:abc'))

    sfw = app._content_level_note('lilith', 'x', 'sfw')
    check('asking for SFW says so plainly', 'safe for work' in sfw, sfw)
    check('and a locked channel cannot be talked out of it',
          'safe for work' in app._content_level_note('lilith', 'tiktok', 'nsfw'),
          app._content_level_note('lilith', 'tiktok', 'nsfw'))
    check('a draft with no picture says nothing about one',
          app._draft_media_note(False) == '')
    check('and one with a picture tells her to write about it',
          'what is actually in it' in app._draft_media_note(True))
    check('no media id means no picture to fetch',
          app._draft_media_bytes('lilith', '') == (None, ''))

    check('reading posts names read:post',
          app._fv_scope_for_path('/v1/posts?size=50') == 'read:post',
          app._fv_scope_for_path('/v1/posts?size=50'))
    check('writing one names write:post',
          app._fv_scope_for_path('/v1/posts', 'POST') == 'write:post',
          app._fv_scope_for_path('/v1/posts', 'POST'))
    check('and an agency login is still about posts',
          app._fv_scope_for_path('/v1/creators/abc-123/posts') == 'read:post',
          app._fv_scope_for_path('/v1/creators/abc-123/posts'))
    check('a Fanvue timestamp reads back as an epoch',
          app._fv_epoch('2026-09-14T09:00:00Z') == 1789376400,
          app._fv_epoch('2026-09-14T09:00:00Z'))
    check('and a missing one is zero, not a crash', app._fv_epoch(None) == 0)


def test_chat_lists():
    """The picker said "no lists on this account" for an account full of them:
    smart lists carry a string id, and only `uuid` was ever read."""
    row = app._fv_list_row({'id': 'unread', 'name': 'Unread', 'count': 12}, 'smart')
    check('a smart list keyed by id is kept', row['id'] == 'unread', row)
    check('and keeps its name and size', (row['name'], row['count']) == ('Unread', 12), row)
    check('a uuid list still works',
          app._fv_list_row({'uuid': 'u-1', 'name': 'VIP', 'membersCount': 3}, 'custom')['count'] == 3)
    check('a list with no id at all is dropped later',
          app._fv_list_row({'name': 'nameless'}, 'smart')['id'] == '')
    check('an unnamed list falls back to its id',
          app._fv_list_row({'id': 'tippers'}, 'smart')['name'] == 'tippers')
    check('a lists-keyed envelope is unwrapped',
          app._fv_list({'lists': [{'id': 'a'}]}) == [{'id': 'a'}], app._fv_list({'lists': []}))

    calls = []

    def fake(persona, method, path, body=None):
        calls.append(path)
        if path.startswith('/creators/'):
            raise RuntimeError('403 wrong shape for this login')
        if path.startswith('/chats/lists/smart'):
            return {'data': [{'id': 'unread', 'name': 'Unread'}]}
        return {'data': [{'uuid': 'u-1', 'name': 'VIP'}], 'pagination': {'hasMore': False}}

    app._fanvue_call = fake
    app._fanvue_scope = lambda p: '/creators/c-1'
    errs = []
    lists = app._fanvue_chat_lists('lilly', errs)
    ids = sorted(l['id'] for l in lists)
    check('both prefixes are tried', any(p.startswith('/creators/') for p in calls), calls)
    check('and the working one still returns the lists', ids == ['u-1', 'unread'], lists)
    check('a failing prefix is reported, not hidden', len(errs) >= 1, errs)
    check('no list is listed twice', len(lists) == len(set((l['kind'], l['id']) for l in lists)))

    def all_fail(persona, method, path, body=None):
        raise RuntimeError('Fanvue API 403: Insufficient permissions')

    app._fanvue_call = all_fail
    errs = []
    check('nothing is invented when every call fails',
          app._fanvue_chat_lists('lilly', errs) == [], 'x')
    check('and the refusal is carried back for the screen',
          any('403' in e for e in errs), errs)


def test_scope_reporting():
    """A missing optional scope was reported as the reason lists were empty. It
    is not: read:agency and read:insights touch no chat endpoint."""
    for s in app.FANVUE_OPTIONAL_SCOPES:
        check('%s is described by what it costs' % s,
              bool(app.FANVUE_SCOPE_FEATURES.get(s)), s)
    for s in ('read:chat', 'write:chat', 'read:self', 'openid'):
        check('%s is never merely optional' % s, s not in app.FANVUE_OPTIONAL_SCOPES)
    check('lists do not depend on read:agency',
          app._fv_scope_for_path('/chats/lists/smart') == 'read:chat')
    check('nor on read:insights',
          app._fv_scope_for_path('/creators/c-1/chats/lists/custom?page=1') == 'read:chat')
    check('the agency picker is what read:agency is for',
          app._fv_scope_for_path('/agency/creators?limit=50') == 'read:agency')
    check('and earnings is what read:insights is for',
          app._fv_scope_for_path('/creators/c-1/earnings?size=50') == 'read:insights')


def _ppv_ctx(**over):
    """The context _fv_maybe_ppv is called with, with one set of two tiers."""
    ctx = {'sets': [{'id': 's1', 'name': 'Bar shift',
                     'tiers': [{'media_uuids': ['m1'], 'price': 1000, 'caption': 'one'},
                               {'media_uuids': ['m2'], 'price': 5000, 'caption': 'two'}],
                     'scene': '', 'keywords': [], 'hour_from': None, 'hour_to': None}],
           'gap': 1, 'first_after': 1, 'retry_after': 0, 'retry_max': 0,
           'retry_discount': 0, 'stale_days': 14, 'state_key': 'k_state',
           'paid_key': 'k_paid', 'tz_offset': 0, 'require_payment': False,
           'funnels': None, 'assignment': None}
    ctx.update(over)
    return ctx


def _stub_drop_path(sent, traced, store):
    """Enough of the world for _fv_maybe_ppv to run without a database."""
    app._fanvue_call = lambda p, m, path, body=None: (
        sent.append((path, body)) or {'uuid': 'msg-1'})
    app._fv_trace = lambda p, st, dt='', fan='': traced.append((st, dt))
    app._fv_record_drop = lambda *a, **k: 'drop-1'
    app._fv_record_pitch = lambda *a, **k: None
    app._fanvue_msg_count = lambda p, k: 20
    app._fv_fan_hour = lambda *a, **k: 12
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    app._json_setting_strict = lambda k, d: json.loads(store.get(k) or json.dumps(d))


def test_funnels_off_changes_nothing():
    """The whole system is behind a flag. With it off, not one funnel function
    is reachable from the drop path."""
    sent, traced, store = [], [], {}
    _stub_drop_path(sent, traced, store)
    called = []
    app._fv_pitch_gate = lambda *a, **k: called.append(a) or (False, 'should not run', 0)
    app._fv_maybe_ppv('lilly', '', 'fan-1', 'fv:fan-1', 'joe', 'hey', _ppv_ctx())
    check('the drop still goes out', len(sent) == 1, sent)
    check('the guardrails were never consulted', not called)
    check('and it is the configured price', sent and sent[0][1].get('price') == 1000)


def test_guardrails_can_stop_a_drop():
    sent, traced, store = [], [], {}
    _stub_drop_path(sent, traced, store)
    cfg = dict(app.FV_FUNNEL_DEFAULTS, enabled=True)
    app._fv_pitch_gate = lambda *a, **k: (False, 'distress signal — paused', 0)
    app._fv_maybe_ppv('lilly', '', 'fan-1', 'fv:fan-1', 'joe', 'hey',
                      _ppv_ctx(funnels=cfg))
    check('nothing was sent', not sent, sent)
    check('and the reason is on the record',
          any(st == 'guardrail' and 'distress' in dt for st, dt in traced), traced)


def test_price_cap_holds_rather_than_discounts():
    """A churn-risk cap must never quietly sell the creator\'s content cheap."""
    sent, traced, store = [], [], {}
    _stub_drop_path(sent, traced, store)
    store['k_state'] = json.dumps({'fan-1': {'sets': {'s1': 1}, 'active_set': 's1',
                                             'retries': 0, 'msgs_at_last_drop': 0,
                                             'last_drop_id': ''}})
    cfg = dict(app.FV_FUNNEL_DEFAULTS, enabled=True)
    app._fv_pitch_gate = lambda *a, **k: (True, 'watch mode', 1000)
    app._fv_maybe_ppv('lilly', '', 'fan-1', 'fv:fan-1', 'joe', 'hey',
                      _ppv_ctx(funnels=cfg))
    check('the dearer tier is held, not marked down', not sent, sent)
    check('and it says why',
          any('over the' in dt for st, dt in traced), traced)
    # Under the cap the same drop goes out untouched.
    sent2, traced2, store2 = [], [], {}
    _stub_drop_path(sent2, traced2, store2)
    app._fv_pitch_gate = lambda *a, **k: (True, '', 1000)
    app._fv_maybe_ppv('lilly', '', 'fan-1', 'fv:fan-1', 'joe', 'hey',
                      _ppv_ctx(funnels=cfg))
    check('a tier inside the cap is unaffected',
          len(sent2) == 1 and sent2[0][1]['price'] == 1000, sent2)


def test_guardrail_failure_does_not_cost_a_sale():
    """If the new code throws, the old behaviour has to survive it."""
    sent, traced, store = [], [], {}
    _stub_drop_path(sent, traced, store)
    def boom(*a, **k):
        raise RuntimeError('scores table is gone')
    app._fv_pitch_gate = boom
    app._fv_maybe_ppv('lilly', '', 'fan-1', 'fv:fan-1', 'joe', 'hey',
                      _ppv_ctx(funnels=dict(app.FV_FUNNEL_DEFAULTS, enabled=True)))
    check('the drop still went out', len(sent) == 1, sent)


def test_funnel_config_roundtrip():
    store = {}
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    check('off by default', not app._fv_funnels_on('lilly'))
    store['fanvue_funnels_lilly'] = json.dumps({'enabled': True, 'mode': 'thompson',
                                                'unlocked': ['F1', 'F9', 'nope'],
                                                'daily_cap_cents': 5000})
    cfg = app._fv_funnel_cfg('lilly')
    check('reads back on', cfg['enabled'] and app._fv_funnels_on('lilly'))
    check('mode kept', cfg['mode'] == 'thompson')
    check('unknown funnels dropped', cfg['unlocked'] == ['F1', 'F9'], cfg['unlocked'])
    check('cap kept', cfg['daily_cap_cents'] == 5000)
    check('defaults fill the rest', cfg['tier_cents']['T1'] > 0)
    store['fanvue_funnels_lilly'] = 'not json at all'
    check('junk settings fall back to the defaults',
          app._fv_funnel_cfg('lilly')['enabled'] is False)


def test_distress_pause_is_written_once():
    store, traced = {}, []
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    app._fv_trace = lambda p, s, d='', fan='': traced.append((s, d))
    app._fv_note_event = lambda *a, **k: None
    app._fv_flag_distress('lilly', 'fan-1', 'joe')
    first = store.get('fanvue_distress_lilly')
    check('the pause is stored', first and 'fan-1' in first)
    check('it is in the future', app._fv_distress_until('lilly', 'fan-1') > time.time())
    app._fv_flag_distress('lilly', 'fan-1', 'joe')
    check('a second signal does not re-trace it', len(traced) == 1, traced)
    store['fanvue_distress_lilly'] = json.dumps({'old': 1, 'fan-2': time.time() + 999})
    app._fv_flag_distress('lilly', 'fan-3', '')
    kept = json.loads(store['fanvue_distress_lilly'])
    check('expired entries are swept', 'old' not in kept and 'fan-2' in kept, kept)


def test_funnel_exits():
    """Every funnel has an exit. Without one a fan who ignores everything stays
    in the same approach forever."""
    import datetime
    ex = app._fv_check_exit
    led = lambda **k: dict({'ignored': 0, 'bought': 0, 'spent_today': 0,
                            'spend_30d': 0, 'sent': 0, 'opens_no_buy': 0,
                            'lifetime': 0}, **k)
    a = {'id': 'a1', 'funnel': 'F1', 'assigned_at': datetime.datetime.now(
        datetime.timezone.utc), 'pitches_sent': 1}
    check('stays while things are fine', ex('p', 'f', 'j', a, led(), 'hey') == '')
    check('two ignored ends it', 'ignored' in ex('p', 'f', 'j', a, led(ignored=2), ''))
    check('one ignored does not', ex('p', 'f', 'j', a, led(ignored=1), '') == '')
    check('hostility ends it',
          ex('p', 'f', 'j', a, led(), 'this is a scam') == 'fan turned hostile')
    old = dict(a, funnel='F2', assigned_at=datetime.datetime.now(
        datetime.timezone.utc) - datetime.timedelta(hours=60))
    check('the 48h window closes', 'window closed' in ex('p', 'f', 'j', old, led()))
    check('but not once they have bought',
          ex('p', 'f', 'j', old, led(bought=1)) == '')
    check('F9 gives up after three',
          ex('p', 'f', 'j', dict(a, funnel='F9'), led(ignored=3)) != '')
    check('no assignment, nothing to exit', ex('p', 'f', 'j', None, led()) == '')
    for fid, f in app.FN.FUNNELS.items():
        nxt = f.get('exit_to') or ''
        check('%s exits somewhere real' % fid, not nxt or nxt in app.FN.FUNNELS, nxt)


def test_webhook_subscription():
    """Fanvue delivers nothing to a URL that was never subscribed, so connecting
    has to do it — and it has to be idempotent, scope-aware and safe to fail."""
    calls, store, traced = [], {}, []
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    app._fv_trace = lambda p, st, d='', fan='': traced.append((st, d))
    app._callback_origin = lambda: 'https://app.example.com'
    app._fanvue_tokens = lambda p: {'access_token': 't',
                                    'scope': 'openid read:self read:chat read:creator'}
    existing = {'data': []}
    def api(persona, method, path, body=None):
        calls.append((method, path, body))
        if method == 'GET' and path == '/webhooks/subscriptions':
            return existing
        if method == 'POST' and path == '/webhooks/subscriptions':
            return {'id': 'wh-1', 'signingSecret': 'sek-1'}
        return {}
    app._fanvue_call = api

    r = app._fv_ensure_webhook('lilly')
    check('it subscribes on connect', r['ok'] and r['created'], r)
    posted = [c for c in calls if c[0] == 'POST'][0][2]
    check('to our own webhook path',
          posted['url'] == 'https://app.example.com/webhooks/fanvue', posted)
    check('for the churn events',
          'creator.subscription.deactivated' in posted['events']
          and 'creator.refund.created' in posted['events'], posted)
    check('the secret is stored', app._fv_stored_hook('lilly')['secret'] == 'sek-1')

    # Same subscription already present: leave it alone.
    calls.clear()
    existing['data'] = [{'id': 'wh-1', 'url': 'https://app.example.com/webhooks/fanvue',
                         'events': list(app.FV_WEBHOOK_EVENTS)}]
    r = app._fv_ensure_webhook('lilly')
    check('a complete subscription is left alone',
          r['ok'] and not r['created'] and not [c for c in calls if c[0] == 'POST'], r)
    check('and its secret is not lost', app._fv_stored_hook('lilly')['secret'] == 'sek-1')

    # Missing an event: replace it, because Fanvue cannot amend one.
    calls.clear()
    existing['data'] = [{'id': 'wh-1', 'url': 'https://app.example.com/webhooks/fanvue',
                         'events': ['creator.message.read']}]
    r = app._fv_ensure_webhook('lilly')
    check('an incomplete one is replaced',
          any(c[0] == 'DELETE' for c in calls) and any(c[0] == 'POST' for c in calls), calls)

    # Only the events the connection has scopes for — asking for more fails the
    # whole call and would take the ones we could have had with it.
    calls.clear(); existing['data'] = []
    app._fanvue_tokens = lambda p: {'access_token': 't', 'scope': 'openid read:self read:chat'}
    r = app._fv_ensure_webhook('lilly')
    ev = [c for c in calls if c[0] == 'POST'][0][2]['events']
    check('read:creator events are dropped when not granted',
          ev == ['creator.message.read'], ev)
    check('and the gap is reported', 'creator.refund.created' in (r.get('missing') or []), r)

    app._fanvue_tokens = lambda p: {'access_token': 't', 'scope': 'openid read:self'}
    check('nothing to subscribe means no call',
          not app._fv_ensure_webhook('lilly')['ok'])

    # A local or http deployment cannot receive deliveries at all.
    app._callback_origin = lambda: 'http://localhost:8080'
    check('no subscription without a public https URL',
          not app._fv_ensure_webhook('lilly')['ok'])
    app._callback_origin = lambda: 'https://127.0.0.1'
    check('nor for a loopback https URL', app._fv_webhook_url() == '')

    # Connecting must never fail because of this.
    app._callback_origin = lambda: 'https://app.example.com'
    def boom(*a, **k):
        raise RuntimeError('Fanvue is down')
    app._fanvue_call = boom
    r = app._fv_ensure_webhook_safe('lilly')
    check('a failure is reported, not raised', r['ok'] is False and r['reason'], r)


def test_webhook_accepts_any_known_secret():
    """Every subscription mints its own secret and the header names no key, so
    a delivery is genuine if any secret we hold verifies it."""
    import hashlib as _h, hmac as _hm
    store = {'fanvue_webhook_lilly': json.dumps({'id': 'wh-1', 'secret': 'per-hook'})}
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._fv_webhook_secret = lambda: 'from-env'
    app.db_list_personas = lambda: [{'slug': 'lilly'}]
    app._fanvue_enabled_list = lambda: []
    secrets = app._fv_webhook_secrets()
    check('both secrets are candidates',
          'from-env' in secrets and 'per-hook' in secrets, secrets)

    body = b'{"type":"creator.subscription.deactivated"}'
    ts = str(int(time.time()))
    def sign(sec):
        return _hm.new(sec.encode(), ts.encode() + b'.' + body, _h.sha256).hexdigest()
    for sec in ('from-env', 'per-hook'):
        ok, why = app._fv_verify_signature(body, 't=%s,v0=%s' % (ts, sign(sec)), sec)
        check('a delivery signed with %s verifies' % sec, ok, why)
    ok, _ = app._fv_verify_signature(body, 't=%s,v0=%s' % (ts, sign('wrong')), 'per-hook')
    check('an unknown secret does not', not ok)
    old_ts = str(int(time.time()) - 4000)
    sig = _hm.new(b'per-hook', old_ts.encode() + b'.' + body, _h.sha256).hexdigest()
    check('a replayed old delivery is still refused',
          not app._fv_verify_signature(body, 't=%s,v0=%s' % (old_ts, sig), 'per-hook')[0])


def test_webhook_diagnosis():
    """Fanvue can refuse a subscription with a 400 and an empty error body. The
    check has to narrow that to a cause instead of repeating the empty error."""
    import utils
    utils._is_operator = lambda: True
    store = {}
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    app._callback_origin = lambda: 'https://dev.example.run.app'
    app._fanvue_tokens = lambda p: {'access_token': 't',
                                    'scope': 'openid read:self read:chat'}

    def run(api):
        app._fanvue_call = api
        with app.app.test_request_context('/api/fanvue/webhook-check', method='POST',
                                          json={'persona': 'lilly'}):
            return json.loads(app.api_fanvue_webhook_check().get_data())

    def refuse_all(p, m, path, body=None):
        if m == 'GET':
            return {'data': []}
        raise RuntimeError('Fanvue API 400: {"error":""}')
    r = run(refuse_all)
    check('a blanket refusal points at the URL', 'points at the URL' in r['verdict'], r)
    check('and names the URL that was sent',
          r['url'] == 'https://dev.example.run.app/webhooks/fanvue', r['url'])

    def one_event_ok(p, m, path, body=None):
        if m == 'GET':
            return {'data': []}
        if len(body.get('events', [])) == 1:
            return {'id': 'wh-2', 'signingSecret': 's2'}
        raise RuntimeError('Fanvue API 400: {"error":""}')
    r = run(one_event_ok)
    check('one event succeeding points at the event list', 'event list' in r['verdict'], r)
    check('and the subscription that worked is kept',
          json.loads(store['fanvue_webhook_lilly'])['secret'] == 's2')

    store.clear()
    def already(p, m, path, body=None):
        if m == 'GET':
            return {'data': [{'id': 'wh-x', 'events': ['creator.message.read'],
                              'url': 'https://dev.example.run.app/webhooks/fanvue'}]}
        raise AssertionError('must not create a second subscription')
    r = run(already)
    check('an existing subscription is recognised', 'already subscribed' in r['verdict'], r)

    app._callback_origin = lambda: 'http://localhost:8080'
    check('a local deployment is told what is missing',
          'PUBLIC_BASE_URL' in run(refuse_all)['verdict'])


def test_unsendable_chats():
    """A fan can be listed in /chats and still be unwritable — deleted accounts
    and blocks answer the send with "Invalid user UUID" while reading the same
    chat works. Retrying every round burns a reply slot and an API call."""
    store, traced, sent = {}, [], []
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    app._fv_trace = lambda p, st, d='', fan='': traced.append((st, d))
    app._log_x_message = lambda *a: None
    app._fv_send_human = lambda *a, **k: sent.append(a)

    err400 = app.FanvueApiError.__new__(app.FanvueApiError)
    err400.code, err400.detail = 400, 'Invalid user UUID'
    err400.args = ('Fanvue API 400: Invalid user UUID',)
    check('an invalid recipient is recognised', app._fv_unreachable_error(err400))
    for code, msg in ((500, 'Internal error'), (400, 'price must be at least 300'),
                      (429, 'Too many requests')):
        e = app.FanvueApiError.__new__(app.FanvueApiError)
        e.code, e.detail, e.args = code, msg, ('Fanvue API %d: %s' % (code, msg),)
        check('%d %s is not treated as unreachable' % (code, msg[:18]),
              not app._fv_unreachable_error(e))

    uid = '11111111-2222-3333-4444-555555555555'
    def boom(*a, **k):
        raise err400
    app._fv_send_human = boom
    app._fv_deliver('lilly', '', uid, 'fv:' + uid, 'aikoxxx', 'hey', '', {}, None)
    row = app._fv_is_unsendable('lilly', uid)
    check('the chat is stood down', row is not None and row['tries'] == 1, row)
    check('for a day to start with',
          23 < (row['until'] - time.time()) / 3600 <= 24, row)
    check('and it is said once, with the uuid',
          any('cannot be messaged' in d and uid in d for st, d in traced), traced)
    check('a real uuid is not blamed on us',
          not any('not a uuid' in d for st, d in traced), traced)

    # Failing again after the retry stands it down for longer.
    store[app._fv_unsendable_key('lilly')] = json.dumps(
        {uid: {'until': time.time() - 1, 'tries': 1, 'handle': 'aikoxxx', 'why': 'x'}})
    check('an expired stand-down lets it try again',
          app._fv_is_unsendable('lilly', uid) is None)
    app._fv_deliver('lilly', '', uid, 'fv:' + uid, 'aikoxxx', 'hey', '', {}, None)
    row = app._fv_is_unsendable('lilly', uid)
    check('the second failure backs off further',
          row['tries'] == 2 and (row['until'] - time.time()) / 3600 > 40, row)

    # A send that works clears it.
    app._fv_send_human = lambda *a, **k: sent.append(a)
    app._fv_deliver('lilly', '', uid, 'fv:' + uid, 'aikoxxx', 'hey', '', {}, None)
    check('a successful send clears the stand-down',
          app._fv_is_unsendable('lilly', uid) is None)

    # An id that is not a uuid is our bug, and says so.
    traced.clear()
    app._fv_send_human = boom
    app._fv_deliver('lilly', '', 'chat-42', 'fv:chat-42', 'joe', 'hey', '', {}, None)
    check('a malformed id is called out as ours',
          any('not a uuid' in d for st, d in traced), traced)

    # The round must not see them at all: no reply slot, no read, one log line.
    store[app._fv_unsendable_key('lilly')] = json.dumps(
        {uid: {'until': time.time() + 3600, 'tries': 1, 'handle': 'aikoxxx', 'why': 'x'}})
    chats = [{'user': {'uuid': uid, 'handle': 'aikoxxx'}},
             {'user': {'uuid': '99999999-2222-3333-4444-555555555555', 'handle': 'jo'}}]
    read = []
    app._fanvue_paged = lambda p, path, **k: chats if path.endswith('/chats') else []
    app._fanvue_me_uuid = lambda p: 'me-1'
    app._fanvue_scope = lambda p: ''
    app._fanvue_auto_settings = lambda p, **k: {'reply_limit': 10}
    app._fanvue_chat_messages = lambda p, u, w: read.append(u) or []
    app._fanvue_ppv_sets = lambda p, **k: []
    app._fanvue_msg_count = lambda p, k: 5
    acts, rlog = app._fanvue_auto_round('lilly')
    check('the dead chat is never read', uid not in read, read)
    check('the live one still is', '99999999-2222-3333-4444-555555555555' in read, read)
    check('it is counted as dropped', acts['skipped_unsendable'] == 1, acts)
    check('and said once, not once per chat',
          len([l for l in rlog if 'dropped' in l]) == 1, rlog)
    check('the chat count reflects what is worked',
          any(l.startswith('1 chats found') for l in rlog), rlog)

    # Anything else is still reported as a plain failure, not stood down.
    store.pop(app._fv_unsendable_key('lilly'), None)   # clear the seeded row
    traced.clear()
    other = app.FanvueApiError.__new__(app.FanvueApiError)
    other.code, other.detail = 500, 'Internal error'
    other.args = ('Fanvue API 500: Internal error',)
    def boom500(*a, **k):
        raise other
    app._fv_send_human = boom500
    app._fv_deliver('lilly', '', uid, 'fv:' + uid, 'aikoxxx', 'hey', '', {}, None)
    check('a server error is not a dead chat', app._fv_is_unsendable('lilly', uid) is None)
    check('but it is still reported',
          any('failed' in d for st, d in traced), traced)


def test_complaints_are_remembered():
    """A fan saying she keeps going on about chocolate has to change what she
    says next — to him, and to every other fan of that persona."""
    store, traced = {}, []
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    app._del_setting = lambda k: store.pop(k, None) is not None
    app._fv_trace = lambda p, st, d='', fan='': traced.append((st, d))
    app.load_persona_config = lambda slug: {'name': 'Lilly', 'age': '22',
                                            'location': 'Maastricht'}
    replies = []

    class _Resp:
        def __init__(self, text):
            self.text = text

    class _Models:
        def generate_content(self, **kw):
            return _Resp(replies.pop(0))

    class _Client:
        models = _Models()

    was_client, app.client = app.client, _Client()
    fan = 'fv:im2ez4u'
    try:
        replies.append(json.dumps({'name': 'Mark', 'interests': ['chocolate']}))
        app._fan_memory_update('lilly', fan, 'my name is Mark, I love chocolate')
        check('what he said is remembered',
              app._fan_memory('lilly', fan).get('interests') == ['chocolate'])

        replies.append(json.dumps({'name': 'Mark', 'interests': [],
                                   'avoid': ['chocolate']}))
        mem = app._fan_memory_update(
            'lilly', fan, 'you keep going on about chocolate, stop it')
        check('the complaint is filed', mem.get('avoid') == ['chocolate'], mem)
        check('and the fact behind it is dropped', not mem.get('interests'), mem)
        check('the reply prompt now forbids it',
              'chocolate' in app._fan_memory_block(mem, 'lilly') and
              'PUSHED BACK' in app._fan_memory_block(mem, 'lilly'))
        check('the console is told', any(st == 'complaint' for st, d in traced), traced)

        # The persona itself learns it, so the next fan never hears it either.
        check('the persona learns it too',
              app._persona_avoid('lilly') == ['chocolate'], store)
        app._prompt_cache.pop('lilly', None)
        check('and every channel gets it, through the system prompt',
              'chocolate' in app.get_system_prompt('lilly'))

        # An extraction that forgets to echo `avoid` back must not lose it.
        replies.append(json.dumps({'name': 'Mark', 'job': 'plumber'}))
        mem = app._fan_memory_update('lilly', fan, 'I fix pipes for a living')
        check('a later update cannot drop the complaint',
              mem.get('avoid') == ['chocolate'], mem)

        # Preferences are not complaints: one fan changing the subject must not
        # rewrite the persona for everyone.
        replies.append(json.dumps({'avoid': ['football']}))
        app._fan_memory_update('lilly', fan, "let's talk about films instead")
        check('a preference stays with that fan',
              app._persona_avoid('lilly') == ['chocolate'], store)

        check('a complaint is recognised',
              all(app._is_complaint(t) for t in (
                  'you already said that', 'you keep saying the same thing',
                  'you keep going on about chocolate', 'stop it', 'enough about that',
                  "that's not true", 'I never said that', 'this is boring',
                  'dat klopt niet', 'je herhaalt jezelf')))
        check('ordinary chat is not',
              not any(app._is_complaint(t) for t in (
                  'hey how are you', 'I finish work at 6',
                  'tell me more about that')))

        app._persona_avoid_clear('lilly', 'chocolate')
        check('the creator can hand a subject back',
              app._persona_avoid('lilly') == [])
        app._fan_memory_reset('lilly', fan)
        check('and wipe what she remembers about one fan',
              app._fan_memory('lilly', fan) == {})
    finally:
        app.client = was_client


def test_telegram_off_silences_the_personal_account():
    """"Replies active" off has to stop the personal account too, not just the
    bot — that is what let her keep answering with the box unchecked."""
    store, traced = {}, []
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    app._tg_trace = lambda p, st, d='', fan='': traced.append((st, d))

    store['telegram_auto_lilly'] = json.dumps({'enabled': False})
    check('she does not answer a DM while replies are off',
          app._tgu_plan('lilly', 11, 'fan', 'hey') is None)
    check('and the console says why',
          traced and traced[-1][0] == 'skipped' and 'switched off' in traced[-1][1],
          traced)

    store['telegram_auto_lilly'] = json.dumps({'enabled': True, 'only_fans': ['999']})
    check('turning it back on clears that gate',
          app._tgu_plan('lilly', 11, 'fan', 'hey') is None
          and 'not in the' in traced[-1][1], traced)


def test_a_stopped_account_stays_stopped():
    """Stop used to live in memory only, so every Cloud Run restart brought the
    account back online on its own."""
    store = {}
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    store['tguser_accounts'] = json.dumps({'lilly': {'session': 'x'}})

    app._tgu_stop('lilly')
    check('a stop is written down, so a restart leaves it alone',
          json.loads(store['tguser_accounts'])['lilly'].get('stopped') is True)

    app._tgu_set_stopped('lilly', False)
    check('and starting her again clears it',
          json.loads(store['tguser_accounts'])['lilly'].get('stopped') is False)


def test_a_tag_only_reply_is_never_sent_as_a_blank():
    """The trap behind Telegram going quiet: strip the photo tag off a reply
    that was nothing else and the splitter hands back a single empty message,
    which the sender then skipped without a word."""
    check('a tag-only reply strips to nothing',
          app._tg_parse_photo_tag('[SEND_PHOTO:outfit=1,purpose=tease]')[0] == '')
    check('and one empty message is what the splitter makes of it',
          app._tg_bursts('') == [''])


def test_vault_upload():
    """Pushing studio media into the Fanvue vault: one upload per item however
    often it is pushed, filed into every folder asked for, and never an item
    nobody has kept."""
    import urllib.error
    import utils
    utils._is_operator = lambda: True
    store, calls, uploads = {}, [], []
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    rows = {'m1': {'id': 'm1', 'approved': True, 'kind': 'video', 'mime': 'video/mp4'},
            'm2': {'id': 'm2', 'approved': False, 'kind': 'image', 'mime': 'image/png'}}
    app._media_row = lambda p, mid: rows.get(mid)
    app._media_bytes = lambda r: (b'bytes', r['mime'])

    def up(persona, data, kind, filename, name=None, content_type=''):
        uploads.append((kind, filename, name, content_type))
        return 'uuid-1'
    app._fv_upload_media = up

    def api(persona, method, path, body=None):
        calls.append((method, path, body))
        if path == '/vault/folders/Broken/media':
            raise urllib.error.HTTPError(path, 500, 'boom', {}, None)
        return {}
    app._fanvue_call = api

    def post(body):
        with app.app.test_request_context('/api/fanvue/vault-upload', method='POST', json=body):
            r = app.api_fanvue_vault_upload()
        r, code = (r if isinstance(r, tuple) else (r, 200))
        return code, r.get_json()

    code, d = post({'persona': 'lilith', 'media_id': 'm1', 'name': 'Beach',
                    'folders': ['Teasers', 'New one', 'Broken'], 'create': ['New one']})
    check('upload answers ok', code == 200 and d['ok'], d)
    check('uploaded once as a video with its name',
          uploads == [('video', 'm1.mp4', 'Beach', 'video/mp4')], uploads)
    check('a new folder is created before filing into it',
          ('POST', '/vault/folders', {'name': 'New one'}) in calls, calls)
    check('filed into the folders that work',
          d['folders_ok'] == ['Teasers', 'New one'], d)
    check('one broken folder is reported, not fatal', 'Broken' in d['folder_errors'], d)
    check('folder names are URL-quoted',
          any(c[1] == '/vault/folders/New%20one/media' for c in calls), calls)
    check('the vault item is remembered',
          json.loads(store['fanvue_uploads_lilith']) == {'m1': 'uuid-1'}, store)

    calls.clear()
    code, d = post({'persona': 'lilith', 'media_id': 'm1', 'folders': ['Teasers']})
    check('a second push reuses the vault item', d['reused'] and len(uploads) == 1, d)
    check('and only files it', calls == [('POST', '/vault/folders/Teasers/media',
                                          {'mediaUuids': ['uuid-1']})], calls)

    code, d = post({'persona': 'lilith', 'media_id': 'm2'})
    check('unreviewed media never leaves', code == 400 and not d['ok'], (code, d))
    code, d = post({'persona': 'lilith', 'media_id': 'nope'})
    check('an unknown item is a 404', code == 404, code)

    def bad_body(persona, method, path, body=None):
        calls.append((method, path, body))
        if body == {'mediaUuids': ['uuid-1']}:
            raise urllib.error.HTTPError(path, 400, 'bad', {}, None)
        return {}
    app._fanvue_call = bad_body
    calls.clear()
    code, d = post({'persona': 'lilith', 'media_id': 'm1', 'folders': ['Teasers']})
    check('a refused body shape falls back to the singular key',
          d['folders_ok'] == ['Teasers'] and calls[-1][2] == {'mediaUuid': 'uuid-1'}, (d, calls))


if __name__ == '__main__':
    for fn in (test_direction, test_import, test_identity_never_crosses,
               test_placeholders, test_pacing, test_backlog,
               test_scopes, test_api_errors, test_planner_posts, test_chat_lists,
               test_scope_reporting, test_funnels_off_changes_nothing,
               test_guardrails_can_stop_a_drop,
               test_price_cap_holds_rather_than_discounts,
               test_guardrail_failure_does_not_cost_a_sale,
               test_funnel_config_roundtrip, test_distress_pause_is_written_once,
               test_funnel_exits, test_webhook_subscription,
               test_webhook_accepts_any_known_secret, test_webhook_diagnosis,
               test_unsendable_chats, test_complaints_are_remembered,
               test_telegram_off_silences_the_personal_account,
               test_a_stopped_account_stays_stopped,
               test_a_tag_only_reply_is_never_sent_as_a_blank,
               test_vault_upload):
        print('\n--- %s ---' % fn.__name__)
        restore_app()
        fn()
    print('\n' + ('FAILED: ' + ', '.join(FAILURES) if FAILURES else 'All checks passed.'))
    raise SystemExit(1 if FAILURES else 0)
