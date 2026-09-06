"""Tests for burst handling in the Telegram user-account runner.

A fan firing off several messages in a row must get one reply, not one per
message. Telethon is stubbed out, so this needs no credentials and no network.
Run with: python test_tg_burst.py
"""
import asyncio
import sys
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
    def __init__(self):
        self.sent = []

    def action(self, *a, **kw):
        return FakeAction()

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


class FakeEvent:
    out = False
    is_private = True

    def __init__(self, text, chat_id=7, name='twippa'):
        self.raw_text = text
        self.chat_id = chat_id
        self.message = types.SimpleNamespace(date=None)
        self._name = name

    async def get_sender(self):
        return types.SimpleNamespace(username=self._name, first_name='', bot=False)


def _runner(calls, traces, pre_delay=1.0):
    def plan(chat_id, name, text, texts=None):
        calls.append((chat_id, texts))
        return {'read': 0, 'cps': 999, 'chunks': [f'answer to {len(texts)}']}

    r = AccountRunner('p', 1, 'h', 's', plan,
                      on_trace=lambda p, stage, detail: traces.append((stage, detail)),
                      pre_delay=lambda chat_id: pre_delay)
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
