"""Manufacturing the OnlyFans events OnlyFans does not send.

OnlyFans has no outbound webhooks, so a watcher per account produces them: it
follows the chat list and turns what changed into exactly the envelope
`/webhooks/onlyfans` already accepts, signed with our own secret. The reply
engine cannot tell this apart from a delivery by the middleman, which is what
keeps the swap one module deep.

The watcher only ever reads the chat *list*. Opening a chat marks it read on
OnlyFans, and a fan seeing read receipts from a creator who never answers is
worse than a slow reply — so the list, which already carries each chat's last
message, is the only thing polled.
"""
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.request
import uuid
from urllib import error as url_error

import of_client
import of_session

logger = logging.getLogger(__name__)

# How often an account is looked at. A chat that just moved is worth watching
# closely; an account nobody is talking to is not worth the requests.
POLL_ACTIVE = float(os.getenv('ONLYFANS_POLL_ACTIVE', '5') or 5)
POLL_IDLE = float(os.getenv('ONLYFANS_POLL_IDLE', '60') or 60)
ACTIVE_WINDOW = 300
CHAT_SCAN = int(os.getenv('ONLYFANS_WATCH_CHATS', '40') or 40)
EMIT_TIMEOUT = 15
# What a fresh watcher does with the backlog: chats whose last message is older
# than this are recorded as seen rather than replied to, so connecting an
# account does not fire off answers to weeks-old conversations.
BACKLOG_MINUTES = 30

_sink = None
_watchers = {}
_lock = threading.Lock()


def sink(fn):
    """Deliver events by calling `fn(event, account, payload, idem)` instead of
    posting them. Used when the watcher runs inside the web app itself."""
    global _sink
    _sink = fn


def webhook_url():
    return (os.getenv('ONLYFANS_INTERNAL_WEBHOOK_URL') or '').strip()


def secret():
    return (os.getenv('ONLYFANS_WEBHOOK_SECRET') or '').strip()


def emit(event, account, payload, idem=''):
    """Deliver one event. Posting it is the real path — the sink exists so a
    single-process deployment can skip the round trip."""
    idem = idem or uuid.uuid4().hex
    if _sink:
        return _sink(event, account, payload, idem)
    url = webhook_url()
    if not url:
        logger.warning('OnlyFans event %s dropped: no internal webhook URL', event)
        return False
    body = json.dumps({'event': event, 'account_id': account,
                       'payload': payload}).encode()
    signature = hmac.new(secret().encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=body, method='POST', headers={
        'Content-Type': 'application/json', 'Signature': signature,
        'X-OFAPI-Idempotency-Key': idem})
    try:
        with urllib.request.urlopen(req, timeout=EMIT_TIMEOUT) as r:
            return 200 <= r.status < 300
    except url_error.HTTPError as e:
        logger.warning('OnlyFans event %s rejected: %s %s', event, e.code,
                       e.read()[:120])
    except url_error.URLError as e:
        logger.warning('OnlyFans event %s undelivered: %s', event, str(e)[:120])
    return False


def _last_message(chat):
    m = chat.get('lastMessage') if isinstance(chat, dict) else None
    return m if isinstance(m, dict) else {}


def _payload(chat, message, fan_id, handle):
    return {'id': of_client.msg_id(message),
            'text': message.get('text') or '',
            'user_id': fan_id,
            'fromUser': {'id': fan_id, 'username': handle},
            'price': message.get('price'),
            'isOpened': message.get('isOpened'),
            'createdAt': of_client.msg_time(message)}


class Watcher:
    """One account, watched. Owns no browser and no session — it asks the
    transport, which asks the vault."""

    def __init__(self, account):
        self.account = account
        self.seen = {}
        self.primed = False
        self.last_activity = 0.0
        self.last_error = ''
        self.polls = 0
        self.events = 0
        self.stopped = threading.Event()
        self._thread = None

    def interval(self):
        return POLL_ACTIVE if time.time() - self.last_activity < ACTIVE_WINDOW \
            else POLL_IDLE

    def state(self):
        return {'account': self.account, 'running': bool(
            self._thread and self._thread.is_alive()),
            'interval': self.interval(), 'polls': self.polls,
            'events': self.events, 'last_error': self.last_error,
            'chats_tracked': len(self.seen)}

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self.stopped.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f'of-watch-{self.account}')
        self._thread.start()
        return self

    def stop(self):
        self.stopped.set()

    def _run(self):
        while not self.stopped.is_set():
            try:
                self.poll()
            except of_client.SessionExpired as e:
                self.last_error = str(e)
                logger.warning('watcher for %s stopping: %s', self.account, e)
                return
            except Exception as e:
                self.last_error = str(e)[:200]
                logger.warning('watcher for %s: %s', self.account, str(e)[:200])
            self.stopped.wait(self.interval())

    def poll(self):
        """One pass over the chat list. Returns the events it produced."""
        if not of_session.live(self.account):
            raise of_client.SessionExpired(401, 'session is not live')
        chats = of_client.chats(self.account, want=CHAT_SCAN)
        self.polls += 1
        self.last_error = ''
        produced = []
        for chat in chats:
            produced += self._chat(chat)
        self.primed = True
        self.events += len(produced)
        return produced

    def _chat(self, chat):
        fan_id, handle, _, _ = of_client.user_of_chat(chat)
        message = _last_message(chat)
        mid = of_client.msg_id(message)
        if not (fan_id and mid):
            return []
        was = self.seen.get(fan_id)
        self.seen[fan_id] = mid
        if mid == was:
            return self._typing(chat, fan_id)

        # First pass after a connect: remember where every chat stands rather
        # than replying to a backlog the creator has already read.
        age = of_client.msg_age_minutes(message)
        if not self.primed and (age is None or age > BACKLOG_MINUTES):
            return []

        self.last_activity = time.time()
        payload = _payload(chat, message, fan_id, handle)
        # An unlock is a paid message of ours that has been opened, so it looks
        # outbound too — it has to be recognised before direction decides.
        if message.get('price') and message.get('isOpened'):
            emit('messages.ppv.unlocked', self.account, payload, f'{mid}:ppv')
            return ['messages.ppv.unlocked']
        if of_client.direction_of(message, fan_id) == 'out':
            # The creator answered in the OnlyFans app. The persona has to know,
            # or it replies as though the fan was ignored.
            emit('messages.sent', self.account, payload, f'{mid}:sent')
            return ['messages.sent']
        emit('messages.received', self.account, payload, f'{mid}:in')
        return ['messages.received']

    def _typing(self, chat, fan_id):
        """A fan who is writing holds the reply back rather than starting one."""
        if not (isinstance(chat, dict) and chat.get('isTyping')):
            return []
        self.last_activity = time.time()
        emit('users.typing', self.account, {'user_id': fan_id})
        return ['users.typing']


# ── The registry ──────────────────────────────────────────────────────────────

def watch(account):
    with _lock:
        w = _watchers.get(account) or Watcher(account)
        _watchers[account] = w
    return w.start()


def unwatch(account):
    with _lock:
        w = _watchers.pop(account, None)
    if w:
        w.stop()


def watching():
    with _lock:
        return [w.state() for w in _watchers.values()]


def reconcile(accounts):
    """Watch exactly the accounts given: start what is new, drop what is gone.

    Called on a timer as well as on connect, because a Cloud Run instance that
    restarts comes back with an empty registry and a database full of accounts.
    """
    accounts = {a for a in accounts if a}
    with _lock:
        current = set(_watchers)
        # A watcher whose thread has died stays in the registry, so membership
        # alone would leave it dead for ever. start() no-ops on a live thread,
        # which makes restarting the stopped ones just as cheap as skipping.
        stopped = {a for a, w in _watchers.items() if not w.state()['running']}
    for account in (accounts - current) | (accounts & stopped):
        watch(account)
    for account in current - accounts:
        unwatch(account)
    return sorted(accounts)
