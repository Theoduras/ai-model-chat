"""The whole OnlyFans product, locally, against a stand-in for the site.

Everything here is the real code: the vault, the signing, the transport, the
watcher, the reply round, the console routes. What is faked is OnlyFans (see
of_stub.py) and the model, because neither is what breaks.

The point is the loop: a change to chat, PPV, signing or the watcher is tried
here in seconds rather than in a deploy and a manual sign-in.

Run directly: python3 test_of_local.py
"""
import json
import os
import sys
import tempfile
import time

os.environ['no_proxy'] = os.environ['NO_PROXY'] = '*'
os.environ['ONLYFANS_TRANSPORT'] = 'direct'
os.environ.setdefault('SECRET_KEY', 'local-harness-secret')
os.environ.setdefault('ADMIN_PASSWORD', 'operator')
os.environ.setdefault('ONLYFANS_WEBHOOK_SECRET', 'local-harness-webhook')
os.environ['DATABASE_URL'] = 'sqlite:///' + tempfile.mkdtemp() + '/local.db'

import of_stub  # noqa: E402

INBOX, BASE = of_stub.start()
# Before app, so every module that reads it sees the stand-in.
os.environ['ONLYFANS_BASE_URL'] = BASE

import of_client  # noqa: E402
import of_events  # noqa: E402
import of_rules  # noqa: E402
import of_session  # noqa: E402

PASS, FAIL = [], []
ACCOUNT, PERSONA = 'acct-local', 'lilith'
FAN, HANDLE = 99, 'fan_dave'


def check(label, ok, extra=''):
    (PASS if ok else FAIL).append(label)
    print(('PASS ' if ok else 'FAIL ') + label + (f'  [{extra}]' if extra and not ok else ''))


class Reply:
    """Gemini's part, which is not what this harness is testing."""

    text = 'hey you 😊 just got in, still in that dress'
    candidates = []


class Model:
    def generate_content(self, **kw):
        return Reply()


def main():
    import app

    app.client = type('C', (), {'models': Model()})()
    of_rules.refresh()
    check('signing rules load', of_rules.state().get('ready') is True)

    of_session.put(ACCOUNT, {'cookie': 'sess=local; auth_id=4242', 'x_bc': 'bc',
                             'user_id': '4242', 'user_agent': 'Mozilla/5.0 local'})
    with app.app.app_context():
        app._set_setting(f'onlyfans_account_{PERSONA}', ACCOUNT)
    check('the vault stores her session encrypted',
          'local' not in str(app._get_setting(f'onlyfans_vault_{ACCOUNT}') or ''))

    # ── The session, and the chat it is for ──────────────────────────────────
    INBOX.chat(FAN, HANDLE, 'hey gorgeous, what you up to?', mid=2)
    alive, who = of_client.check(ACCOUNT)
    check('OnlyFans accepts our signature', alive is True, who)
    check('and names the account', who == 'lilith_x', who)

    chats = of_client.chats(ACCOUNT, unread_only=True)
    check('the unread chat reads', len(chats) == 1, str(chats))
    fan_id, handle, _, chat_id = of_client.user_of_chat(chats[0])
    check('with the fan on it', (fan_id, handle) == (str(FAN), HANDLE), str((fan_id, handle)))

    msgs = of_client.messages(ACCOUNT, chat_id, want=20)
    check('the history reads oldest first',
          [of_client.text_of(m) for m in msgs] == ['hey gorgeous, what you up to?'],
          str(msgs))
    check('the fan message is inbound',
          of_client.direction_of(msgs[-1], fan_id, '4242') == 'in')

    of_client.typing(ACCOUNT, chat_id)
    sent = of_client.send(ACCOUNT, chat_id, 'morning you 😈')
    check('a reply sends', str(sent.get('id')).startswith('9'), str(sent))
    check('with the text intact', INBOX.texts()[-1] == 'morning you 😈', str(INBOX.texts()))

    try:
        of_client.send(ACCOUNT, chat_id, 'unlock me', price=12.0)
        check('a paid message with no media is refused', False)
    except of_client.OnlyFansError as e:
        check('a paid message with no media is refused', 'media' in str(e))

    of_client.send(ACCOUNT, chat_id, 'unlock me', price=12.0, media=['31337'])
    check('a PPV sends with its price', INBOX.sent[-1].get('price') == 12.0,
          str(INBOX.sent[-1]))
    check('every request we made was correctly signed', not INBOX.refused,
          str(INBOX.refused[:2]))

    # ── The watcher ──────────────────────────────────────────────────────────
    events = []
    of_events.sink(lambda event, account, payload, idem: events.append(event) or True)
    watcher = of_events.Watcher(ACCOUNT)
    check('the first pass primes rather than answering a backlog',
          watcher.poll() == [])
    INBOX.chat(FAN, HANDLE, 'you there?', mid=3,
               at=time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime()))
    check('a new fan message becomes one event',
          watcher.poll() == ['messages.received'], str(events))
    check('and is not delivered twice', watcher.poll() == [])

    # ── The round the persona actually runs ──────────────────────────────────
    with app.app.app_context():
        out = app._plat_round_now(app.PLAT_ONLYFANS, PERSONA, block=True)
    check('the round answers the fan', bool(out) and out[0]['replies'] == 1, str(out))
    # The humanizer paces the send on its own thread, so the round returns
    # before the message lands. Waiting for it is the point: this is what a
    # fan sees, and it is the last thing to break.
    for _ in range(600):
        if len(INBOX.sent) > 2:
            break
        time.sleep(0.5)
    check('and the reply reaches OnlyFans', len(INBOX.sent) > 2,
          str(INBOX.texts()))

    # ── The console, as an operator sees it ──────────────────────────────────
    from db import SessionLocal, User
    s = SessionLocal()
    operator = User(email='operator@local', password_hash='x', role='admin',
                    status='active', tier='agency')
    s.add(operator)
    s.commit()
    uid = operator.id
    s.close()
    c = app.app.test_client()
    with c.session_transaction() as session:
        session['user_id'] = uid
        session['admin_authed'] = True

    r = c.post('/api/onlyfans/signing/test', json={'persona': PERSONA})
    check('the signing panel answers', r.status_code == 200 and r.json.get('ok'),
          str(r.status_code))
    check('and a request from this server works',
          (r.json.get('server_call') or {}).get('ok') is True,
          json.dumps(r.json.get('server_call')))

    r = c.get('/api/onlyfans/watch?persona=' + PERSONA)
    rows = (r.json or {}).get('accounts') or []
    check('the watcher panel lists her account',
          any(row.get('account') == ACCOUNT for row in rows), json.dumps(rows)[:200])

    r = c.get('/api/onlyfans/health')
    check('health answers', r.status_code == 200 and r.json.get('direct') is True)

    print()
    print(f'{len(PASS)} passed, {len(FAIL)} failed')
    if FAIL:
        print('failed: ' + '; '.join(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
