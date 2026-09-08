"""Tests for the Telegram user-account runner.

A fan firing off several messages in a row must get one reply, not one per
message, and a chat that started before the account was connected must be read
back before she answers it. Telethon is stubbed out, so this needs no
credentials and no network.
Run with: python test_tg_burst.py
"""
import asyncio
import sys
import time
import types

_events = types.ModuleType('telethon.events')
_events.NewMessage = object
_telethon = types.ModuleType('telethon')
_telethon.TelegramClient = object
_telethon.events = _events
_sessions = types.ModuleType('telethon.sessions')
_sessions.StringSession = object
_errors = types.ModuleType('telethon.errors')
_errors.SessionPasswordNeededError = type('SessionPasswordNeededError', (Exception,), {})
for _name, _mod in [('telethon', _telethon), ('telethon.events', _events),
                    ('telethon.sessions', _sessions), ('telethon.errors', _errors)]:
    sys.modules.setdefault(_name, _mod)

from tg_user import AccountRunner  # noqa: E402

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ok   {name}')
    else:
        print(f'  FAIL {name} {detail}')
        FAILURES.append(name)


class FakeAction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeClient:
    def __init__(self, history=None, dialogs=None):
        self.sent = []
        self.history = history or []      # newest first, as Telegram returns it
        self.history_calls = []
        self.dialogs = dialogs or []

    async def get_me(self):
        return types.SimpleNamespace(id=999)

    async def get_dialogs(self, limit=None):
        return list(self.dialogs)

    def action(self, *a, **kw):
        return FakeAction()

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    async def get_messages(self, chat_id, limit=None, offset_id=None):
        self.history_calls.append((chat_id, limit, offset_id))
        return list(self.history)


class FakeEvent:
    out = False
    is_private = True

    def __init__(self, text, chat_id=7, name='twippa', msg_id=100):
        self.raw_text = text
        self.chat_id = chat_id
        self.message = types.SimpleNamespace(date=None, id=msg_id)
        self._name = name

    async def get_sender(self):
        return types.SimpleNamespace(username=self._name, first_name='', bot=False)


def _msg(text, out=False, msg_id=1, at=None):
    from datetime import datetime, timezone
    return types.SimpleNamespace(out=out, raw_text=text, id=msg_id,
                                 date=datetime.fromtimestamp(at or 1700000000,
                                                             timezone.utc))


def _runner(calls, traces, pre_delay=1.0, **kw):
    def plan(chat_id, name, text, texts=None):
        calls.append((chat_id, texts))
        return {'read': 0, 'cps': 999, 'chunks': [f'answer to {len(texts)}']}

    r = AccountRunner('p', 1, 'h', 's', plan,
                      on_trace=lambda p, stage, detail: traces.append((stage, detail)),
                      pre_delay=lambda chat_id: pre_delay, **kw)
    r.BURST_SECONDS = 0.5
    return r


async def run():
    calls, traces = [], []
    r = _runner(calls, traces)
    c = FakeClient()

    for text in ('hey', 'wie gehts', 'ja toll', 'and you'):
        await r._handle(c, FakeEvent(text))
        await asyncio.sleep(0.3)
    await asyncio.sleep(3.5)
    check('four messages become one reply', len(calls) == 1 and len(c.sent) == 1, calls)
    check('every message reaches the prompt',
          calls and calls[0][1] == ['hey', 'wie gehts', 'ja toll', 'and you'], calls)

    await r._handle(c, FakeEvent('still there?'))
    await asyncio.sleep(3.5)
    check('a message after the reply starts a new burst',
          len(calls) == 2 and calls[1][1] == ['still there?'], calls)

    await r._handle(c, FakeEvent('a', chat_id=8, name='other'))
    await r._handle(c, FakeEvent('b', chat_id=9, name='third'))
    await asyncio.sleep(3.5)
    check('separate fans are never merged',
          len(calls) == 4 and {calls[2][0], calls[3][0]} == {8, 9}, calls)

    imported = []
    seen = {'n': 0}

    def needs_history(chat_id):
        seen['n'] += 1
        return not imported

    h = _runner(calls, traces, needs_history=needs_history,
                on_history=lambda cid, name, rows, first_at=0:
                    imported.append((cid, rows, first_at)))
    hc = FakeClient(history=[_msg('nice talking to you', out=True, msg_id=3, at=1700000200),
                            _msg('im in berlin', msg_id=2, at=1700000100),
                            _msg('', msg_id=1, at=1700000000)])
    await h._handle(hc, FakeEvent('hey again', chat_id=21, name='old'))
    await asyncio.sleep(3.5)
    check('the chat is read back before the first reply', len(imported) == 1, imported)
    check('read-back is oldest first, with directions and empties dropped',
          imported and imported[0][1] == [('in', 'im in berlin'), ('out', 'nice talking to you')],
          imported)
    check('the read-back reports when the conversation started',
          imported and imported[0][2] == 1700000000, imported)
    check('the read-back stops at the new message',
          hc.history_calls and hc.history_calls[0][2] == 100, hc.history_calls)

    await h._handle(hc, FakeEvent('and you?', chat_id=21, name='old'))
    await asyncio.sleep(3.5)
    check('an already-read chat is not imported twice',
          len(imported) == 1 and len(hc.history_calls) == 1, hc.history_calls)

    # A reply that strips down to nothing used to be planned, logged as "1
    # message(s)", then skipped by the sender without a word in the console.
    blank, bt = [], []
    b = AccountRunner('p', 1, 'h', 's',
                      lambda chat_id, name, text, texts=None:
                          {'read': 0, 'cps': 999, 'chunks': ['   ']},
                      on_trace=lambda p, stage, detail: bt.append((stage, detail)),
                      pre_delay=lambda chat_id: 0)
    b.BURST_SECONDS = 0.5
    bc = FakeClient()
    await b._handle(bc, FakeEvent('you there?', chat_id=31, name='blankfan'))
    await asyncio.sleep(1.5)
    check('an empty reply sends nothing and says so',
          not bc.sent and [t for t in bt if t[0] == 'no-reply']
          and not [t for t in bt if t[0] == 'planning'], (bc.sent, bt))

    # Gemini has no deadline of its own, so a stalled call held the reply open
    # forever with nothing in the log.
    st = []
    slow = AccountRunner('p', 1, 'h', 's',
                         lambda chat_id, name, text, texts=None: time.sleep(30),
                         on_trace=lambda p, stage, detail: st.append((stage, detail)),
                         pre_delay=lambda chat_id: 0)
    slow.BURST_SECONDS = 0.5
    slow.PLAN_SECONDS = 1
    sc = FakeClient()
    await slow._handle(sc, FakeEvent('hello?', chat_id=32, name='waiting'))
    await asyncio.sleep(2.5)
    check('a reply that never gets written is given up on, out loud',
          not sc.sent and any(st_ == 'error' and 'longer than' in d for st_, d in st), st)

    # Same for a send that hangs: connection_retries=None means it never returns.
    ht = []
    hung = AccountRunner('p', 1, 'h', 's',
                         lambda chat_id, name, text, texts=None:
                             {'read': 0, 'cps': 999, 'chunks': ['hi']},
                         on_trace=lambda p, stage, detail: ht.append((stage, detail)),
                         on_error=lambda p, e: ht.append(('on_error', e)),
                         pre_delay=lambda chat_id: 0)
    hung.BURST_SECONDS = 0.5
    hung.SEND_SECONDS = 1

    class HangingClient(FakeClient):
        async def send_message(self, chat_id, text):
            await asyncio.sleep(30)

    await hung._handle(HangingClient(), FakeEvent('hi', chat_id=33, name='stuck'))
    await asyncio.sleep(4.0)
    check('a send that hangs is reported instead of waiting forever',
          any(s == 'error' and 'failed' in d for s, d in ht), ht)

    # A restart drops whatever burst was in flight, and the follow-up loop skips
    # fans who spoke last — so without this she never answers them at all.
    def _dialog(chat_id, name, text, out=False, age=0):
        from datetime import datetime, timedelta, timezone as tz
        return types.SimpleNamespace(
            id=chat_id, is_user=True,
            entity=types.SimpleNamespace(id=chat_id, username=name, first_name='',
                                         bot=False),
            message=types.SimpleNamespace(
                out=out, raw_text=text, id=5,
                date=datetime.now(tz.utc) - timedelta(seconds=age)))

    ct = []
    cu = _runner([], ct, pre_delay=0)
    cu.CATCHUP_AFTER = 0.1
    cc = FakeClient(dialogs=[
        _dialog(41, 'ignored', 'Hey'),
        _dialog(42, 'answered', 'nice one', out=True),
        _dialog(43, 'ancient', 'hello?', age=cu.CATCHUP_MAX_AGE + 60),
    ])
    await cu._catch_up(cc)
    check('a fan left hanging by a restart is answered on reconnect',
          [s for s, d in ct if s == 'missed'] and len(cc.sent) == 1
          and cc.sent[0][0] == 41, (ct, cc.sent))
    check('a chat she already replied to is left alone',
          not any(chat == 42 for chat, _ in cc.sent), cc.sent)
    check('and an old message is not dredged up',
          not any(chat == 43 for chat, _ in cc.sent), cc.sent)

    r.MAX_HOLD_SECONDS = 2
    for _ in range(12):
        await r._handle(c, FakeEvent('spam', chat_id=11, name='spammer'))
        await asyncio.sleep(0.3)
    await asyncio.sleep(2.0)
    spam = [x for x in calls if x[0] == 11]
    check('a fan who never stops typing is still answered',
          len(spam) >= 1 and len(spam[0][1]) > 1, spam)


asyncio.run(run())
print('\nall burst tests passed' if not FAILURES else f'\n{len(FAILURES)} FAILED')
sys.exit(1 if FAILURES else 0)
