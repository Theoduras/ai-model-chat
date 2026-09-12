"""Regression tests for the OnlyFans connection.

Everything here runs against stubs: no OnlyFansAPI key, no network, no database.
What is checked is the part that cannot be checked in production without a real
fan on the other end — who a message came from, what actually goes on the wire,
and that a forged webhook is refused.

Run with: python test_onlyfans.py
"""
import hashlib
import hmac
import json
import os

os.environ.setdefault('GEMINI_API_KEY', 'test')
os.environ.setdefault('ONLYFANS_WEBHOOK_SECRET', 'shh-secret')
os.environ.setdefault('ONLYFANS_WORKER', '0')

import app
import onlyfans as OF

FAILURES = []
_PRISTINE = {k: v for k, v in vars(app).items() if callable(v)}
_OF_PRISTINE = {k: v for k, v in vars(OF).items() if callable(v)}


def restore():
    for k, v in _PRISTINE.items():
        setattr(app, k, v)
    for k, v in _OF_PRISTINE.items():
        setattr(OF, k, v)


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  ' + str(detail)) if not ok else ''))
    if not ok:
        FAILURES.append(name)


def test_signature():
    """A forged delivery must never be acted on: anyone who finds the URL could
    otherwise make the persona reply to a fan who never wrote."""
    body = b'{"event":"messages.received"}'
    good = hmac.new(b'shh', body, hashlib.sha256).hexdigest()
    check('a correct signature verifies', OF.verify_signature(body, good, 'shh')[0])
    check('the sha256= prefix is tolerated',
          OF.verify_signature(body, 'sha256=' + good, 'shh')[0])
    check('upper case hex still verifies',
          OF.verify_signature(body, good.upper(), 'shh')[0])
    check('a wrong secret does not', not OF.verify_signature(body, good, 'other')[0])
    check('a changed body does not',
          not OF.verify_signature(body + b' ', good, 'shh')[0])
    check('no header is refused', not OF.verify_signature(body, '', 'shh')[0])
    check('and no secret is refused, never waved through',
          not OF.verify_signature(body, good, '')[0])


def test_strip_html():
    """Message text arrives as HTML. The model must see what the fan typed."""
    check('paragraphs come out as text', OF.strip_html('<p>hey you</p>') == 'hey you')
    check('breaks become newlines', OF.strip_html('a<br>b') == 'a\nb')
    check('entities are decoded', OF.strip_html('<p>me &amp; you</p>') == 'me & you')
    check('plain text is untouched', OF.strip_html('hey') == 'hey')
    check('nothing is empty', OF.strip_html(None) == '')


def test_direction():
    """Who said what. Getting this wrong is what makes a persona read her own
    words back as the fan's and greet him by her own name."""
    d = OF.direction_of
    check('isSentByMe is believed', d({'isSentByMe': True, 'text': 'x'}, '9') == 'out')
    check('and its absence means the fan',
          d({'isSentByMe': False, 'text': 'x'}, '9') == 'in')
    check('fromUser matching the fan is theirs',
          d({'fromUser': {'id': 9}, 'text': 'x'}, '9') == 'in')
    check('fromUser matching us is ours',
          d({'fromUser': {'id': 1}, 'text': 'x'}, '9', '1') == 'out')
    check('a third id is unresolved',
          d({'fromUser': {'id': 5}, 'text': 'x'}, '9', '1') == '')
    check('what we just sent is recognised as ours',
          d({'text': '<p>hey you</p>'}, '9', '1', {'hey you'}) == 'out')
    check('and nothing at all stays unresolved', d({'text': 'hello'}, '9', '1') == '')


def test_chat_reading():
    chat = {'fan': {'id': 42, 'username': 'joe', 'isPerformer': False},
            'canSendMessage': True, 'unreadMessagesCount': 2}
    fan, handle, is_creator, chat_id = OF.user_of_chat(chat)
    check('the fan id is read', fan == '42')
    check('the chat id is the fan id', chat_id == '42')
    check('the handle is read', handle == 'joe')
    check('a fan is not a creator', not is_creator)
    check('another creator is flagged',
          OF.user_of_chat({'fan': {'id': 7, 'isPerformer': True}})[2])
    check('a chat we may not write to says why',
          OF.chat_unsendable({'canSendMessage': False,
                              'canNotSendReason': 'blocked'}) == 'blocked')
    check('and a normal chat says nothing', OF.chat_unsendable(chat) == '')
    check('a fan seen just now is online',
          OF.chat_online({'fan': {'isOnline': True}}))
    check('a fan not seen for days is not', not OF.chat_online(
        {'fan': {'lastSeen': '2020-01-01T00:00:00+00:00'}}))


def test_paging():
    """OnlyFansAPI pages by handing back the next URL. Asking for page numbers
    the way Fanvue does would return the first page for ever."""
    calls = []

    def fake_call(method, path, body=None, idem=None, key=None, timeout=None):
        calls.append(path)
        if 'cursor=2' in path:
            return {'data': [{'id': 3}], '_pagination': {'next_page': None}}
        return {'data': [{'id': 1}, {'id': 2}],
                '_pagination': {'next_page': 'https://x/api/a/chats?cursor=2'}}

    OF.call = fake_call
    rows = OF.paged('/api/a/chats')
    check('every page is walked', [r['id'] for r in rows] == [1, 2, 3], rows)
    check('the second call follows next_page', 'cursor=2' in calls[1], calls)

    calls.clear()
    OF.call = lambda *a, **k: (calls.append(a) or
                               {'data': [{'id': 1}], '_pagination':
                                {'next_page': 'https://x/api/a/chats?cursor=1'}})
    check('a page that repeats itself stops the walk', len(OF.paged('/api/a/chats')) == 1)
    restore()


def test_send_body():
    """What actually goes on the wire — the one thing a fan sees."""
    sent = []
    OF.call = lambda m, p, body=None, idem=None, **k: (
        sent.append((m, p, body, idem)) or {'data': {'id': 5}})

    OF.send('acct_1', '42', 'hey you')
    check('a plain reply is text only', sent[-1][2] == {'text': 'hey you'}, sent[-1])
    check('it posts to the chat', sent[-1][1] == '/api/acct_1/chats/42/messages')
    check('and carries an idempotency key so a retry cannot double-send',
          bool(sent[-1][3]))

    OF.send('acct_1', '42', 'look', price=6.97, media=['m1'])
    check('a paid message carries the price in dollars',
          sent[-1][2].get('price') == 6.97, sent[-1][2])
    check('and the media it locks', sent[-1][2].get('mediaFiles') == ['m1'])

    failed = ''
    try:
        OF.send('acct_1', '42', 'look', price=6.97)
    except OF.OnlyFansApiError as e:
        failed = str(e)
    check('a paid message with no media is refused before it is sent',
          'media' in failed, failed)
    restore()


def test_ppv_price_conversion():
    """The engine holds prices in cents; OnlyFans wants dollars, and takes
    nothing outside $3-$200. A tier outside that must not fail silently."""
    sent = []
    OF.send = lambda acct, chat, text, price=0, media=(), idem=None: (
        sent.append((text, price, list(media))) or {'data': {'id': 9}})
    app._get_setting = lambda k, d=None: 'acct_1' if k.startswith('onlyfans_account_') else d

    mid = app.PLAT_ONLYFANS.send_ppv('lilly', '', '42', 'unlock me', ['m1'], 1000)
    check('$10.00 goes out as 10.0', sent[-1][1] == 10.0, sent)
    check('the message id comes back', mid == '9', mid)

    # The direct transport answers with OnlyFans' own body, where the id is top
    # level rather than wrapped in `data`. Reading only the wrapped shape lost
    # the id of every drop sold on that path.
    OF.send = lambda acct, chat, text, price=0, media=(), idem=None: (
        sent.append((text, price, list(media))) or {'id': 77, 'text': text})
    mid = app.PLAT_ONLYFANS.send_ppv('lilly', '', '42', 'unlock me', ['m1'], 1000)
    check('an unwrapped answer still yields the message id', mid == '77', mid)

    for cents, why in ((100, 'under the floor'), (30000, 'over the ceiling')):
        refused = ''
        try:
            app.PLAT_ONLYFANS.send_ppv('lilly', '', '42', 'x', ['m1'], cents)
        except OF.OnlyFansApiError as e:
            refused = str(e)
        check(f'a price {why} is refused with a reason', 'between' in refused, refused)
    restore()


def test_ppv_amount():
    check('the unlock amount is read from replacePairs',
          OF.ppv_amount_cents({'replacePairs': {'{AMOUNT}': '$12.50'}}) == 1250)
    check('a plain amount works too', OF.ppv_amount_cents({'amount': 9}) == 900)
    check('and nothing readable is zero, never a guess',
          OF.ppv_amount_cents({'replacePairs': {}}) == 0)


def _webhook_post(client, body, secret='shh-secret', idem='evt_1'):
    raw = json.dumps(body).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    headers = {'Signature': sig, 'Content-Type': 'application/json'}
    if idem:
        headers['X-OFAPI-Idempotency-Key'] = idem
    return client.post('/webhooks/onlyfans', data=raw, headers=headers)


def test_webhook():
    """The receiver: verified, deduplicated, and answered immediately — the work
    happens on the worker, because a slow handler is retried as a failure."""
    store = {'onlyfans_account_lilly': 'acct_1'}
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    app.db_list_personas = lambda *a, **k: [{'slug': 'lilly'}]
    app._of_due.clear()
    client = app.app.test_client()

    r = _webhook_post(client, {'event': 'messages.received', 'account_id': 'acct_1',
                               'payload': {'fromUser': {'id': 42}, 'text': 'hey'}})
    check('a signed delivery is accepted', r.status_code == 200, r.status_code)
    check('and the persona is queued for a round', 'lilly' in app._of_due, app._of_due)

    app._of_due.clear()
    r = client.post('/webhooks/onlyfans', data=b'{"event":"messages.received"}',
                    headers={'Signature': 'deadbeef'})
    check('a forged delivery is refused', r.status_code == 401, r.status_code)
    check('and nothing is queued off it', not app._of_due)

    r = _webhook_post(client, {'event': 'messages.received', 'account_id': 'acct_1',
                               'payload': {'fromUser': {'id': 42}}}, idem='evt_dup')
    r2 = _webhook_post(client, {'event': 'messages.received', 'account_id': 'acct_1',
                                'payload': {'fromUser': {'id': 42}}}, idem='evt_dup')
    check('the same event twice is only applied once',
          r2.get_json().get('duplicate') is True, r2.get_json())

    r = _webhook_post(client, {'event': 'messages.received', 'account_id': 'acct_other',
                               'payload': {}}, idem='evt_2')
    check('an account we do not run is ignored, not answered',
          r.get_json().get('ignored') == 'unknown account', r.get_json())

    # A human replying in the OnlyFans app has to reach the transcript, or the
    # persona answers as though it never happened.
    logged = []
    app._log_x_message = lambda p, k, h, d, t: logged.append((k, d, t))
    _webhook_post(client, {'event': 'messages.sent', 'account_id': 'acct_1',
                           'payload': {'fromUser': {'id': 42, 'username': 'joe'},
                                       'text': '<p>on my way</p>'}}, idem='evt_3')
    check("a human's own reply is stored as ours",
          logged and logged[-1] == ('of:42', 'out', 'on my way'), logged)

    # Typing is ephemeral: it carries no idempotency key and must only push the
    # reply back, never trigger one of its own.
    app._of_due.clear()
    _webhook_post(client, {'event': 'users.typing', 'account_id': 'acct_1',
                           'payload': {'id': 42}}, idem=None)
    due = app._of_due.get('lilly')
    check('typing holds the reply back rather than answering mid-sentence',
          due is not None and due > 0, app._of_due)
    restore()


def test_round_end_to_end():
    """One whole round on stubbed transport: a fan writes, she answers, and the
    reply goes out through OnlyFansAPI with both sides kept in the transcript."""
    store = {'onlyfans_account_lilly': 'acct_1',
             'onlyfans_account_meta_lilly': json.dumps({'onlyfans_id': '1',
                                                        'username': 'lilly'}),
             'onlyfans_auto_lilly': json.dumps({'enabled': True, 'humanize': False,
                                                'reply_limit': 5})}
    logged, sent, traced = [], [], []
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    app._log_x_message = lambda p, k, h, d, t: logged.append((k, d, t))
    app._fanvue_msg_count = lambda p, k: 3
    app._fanvue_saved_history = lambda p, k, limit=40: []
    app._fan_memory = lambda p, k: ''
    app._fan_memory_update = lambda *a, **k: None
    app._fan_memory_block = lambda m, p: ''
    app._funnel_read = lambda *a, **k: ('', {}, None)
    app._persona_text = lambda p, instr, **k: 'hey you, how was the shift?'
    app._fv_trace = lambda p, st, d='', fan='': traced.append((st, d))
    OF.chats = lambda acct, unread_only=False, want=None: [
        {'fan': {'id': 42, 'username': 'joe'}, 'canSendMessage': True}]
    OF.messages = lambda acct, chat, want=20: [
        {'id': 7, 'text': '<p>hey</p>', 'isSentByMe': False,
         'createdAt': '2026-01-01T00:00:00+00:00'}]
    OF.send = lambda acct, chat, text, price=0, media=(), idem=None: (
        sent.append((acct, chat, text, price)) or {'data': {'id': 8}})

    actions, log = app._plat_auto_round(app.PLAT_ONLYFANS, 'lilly')
    check('she replied once', actions.get('replies') == 1, (actions, log))
    check('the reply went to that chat on that account',
          sent and sent[0][:2] == ('acct_1', '42'), sent)
    check('it is what the model wrote', sent and 'how was the shift' in sent[0][2], sent)
    check('and it was free', sent and not sent[0][3], sent)
    check('the fan message is stored under the OnlyFans key',
          ('of:42', 'in', 'hey') in logged, logged)
    check('so is her reply', any(k == 'of:42' and d == 'out' for k, d, t in logged), logged)
    check('the cursor is written so the same message is not answered twice',
          json.loads(store.get('onlyfans_cursor_lilly') or '{}').get('42') == '7', store)
    check("and none of it touched Fanvue's state",
          not any(k.startswith('fanvue_') for k in store), sorted(store))

    # Second round, nothing new: the cursor must hold her back.
    sent.clear()
    actions, log = app._plat_auto_round(app.PLAT_ONLYFANS, 'lilly')
    check('the same message is not answered again', not sent, sent)
    restore()

def test_signin_flow():
    """Signing a creator in from our own dashboard: the password is relayed and
    forgotten, 2FA and the face check are asked for, and the finished account
    attaches itself to the persona."""
    store, calls = {}, []
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    app._log_x_event = lambda *a, **k: None
    app.db_list_personas = lambda *a, **k: [{'slug': 'lilly'}]
    # The console's own routes are behind the operator/owner guard; an admin
    # caller is the shortest way past it that does not weaken the guard itself.
    app._current_user = lambda: {'id': 1, 'email': 'her@example.com',
                                 'is_admin': True, 'status': 'active'}
    OF.api_key = lambda: 'team-key'
    client = app.app.test_client()

    stage = {'now': {'attempt_id': 'auth_1',
                     'lastAttempt': {'needs_otp': True, 'otp_phone_ending': '42'}}}

    def fake_call(method, path, body=None, idem=None, key=None, timeout=None):
        calls.append((method, path, body))
        if method == 'POST' and path == '/api/authenticate':
            return {'attempt_id': 'auth_1'}
        return stage['now']

    OF.call = fake_call

    r = client.post('/api/onlyfans/connect/start', json={
        'persona': 'lilly', 'email': 'her@example.com', 'password': 'hunter2'}).get_json()
    check('the sign-in starts', r.get('ok'), r)
    check('the attempt id is kept', store.get('onlyfans_attempt_lilly') == 'auth_1', store)
    check('the password is never written down',
          not any('hunter2' in str(v) for v in store.values()), store)
    check('and it left us once, in the sign-in call only',
          sum(1 for c in calls if 'hunter2' in str(c[2] or {})) == 1, calls)

    r = client.get('/api/onlyfans/connect/status?persona=lilly').get_json()
    check('a code is asked for', r['attempt']['needs_otp'], r)
    check('and the console can say where it went',
          r['attempt']['otp_phone_ending'] == '42', r)

    # A wrong code must leave the attempt open rather than start over: the face
    # check that may follow is limited to three a day.
    stage['now'] = {'attempt_id': 'auth_1',
                    'lastAttempt': {'needs_otp': True, 'error_message': 'wrong code',
                                    'error_code': 'WRONG_2FA'}}
    r = client.post('/api/onlyfans/connect/code',
                    json={'persona': 'lilly', 'code': '000000'}).get_json()
    check('a wrong code says so', r['attempt']['error'] == 'wrong code', r)
    check('and the attempt is still open to try again',
          store.get('onlyfans_attempt_lilly') == 'auth_1', store)

    stage['now'] = {'attempt_id': 'auth_1', 'lastAttempt': {
        'needs_face_otp': True, 'face_otp_verification_url': 'https://of/face'}}
    r = client.get('/api/onlyfans/connect/status?persona=lilly').get_json()
    check('a face check is surfaced with its link',
          r['attempt']['needs_face'] and r['attempt']['face_url'] == 'https://of/face', r)

    stage['now'] = {'attempt_id': 'auth_1', 'state': 'authenticated',
                    'progress': 'signed_in', 'lastAttempt': {'success': True},
                    'account': {'id': 'acct_9', 'display_name': 'Lilly',
                                'onlyfans_data': {'id': 77, 'username': 'lilly_x'}}}
    r = client.post('/api/onlyfans/connect/code',
                    json={'persona': 'lilly', 'face_done': True}).get_json()
    check('the finished sign-in is recognised', r['attempt']['done'], r)
    check('the account is attached to the persona',
          store.get('onlyfans_account_lilly') == 'acct_9', store)
    check('with her own OnlyFans id, so history can tell who spoke',
          json.loads(store['onlyfans_account_meta_lilly'])['onlyfans_id'] == '77', store)
    check('the attempt is cleared once it is done',
          not store.get('onlyfans_attempt_lilly'), store)
    check('and she starts from a clean cursor rather than a year of history',
          store.get('onlyfans_cursor_lilly') == '{}', store)

    r = client.post('/api/onlyfans/connect/code',
                    json={'persona': 'lilly', 'code': '1'}).get_json()
    check('a code sent with no sign-in running is refused', not r.get('ok'), r)
    restore()

def test_a_connected_account_that_cannot_sign_says_so():
    """Connected is only holding her credentials. While signing is stale every
    request behind it is refused, and the console showed a green connection
    beside an empty chat list and an empty vault, with the reason nowhere.
    """
    of = app.PLAT_ONLYFANS

    class _Rules:
        verified = None

        def state(self):
            return {'verified': self.verified}

    # This file runs against the API transport, where OF is the middleman with
    # no hold of its own and of_rules is not even imported; direct mode is what
    # is being checked, so both are stood in for.
    rules, was_rules = _Rules(), app.of_rules
    app.of_rules = rules
    app._of_direct = lambda: True
    of.connected = lambda persona: True
    app.OF.held = lambda: {}
    try:
        check('signing that cannot be checked is reported',
              'signing is repaired' in of.reachable('lilly'), of.reachable('lilly'))
        rules.verified = False
        check('signing that is disproven is reported',
              'signing is repaired' in of.reachable('lilly'), of.reachable('lilly'))
        rules.verified = True
        check('a proven set leaves nothing to report', of.reachable('lilly') == '',
              of.reachable('lilly'))
        app.OF.held = lambda: {app._of_account('lilly'): 42}
        check('a held account says how long it is held',
              '42s' in of.reachable('lilly'), of.reachable('lilly'))
    finally:
        app.of_rules = was_rules
        del app.OF.held, of.connected
        restore()


def test_settings_are_namespaced():
    """The two platforms must never read each other's state: one shared key
    would have OnlyFans replying with Fanvue's PPV progress."""
    fv, of = app.PLAT_FANVUE, app.PLAT_ONLYFANS
    check('auto settings differ', fv.k('auto', 'lilly') != of.k('auto', 'lilly'))
    check('fanvue keeps its historical key', fv.k('auto', 'lilly') == 'fanvue_auto_lilly')
    check('onlyfans has its own', of.k('auto', 'lilly') == 'onlyfans_auto_lilly')
    check('fan keys are namespaced too',
          (fv.fan_key('1'), of.fan_key('1')) == ('fv:1', 'of:1'))
    check('the enabled list is per platform',
          fv.k('auto_personas') != of.k('auto_personas'))


if __name__ == '__main__':
    for fn in (test_signature, test_strip_html, test_direction, test_chat_reading,
               test_paging, test_send_body, test_ppv_price_conversion,
               test_ppv_amount, test_webhook, test_round_end_to_end,
               test_signin_flow, test_a_connected_account_that_cannot_sign_says_so,
               test_settings_are_namespaced):
        restore()
        fn()
    restore()
    print()
    if FAILURES:
        print('FAILED: ' + ', '.join(FAILURES))
        raise SystemExit(1)
    print('All checks passed.')
