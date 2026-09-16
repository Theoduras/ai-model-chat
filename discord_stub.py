"""A stand-in for Discord, for driving the adapter without a socket.

It replaces only the two things that need a network — the REST side and the
connection itself — and leaves the real dispatcher, the real gates and the real
caches in place. So a test that feeds it a message is testing the code that
decides whether to answer, not a re-description of it.

Used by test_discord.py. Not imported by the app.
"""
import itertools
import time

import discord_gateway as DG

_ids = itertools.count(1000)


def _stamp():
    return time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())


class FakeRest:
    """Records what would have gone out, and answers like Discord does."""

    def __init__(self, me='1', username='lilith'):
        self.me = str(me)
        self.username = username
        self.sent = []
        self.typing_calls = []
        self.reactions = []
        self.accepted = []
        self.history_rows = {}

    def configured(self):
        return True

    def held(self):
        return 0

    def send(self, channel_id, content, reply_to=''):
        row = {'id': str(next(_ids)), 'channel_id': str(channel_id),
               'content': content, 'timestamp': _stamp()}
        self.sent.append(row)
        return row

    def typing(self, channel_id):
        self.typing_calls.append(str(channel_id))
        return {}

    def react(self, channel_id, message_id, emoji):
        self.reactions.append((str(channel_id), str(message_id), emoji))
        return {}

    def history(self, channel_id, limit=20):
        return list(self.history_rows.get(str(channel_id), []))[-limit:]

    def accept_friend(self, user_id):
        self.accepted.append(str(user_id))
        return {}

    def texts(self):
        return [r['content'] for r in self.sent]


def install(persona, me='1', username='lilith', gates=None, **callbacks):
    """Put a connected-looking runner in the registry and return it.

    It is a real Runner — only its transport is fake — so every gate the live
    one applies is applied here too.
    """
    DG._chats.pop(persona, None)
    DG._msgs.pop(persona, None)
    runner = DG.Runner(persona, 'stub-token', **callbacks)
    runner.rest = FakeRest(me, username)
    runner.me_id = str(me)
    runner.username = username
    runner.session_id = 'stub-session'
    runner.connected_at = time.time()
    runner.configure(gates or {'allow': [], 'chime': {}, 'dm': {}})
    # alive() reads a thread that a stub never starts, so it is answered
    # directly rather than by pretending to run one.
    runner.alive = lambda: True
    DG._runners[persona] = runner
    return runner


def uninstall(persona):
    DG._runners.pop(persona, None)
    DG._chats.pop(persona, None)
    DG._msgs.pop(persona, None)


def dm(runner, text, fan='77', handle='dave', mid=None, channel='500',
       bot=False, mtype=0):
    """Deliver one direct message, through the real dispatcher."""
    raw = {'id': str(mid or next(_ids)), 'channel_id': str(channel),
           'content': text, 'timestamp': _stamp(), 'type': mtype,
           'author': {'id': str(fan), 'username': handle, 'bot': bot},
           'mentions': []}
    runner._on_message(raw)
    return raw


def channel(runner, text, fan='77', handle='dave', guild='9', chan='90',
            mention=False, reply_to_me=False, mid=None, bot=False):
    """Deliver one guild message. `mention` pings her; `reply_to_me` replies to
    something she said with the ping suppressed — both have to count."""
    raw = {'id': str(mid or next(_ids)), 'channel_id': str(chan),
           'guild_id': str(guild), 'content': text, 'timestamp': _stamp(),
           'type': 0, 'author': {'id': str(fan), 'username': handle, 'bot': bot},
           'mentions': [{'id': runner.me_id}] if mention else []}
    if reply_to_me:
        raw['referenced_message'] = {'author': {'id': runner.me_id}}
    runner._on_message(raw)
    return raw


def ready(runner, chats=()):
    """A READY carrying a DM list, the way a reconnect brings the cache back."""
    runner._on_ready({
        'user': {'id': runner.me_id, 'username': runner.username},
        'session_id': 'stub-session', 'resume_gateway_url': '',
        'private_channels': [
            {'id': str(c.get('channel', '500')), 'type': 1,
             'last_message_id': str(c.get('last', '1')),
             'recipients': [{'id': str(c.get('fan', '77')),
                             'username': c.get('handle', 'dave')}]}
            for c in chats],
    })
