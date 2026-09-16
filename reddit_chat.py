"""Reddit Chat gateway for a user account.

Reddit Chat is not part of Reddit's Data API and never has been: it is a
Sendbird deployment reddit.com proxies, and the only client that speaks it is
Reddit's own. So this module holds one long-lived Sendbird socket per persona
on its own thread, exactly the way discord_gateway.py holds a Discord one, and
exposes the same surface so the platform adapter in app.py reads one shape for
both.

Two things differ from Discord and both are Sendbird's doing:

  * The session token is short-lived -- about an hour -- and is minted from the
    Reddit session rather than stored. An expired token is not a refused
    account, so it is refreshed and reconnected rather than stopped.
  * Every frame is a four-character command followed by JSON, with no envelope
    and no sequence number, so there is no resume: a reconnect re-reads the
    channel list instead.

Group channels are dropped at the top of the dispatcher and never surface in
chats(). A room full of people is not a fan being worked towards something --
the same carve-out Discord's server channels get.

Nothing here touches the database or the persona layer. app.py binds it to a
platform adapter, the same way discord_gateway.py is bound.
"""
import asyncio
import json
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict, deque

import reddit_rest as RR

logger = logging.getLogger(__name__)

# Where the Sendbird session token is minted from the Reddit session, and where
# the socket and its REST side live. All three move between Reddit releases, so
# a capture from the sign-in browser overrides them and an env var overrides
# that -- a drift is a setting to change, not a deploy.
SB_TOKEN_URL = os.getenv('REDDIT_CHAT_TOKEN_URL',
                         'https://s.reddit.com/api/v1/sendbird/me')
SB_WS_HOST = os.getenv('REDDIT_CHAT_WS_HOST',
                       'wss://sendbirdproxyk8s.chat.redditmedia.com')
SB_API_HOST = os.getenv('REDDIT_CHAT_API_HOST',
                        'https://sendbirdproxyk8s.chat.redditmedia.com')
SB_APP_ID = os.getenv('REDDIT_CHAT_APP_ID', '2515BDA8-9D3A-47CF-9325-330BC37ADA13')
SB_SDK = os.getenv('REDDIT_CHAT_SDK_VERSION', '3.0.144')

CHAT_CACHE = 200          # DM channels held per persona, oldest evicted
MSG_CACHE = 40            # messages held per chat; the round asks for 20
OUT_FLOOR_SECONDS = 8     # never two outbound messages into one chat faster
DAILY_OUT_CAP = int(os.getenv('REDDIT_DAILY_CAP', '200'))
BACKOFF_MIN, BACKOFF_MAX = 2.0, 300.0
PING_SECONDS = 15
# Sendbird closes with 1000 and an error code in the body. These mean the
# credential is gone rather than stale, and retrying at one is both futile and
# the kind of thing that gets an account looked at.
FATAL_ERRORS = {400309, 400300, 400301, 400302}

_chats = {}      # persona -> OrderedDict[user_id] = chat dict
_msgs = {}       # persona -> {fan_id: deque}
_runners = {}    # persona -> Runner
_lock = threading.Lock()


def _now():
    return time.time()


def _chat_book(persona):
    return _chats.setdefault(persona, OrderedDict())


def _msg_book(persona, fan_id, cap=MSG_CACHE):
    book = _msgs.setdefault(persona, {})
    if fan_id not in book:
        book[fan_id] = deque(maxlen=cap)
    return book[fan_id]


def _frame(raw):
    """Sendbird's wire format: 'MESG{...}'. Anything that is not a command plus
    JSON is not ours to read."""
    if not raw or len(raw) < 4:
        return '', {}
    command, body = raw[:4], raw[4:]
    try:
        return command, json.loads(body) if body.strip() else {}
    except ValueError:
        return command, {}


def _norm(raw, me_id=''):
    """One message, in the shape the adapter reads. `out` is decided here from
    the sender rather than guessed later, because our own messages come back
    over the same socket as everyone else's."""
    user = raw.get('user') or {}
    sender = str(user.get('guest_id') or user.get('user_id') or raw.get('user_id') or '')
    stamp = raw.get('ts') or raw.get('created_at') or 0
    return {'id': str(raw.get('msg_id') or raw.get('message_id') or ''),
            'channel_url': str(raw.get('channel_url') or ''),
            'author_id': sender,
            'handle': user.get('nickname') or user.get('name') or '',
            'content': raw.get('message') or '',
            'ts': int(stamp or 0),
            'out': bool(sender) and sender == str(me_id)}


def _is_dm(channel):
    """A 1:1 chat. Sendbird counts the account itself among the members, so a
    DM has two -- anything larger is a room and never reaches the round."""
    count = channel.get('member_count')
    if count is None:
        count = len(channel.get('members') or [])
    return int(count or 0) <= 2


def _other_member(channel, me_id):
    for member in channel.get('members') or []:
        uid = str(member.get('user_id') or '')
        if uid and uid != str(me_id):
            return uid, member.get('nickname') or member.get('name') or ''
    return '', ''


class RedditChatError(RuntimeError):
    pass


def mint_token(session):
    """Ask Reddit for a Sendbird session, using the credentials the sign-in
    captured. Short-lived by design, so this is called again on reconnect
    rather than its answer being stored."""
    session = session or {}
    bearer = session.get('bearer') or ''
    if not bearer:
        raise RedditChatError(
            'Her Reddit sign-in did not capture a chat token, so chat cannot '
            'connect. Posting and comments still work; reconnect the account '
            'to pick chat up.')
    req = urllib.request.Request(SB_TOKEN_URL, method='GET')
    req.add_header('Authorization', f'Bearer {bearer}')
    req.add_header('User-Agent', session.get('user_agent') or RR.DEFAULT_UA)
    req.add_header('Cookie', session.get('cookie') or '')
    try:
        with RR._opener(session.get('proxy') or '').open(req, timeout=RR.REDDIT_TIMEOUT) as resp:
            got = json.loads(resp.read() or b'{}')
    except urllib.error.HTTPError as e:
        raise RedditChatError(f'Reddit refused a chat token ({e.code})')
    except urllib.error.URLError as e:
        raise RedditChatError(str(getattr(e, 'reason', e))[:160])
    token = got.get('sb_access_token') or got.get('access_token') or ''
    if not token:
        raise RedditChatError('Reddit returned no chat token')
    return token, str(got.get('user_id') or session.get('user_id') or '')


class Runner:
    """One account's socket. Owns its thread, its loop, and nothing else."""

    def __init__(self, persona, session, on_dm=None, on_typing=None, on_trace=None):
        self.persona = persona
        self.session = session or {}
        captured = self.session.get('chat') or {}
        # What her own browser reached chat with, when the sign-in saw it. A
        # constant we invented is a constant that is wrong after the next
        # release, and a client that looks nothing like the one that signed in
        # is what loses the account.
        self.app_id = captured.get('app_id') or SB_APP_ID
        self.ws_host = captured.get('ws_host') or SB_WS_HOST
        self.on_dm = on_dm or (lambda *a: None)
        self.on_typing = on_typing or (lambda *a: None)
        self.on_trace = on_trace or (lambda *a: None)
        self.gates = {'dm': {}}
        self.me_id = str(self.session.get('user_id') or '')
        self.username = self.session.get('username') or ''
        self.token = ''
        self.last_error = ''
        self.error_code = 0
        self.stopped = False
        self.connected = False
        self.connected_at = 0.0
        self._thread = None
        self._loop = None
        self._ws = None
        self._last_out = {}
        self._sent_today = 0
        self._day = ''
        self._seen = deque(maxlen=400)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def configure(self, gates):
        self.gates.update(gates or {})

    def alive(self):
        return bool(self._thread and self._thread.is_alive() and not self.stopped)

    def state(self):
        return {'connected': self.connected and self.alive(),
                'user_id': self.me_id, 'username': self.username,
                'close_code': self.error_code, 'error': self.last_error,
                'stopped': self.stopped, 'at': self.connected_at, 'held': 0}

    def start(self):
        if self.alive():
            return
        self.stopped = False
        self._thread = threading.Thread(
            target=self._thread_main, name=f'reddit:{self.persona}', daemon=True)
        self._thread.start()

    def stop(self):
        self.stopped = True
        loop, ws = self._loop, self._ws
        if loop and ws:
            asyncio.run_coroutine_threadsafe(ws.close(), loop)

    def _thread_main(self):
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._supervise())
        except Exception as e:
            self.last_error = str(e)[:200]
            logger.exception('reddit chat runner for %s died', self.persona)
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _supervise(self):
        backoff = BACKOFF_MIN
        while not self.stopped:
            try:
                await self._connect()
                backoff = BACKOFF_MIN
            except RedditChatError as e:
                self.last_error = str(e)[:200]
                self.stopped = True
                self.on_trace(self.persona, 'error', self.last_error)
                return
            except Exception as e:
                self.last_error = str(e)[:200]
                logger.warning('reddit chat %s disconnected: %s', self.persona,
                               self.last_error)
            finally:
                self.connected = False
            if self.error_code in FATAL_ERRORS:
                self.stopped = True
                self.on_trace(self.persona, 'error',
                              'Reddit refused her chat credentials, so chat is '
                              'stopped until the account is reconnected.')
                return
            if self.stopped:
                return
            await asyncio.sleep(backoff + random.uniform(0, backoff * 0.3))
            backoff = min(backoff * 2, BACKOFF_MAX)

    # ── The socket ───────────────────────────────────────────────────────────

    def _ws_url(self):
        """A fresh token every connect: it expires in about an hour, which is
        shorter than this socket is meant to live."""
        self.token, user_id = mint_token(self.session)
        if user_id:
            self.me_id = user_id
        params = {'p': 'Web', 'pv': '3', 'sv': SB_SDK, 'ai': self.app_id,
                  'user_id': self.me_id, 'access_token': self.token,
                  'active': '1'}
        return f'{self.ws_host}/?{urllib.parse.urlencode(params)}'

    async def _connect(self):
        import aiohttp
        url = self._ws_url()
        headers = {'User-Agent': self.session.get('user_agent') or RR.DEFAULT_UA}
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(url, heartbeat=None, max_msg_size=0,
                                       headers=headers) as ws:
                self._ws = ws
                self.error_code = 0
                ping = asyncio.ensure_future(self._ping(ws))
                try:
                    async for raw in ws:
                        if raw.type != aiohttp.WSMsgType.TEXT:
                            break
                        self._dispatch(*_frame(raw.data))
                finally:
                    ping.cancel()
                    self._ws = None

    async def _ping(self, ws):
        """Sendbird drops a socket that stops pinging, and a dropped socket
        that still looks open is the failure that leaves a persona silent."""
        while True:
            await asyncio.sleep(PING_SECONDS)
            try:
                await ws.send_str('PING' + json.dumps({'id': int(_now()), 'active': 1}))
            except Exception:
                return

    def _dispatch(self, command, data):
        if command == 'LOGI':
            return self._on_login(data)
        if command == 'MESG' or command == 'FILE':
            return self._on_message(data)
        if command == 'TPST':
            return self._on_typing(data)
        if command == 'EROR':
            self.error_code = int(data.get('code') or 0)
            self.last_error = str(data.get('message') or '')[:200]

    def _on_login(self, data):
        self.me_id = str(data.get('user_id') or self.me_id)
        self.username = data.get('nickname') or self.username
        self.connected = True
        self.connected_at = _now()
        self.on_trace(self.persona, 'idle', 'connected to Reddit chat')
        threading.Thread(target=self._load_chats, name=f'reddit-sync:{self.persona}',
                         daemon=True).start()

    def _on_typing(self, data):
        chat = _chat_book(self.persona).get(self._fan_of(data.get('channel_url') or ''))
        if chat:
            chat['typing_at'] = _now()
            self.on_typing(self.persona, chat['fan_id'])

    def _on_message(self, raw):
        """Every gate that can refuse a message lives here, cheapest first."""
        msg = _norm(raw, self.me_id)
        if not msg['id'] or msg['id'] in self._seen:
            return
        self._seen.append(msg['id'])
        if msg['out'] or not msg['content'].strip():
            return
        url = msg['channel_url']
        fan_id = self._fan_of(url)
        if not fan_id:
            # A channel we have never listed: fetch it once, and drop it here
            # if it turns out to be a room rather than a DM.
            fan_id = self._learn_channel(url)
            if not fan_id:
                return
        book = _chat_book(self.persona)
        chat = book.get(fan_id)
        if not chat:
            return
        chat['handle'] = msg['handle'] or chat.get('handle') or ''
        chat['last_at'] = _now()
        book.move_to_end(fan_id)
        while len(book) > CHAT_CACHE:
            book.popitem(last=False)
        _msg_book(self.persona, fan_id).append(msg)
        self.on_dm(self.persona, fan_id)

    def _fan_of(self, channel_url):
        for fan_id, chat in _chat_book(self.persona).items():
            if chat.get('channel_url') == channel_url:
                return fan_id
        return ''

    # ── Sendbird's REST side ─────────────────────────────────────────────────

    def _api(self, method, path, body=None):
        url = f'{SB_API_HOST.rstrip("/")}/v3{path}'
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method.upper())
        req.add_header('Session-Key', self.token)
        req.add_header('Content-Type', 'application/json')
        req.add_header('User-Agent', self.session.get('user_agent') or RR.DEFAULT_UA)
        try:
            with RR._opener(self.session.get('proxy') or '').open(
                    req, timeout=RR.REDDIT_TIMEOUT) as resp:
                return json.loads(resp.read() or b'{}')
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = (e.read() or b'')[:200].decode('utf-8', 'replace')
            except Exception:
                pass
            raise RedditChatError(f'Reddit chat {e.code}: {detail}')
        except urllib.error.URLError as e:
            raise RedditChatError(str(getattr(e, 'reason', e))[:160])

    def _load_chats(self):
        """The channel list, once per connect. There is no resume on this
        protocol, so this is how a reconnect catches up."""
        try:
            got = self._api('GET', f'/users/{self.me_id}/my_group_channels'
                                   '?limit=100&show_member=true&order=latest_last_message')
        except RedditChatError as e:
            self.last_error = str(e)[:200]
            return
        book = _chat_book(self.persona)
        for channel in got.get('channels') or []:
            if not _is_dm(channel):
                continue
            fan_id, handle = _other_member(channel, self.me_id)
            if not fan_id:
                continue
            chat = book.setdefault(fan_id, {'fan_id': fan_id, 'typing_at': 0.0})
            chat['channel_url'] = channel.get('channel_url') or ''
            chat['handle'] = handle or chat.get('handle') or ''
            last = channel.get('last_message') or {}
            chat['last_at'] = float(last.get('created_at') or 0) / 1000.0 or chat.get('last_at', 0.0)
        while len(book) > CHAT_CACHE:
            book.popitem(last=False)

    def _learn_channel(self, channel_url):
        if not channel_url:
            return ''
        try:
            channel = self._api('GET', f'/group_channels/{urllib.parse.quote(channel_url)}'
                                       '?show_member=true')
        except RedditChatError:
            return ''
        if not _is_dm(channel):
            return ''
        fan_id, handle = _other_member(channel, self.me_id)
        if not fan_id:
            return ''
        _chat_book(self.persona)[fan_id] = {
            'fan_id': fan_id, 'channel_url': channel_url, 'handle': handle,
            'last_at': _now(), 'typing_at': 0.0}
        return fan_id

    def history(self, fan_id, want=100):
        chat = _chat_book(self.persona).get(str(fan_id)) or {}
        url = chat.get('channel_url') or ''
        if not url:
            return []
        try:
            got = self._api(
                'GET', f'/group_channels/{urllib.parse.quote(url)}/messages'
                       f'?message_ts={int(_now() * 1000)}&prev_limit={int(want)}'
                       f'&next_limit=0&include=false')
        except RedditChatError:
            return []
        return [_norm(m, self.me_id) for m in got.get('messages') or []]

    # ── Sending ──────────────────────────────────────────────────────────────

    def _out_ok(self, fan_id):
        day = time.strftime('%Y-%m-%d')
        if day != self._day:
            self._day, self._sent_today = day, 0
        if self._sent_today >= DAILY_OUT_CAP:
            raise RedditChatError(
                f'Her Reddit account has already sent {DAILY_OUT_CAP} messages '
                'today, which is as far as this will push it.')
        since = _now() - self._last_out.get(fan_id, 0)
        if since < OUT_FLOOR_SECONDS:
            time.sleep(OUT_FLOOR_SECONDS - since)

    def send(self, fan_id, text):
        fan_id = str(fan_id)
        chat = _chat_book(self.persona).get(fan_id) or {}
        url = chat.get('channel_url') or ''
        if not url:
            raise RedditChatError('No Reddit chat is open with that fan')
        self._out_ok(fan_id)
        sent = self._api('POST', f'/group_channels/{urllib.parse.quote(url)}/messages',
                         {'message_type': 'MESG', 'user_id': self.me_id,
                          'message': text})
        self._last_out[fan_id] = _now()
        self._sent_today += 1
        msg = _norm(dict(sent, channel_url=url), self.me_id)
        msg['out'] = True
        _msg_book(self.persona, fan_id).append(msg)
        return sent or {}

    def typing(self, fan_id):
        chat = _chat_book(self.persona).get(str(fan_id)) or {}
        url = chat.get('channel_url') or ''
        loop, ws = self._loop, self._ws
        if not (url and loop and ws):
            return
        frame = 'TPST' + json.dumps({'channel_url': url, 'time': int(_now() * 1000)})
        asyncio.run_coroutine_threadsafe(ws.send_str(frame), loop)


# ── What the adapter calls ───────────────────────────────────────────────────


def runner(persona):
    with _lock:
        return _runners.get(persona)


def register(persona, session, **callbacks):
    """Return this persona's runner, making one only if the account changed.
    Two live sockets on one account is the loudest thing it can do, so an
    existing connection is reused rather than replaced."""
    session = session or {}
    with _lock:
        held = _runners.get(persona)
        if held and held.session.get('cookie') == session.get('cookie') and held.alive():
            return held
        if held:
            held.stop()
        made = Runner(persona, session, **callbacks)
        _runners[persona] = made
        return made


def configured(persona):
    return bool(runner(persona))


def me(persona):
    live = runner(persona)
    return live.me_id if live else ''


def chats(persona):
    """Newest conversations first, in the shape the round reads."""
    rows = list(_chat_book(persona).values())
    return sorted(rows, key=lambda c: c.get('last_at') or 0, reverse=True)


def messages(persona, fan_id, want=20):
    """Cached messages, oldest first. A cold cache for a chat we know about is
    filled from Reddit once, for that chat alone -- never for the whole list."""
    fan_id = str(fan_id)
    book = _msgs.get(persona, {}).get(fan_id)
    if not book:
        live = runner(persona)
        if live:
            book = _msg_book(persona, fan_id)
            for msg in live.history(fan_id, min(want, 50)):
                book.append(msg)
    return list(book or [])[-want:]


def send(persona, fan_id, text):
    live = runner(persona)
    if not live:
        raise RedditChatError('Reddit chat is not connected for this persona')
    return live.send(fan_id, text)


def typing(persona, fan_id):
    live = runner(persona)
    if live:
        live.typing(fan_id)


def history(persona, fan_id, want=200):
    """The whole conversation so far, for a fan we are meeting part-way."""
    live = runner(persona)
    return live.history(fan_id, min(want, 100)) if live else []


def text_of(msg):
    return (msg or {}).get('content') or ''


def msg_id(msg):
    return str((msg or {}).get('id') or '')


def msg_time(msg):
    """ISO, because that is what the shared round and the log expect; Sendbird
    counts in milliseconds."""
    ts = int((msg or {}).get('ts') or 0)
    if not ts:
        return ''
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts / 1000.0, timezone.utc).isoformat()


def msg_age_minutes(msg):
    ts = int((msg or {}).get('ts') or 0)
    if not ts:
        return 0.0
    return max(0.0, (_now() - ts / 1000.0) / 60.0)


def direction_of(msg, fan_id='', me_id='', recent_out=()):
    """Sendbird says outright who sent a message, so there is nothing to infer."""
    if (msg or {}).get('out'):
        return 'out'
    return 'in' if text_of(msg) else ''


def chat_online(chat, grace=0):
    """Reddit does not tell a client who is online, so an online filter would
    silently drop everyone. Typing counts as present; nothing else does."""
    if not grace:
        return True
    return (_now() - (chat or {}).get('typing_at', 0)) <= grace * 60


def user_of_chat(chat):
    chat = chat or {}
    return (str(chat.get('fan_id') or ''), chat.get('handle') or '',
            False, str(chat.get('channel_url') or ''))
