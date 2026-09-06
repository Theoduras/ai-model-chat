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
    app._fv_trace = lambda p, s, dt='': traced.append((s, dt))
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
    app._fv_trace = lambda p, s, dt='': traced.append((s, dt))
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
    app._fv_trace = lambda p, s, dt='': traced.append((s, dt))
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
    app._fv_trace = lambda p, st, dt='': traced.append((st, dt))
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
    app._fv_trace = lambda p, s, d='': traced.append((s, d))
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


if __name__ == '__main__':
    for fn in (test_direction, test_import, test_identity_never_crosses,
               test_placeholders, test_pacing, test_backlog,
               test_scopes, test_api_errors, test_chat_lists,
               test_scope_reporting, test_funnels_off_changes_nothing,
               test_guardrails_can_stop_a_drop,
               test_price_cap_holds_rather_than_discounts,
               test_guardrail_failure_does_not_cost_a_sale,
               test_funnel_config_roundtrip, test_distress_pause_is_written_once):
        print('\n--- %s ---' % fn.__name__)
        restore_app()
        fn()
    print('\n' + ('FAILED: ' + ', '.join(FAILURES) if FAILURES else 'All checks passed.'))
    raise SystemExit(1 if FAILURES else 0)
