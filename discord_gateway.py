"""Discord gateway for a user account.

Discord gives a user account no webhook and no polling API worth the name: the
socket pushes everything the account can see, with no server-side filter. So
this module holds one long-lived connection per persona on its own thread, and
the gates that decide what is worth answering live at the top of the dispatcher
rather than anywhere downstream. A message that fails one of them never reaches
the round, never reaches Gemini, and costs nothing.

It caches what the reply engine needs to read — recent chats and recent
messages — because asking the REST API for a chat list on every round is both
slow and exactly the traffic pattern that gets an account looked at. The cache
is memory only and Cloud Run wipes it on restart; that is safe, because the
durable record is x_messages and the reply cursor in the database, so a cold
start can at worst re-read, never re-reply.

Nothing here touches the database or the persona layer. app.py binds it to a
platform adapter, the same way onlyfans.py is bound.
"""
import asyncio
import json
import logging
import os
import random
import threading
import time
from collections import OrderedDict, deque

import discord_rest as DR

logger = logging.getLogger(__name__)

GATEWAY_URL = os.getenv('DISCORD_GATEWAY', 'wss://gateway.discord.gg/?v=10&encoding=json')
# A user connection is push-all. Everything outside this set is dropped on the
# first line of the dispatcher.
KEEP = {'READY', 'READY_SUPPLEMENTAL', 'RESUMED', 'MESSAGE_CREATE',
        'CHANNEL_CREATE', 'RELATIONSHIP_ADD', 'TYPING_START'}
CHAT_CACHE = 200          # DM chats held per persona, oldest evicted
MSG_CACHE = 40            # messages held per DM; the round asks for 20
GUILD_MSG_CACHE = 10      # only messages that passed the mention gate
OUT_FLOOR_SECONDS = 8     # never two outbound messages into one channel faster
DAILY_OUT_CAP = int(os.getenv('DISCORD_DAILY_CAP', '200'))
CATCHUP_AFTER = 6         # seconds after READY before looking for a backlog
CATCHUP_CHATS = 20
BACKOFF_MIN, BACKOFF_MAX = 2.0, 300.0
# Closes that mean the token is gone. Reconnecting at one of these is both
# futile and the kind of thing that gets an account flagged, so it stops.
FATAL_CLOSES = {4004, 4010, 4011, 4012, 4013, 4014}

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


def _norm(raw, me_id=''):
    """One message, in the shape the adapter reads. `out` is decided here from
    the author rather than guessed later, because our own echo comes back over
    the same socket as everything else."""
    author = raw.get('author') or {}
    return {'id': str(raw.get('id') or ''),
            'channel_id': str(raw.get('channel_id') or ''),
            'guild_id': str(raw.get('guild_id') or ''),
            'author_id': str(author.get('id') or ''),
            'handle': author.get('global_name') or author.get('username') or '',
            'content': raw.get('content') or '',
            'timestamp': raw.get('timestamp') or '',
            'out': str(author.get('id') or '') == str(me_id)}


def _is_dm(raw):
    """A DM has no guild_id key at all — not an empty one."""
    return not raw.get('guild_id')


def _mentions_me(raw, me_id):
    """Answer when addressed, and only then.

    Both halves are needed: a reply with the ping suppressed leaves `mentions`
    empty, and an @everyone populates nothing that should count as being asked
    a question.
    """
    if not me_id:
        return False
    for m in raw.get('mentions') or []:
        if str(m.get('id')) == str(me_id):
            return True
    ref = raw.get('referenced_message') or {}
    return str((ref.get('author') or {}).get('id') or '') == str(me_id)


class Runner:
    """One account's socket. Owns its thread, its loop, and nothing else."""

    def __init__(self, persona, token, props=None, on_dm=None, on_channel=None,
                 on_typing=None, on_trace=None, on_accept=None):
        self.persona = persona
        self.token = token or ''
        self.props = props or DR.properties()
        self.rest = DR.Rest(self.token, self.props)
        self.on_dm = on_dm or (lambda *a: None)
        self.on_channel = on_channel or (lambda *a: None)
        self.on_typing = on_typing or (lambda *a: None)
        self.on_trace = on_trace or (lambda *a: None)
        self.on_accept = on_accept or (lambda *a: None)
        self.gates = {'allow': [], 'chime': {}, 'dm': {}, 'accept_route': ''}
        self.me_id = ''
        self.username = ''
        self.session_id = ''
        self.resume_url = ''
        self.seq = None
        self.close_code = 0
        self.last_error = ''
        self.stopped = False
        self.connected_at = 0.0
        self._thread = None
        self._loop = None
        self._ws = None
        self._acked = True
        self._chime_last = {}
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
        return {'connected': bool(self.session_id) and self.alive(),
                'user_id': self.me_id, 'username': self.username,
                'close_code': self.close_code, 'error': self.last_error,
                'stopped': self.stopped, 'at': self.connected_at,
                'held': self.rest.held()}

    def start(self):
        if self.alive():
            return
        self.stopped = False
        self._thread = threading.Thread(
            target=self._thread_main, name=f'discord:{self.persona}', daemon=True)
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
            logger.exception('discord runner for %s died', self.persona)
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
            except Exception as e:
                self.last_error = str(e)[:200]
                logger.warning('discord %s disconnected: %s', self.persona, self.last_error)
            if self.close_code in FATAL_CLOSES:
                self.stopped = True
                self.on_trace(self.persona, 'error',
                              f'Discord refused her token (close {self.close_code}) — '
                              'the account has to be reconnected.')
                return
            if self.stopped:
                return
            await asyncio.sleep(backoff + random.uniform(0, backoff * 0.3))
            backoff = min(backoff * 2, BACKOFF_MAX)

    # ── The socket ───────────────────────────────────────────────────────────

    async def _connect(self):
        import aiohttp
        url = self.resume_url or GATEWAY_URL
        if self.resume_url and '?' not in url:
            url = f'{url}/?v=10&encoding=json'
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                    url, heartbeat=None, max_msg_size=0,
                    headers={'User-Agent': self.props.get('browser_user_agent')}) as ws:
                self._ws = ws
                self.close_code = 0
                hello = await ws.receive_json()
                interval = (hello.get('d') or {}).get('heartbeat_interval', 41250) / 1000.0
                beat = asyncio.ensure_future(self._heartbeat(ws, interval))
                try:
                    if self.session_id and self.seq is not None:
                        await ws.send_json({'op': 6, 'd': {
                            'token': self.token, 'session_id': self.session_id,
                            'seq': self.seq}})
                    else:
                        await ws.send_json(self._identify())
                    async for raw in ws:
                        if raw.type != aiohttp.WSMsgType.TEXT:
                            break
                        await self._dispatch(ws, json.loads(raw.data))
                finally:
                    beat.cancel()
                    self._ws = None
                    self.close_code = ws.close_code or 0

    def _identify(self):
        """A user IDENTIFY, not a bot one: no intents field, a bare token, and
        the client description the REST calls will repeat."""
        return {'op': 2, 'd': {
            'token': self.token,
            'capabilities': DR.DEFAULT_CAPABILITIES,
            'properties': self.props,
            'presence': {'status': 'online', 'since': 0, 'activities': [], 'afk': False},
            'compress': False,
            'client_state': {'guild_versions': {}, 'highest_last_message_id': '0',
                             'read_state_version': 0, 'user_guild_settings_version': -1,
                             'user_settings_version': -1, 'private_channels_version': '0',
                             'api_code_version': 0},
        }}

    async def _heartbeat(self, ws, interval):
        """A socket that stops being ACKed is dead while still looking open —
        which is the failure that leaves a persona silent for hours."""
        await asyncio.sleep(interval * random.random())
        while True:
            if not self._acked:
                await ws.close(code=4000)
                return
            self._acked = False
            await ws.send_json({'op': 1, 'd': self.seq})
            await asyncio.sleep(interval)

    async def _dispatch(self, ws, payload):
        op = payload.get('op')
        if op == 11:
            self._acked = True
            return
        if op == 1:
            await ws.send_json({'op': 1, 'd': self.seq})
            return
        if op == 7:
            await ws.close(code=4000)
            return
        if op == 9:
            self.session_id, self.resume_url, self.seq = '', '', None
            await asyncio.sleep(1 + random.random() * 4)
            await ws.send_json(self._identify())
            return
        if op != 0:
            return
        if payload.get('s') is not None:
            self.seq = payload['s']
        event = payload.get('t') or ''
        if event not in KEEP:
            return
        data = payload.get('d') or {}
        try:
            if event == 'READY':
                self._on_ready(data)
            elif event == 'RESUMED':
                self.on_trace(self.persona, 'idle', 'reconnected to Discord')
            elif event == 'MESSAGE_CREATE':
                self._on_message(data)
            elif event == 'CHANNEL_CREATE':
                self._on_channel_create(data)
            elif event == 'RELATIONSHIP_ADD':
                self._on_relationship(data)
            elif event == 'TYPING_START':
                self._on_typing_start(data)
        except Exception:
            logger.exception('discord %s failed on %s', self.persona, event)

    # ── Events ───────────────────────────────────────────────────────────────

    def _on_ready(self, data):
        user = data.get('user') or {}
        self.me_id = str(user.get('id') or '')
        self.username = user.get('username') or ''
        self.session_id = str(data.get('session_id') or '')
        self.resume_url = str(data.get('resume_gateway_url') or '')
        self.connected_at = _now()
        self.last_error = ''
        # READY already carries the DM list, so the chat cache comes back after
        # a restart without a single REST call.
        book = _chat_book(self.persona)
        for channel in data.get('private_channels') or []:
            if int(channel.get('type') or 0) != 1:
                continue
            for who in channel.get('recipients') or []:
                book[str(who.get('id'))] = {
                    'fan_id': str(who.get('id')),
                    'handle': who.get('global_name') or who.get('username') or '',
                    'channel_id': str(channel.get('id')),
                    'last_at': 0.0, 'typing_at': 0.0,
                    'last_message_id': str(channel.get('last_message_id') or '')}
        self.on_trace(self.persona, 'idle',
                      f'connected to Discord as {self.username} '
                      f'({len(book)} conversations)')
        if self._loop:
            asyncio.ensure_future(self._catch_up(), loop=self._loop)

    def _on_channel_create(self, data):
        if int(data.get('type') or 0) != 1:
            return
        for who in data.get('recipients') or []:
            _chat_book(self.persona)[str(who.get('id'))] = {
                'fan_id': str(who.get('id')),
                'handle': who.get('global_name') or who.get('username') or '',
                'channel_id': str(data.get('id')),
                'last_at': _now(), 'typing_at': 0.0, 'last_message_id': ''}

    def _on_relationship(self, data):
        if int(data.get('type') or 0) != 3:
            return
        if not (self.gates.get('dm') or {}).get('auto_accept_friends'):
            return
        user = (data.get('user') or {}).get('id') or data.get('id')
        try:
            self.rest.accept_friend(user)
        except Exception as e:
            logger.debug('discord friend accept failed: %s', str(e)[:120])

    def _on_typing_start(self, data):
        if data.get('guild_id'):
            return
        user = str(data.get('user_id') or '')
        chat = _chat_book(self.persona).get(user)
        if chat:
            chat['typing_at'] = _now()
        self.on_typing(self.persona, user)

    def _on_message(self, raw):
        """Every gate that can refuse a message lives here, cheapest first."""
        author = raw.get('author') or {}
        mid = str(raw.get('id') or '')
        if not mid or mid in self._seen:
            return
        self._seen.append(mid)
        me = self.me_id
        author_id = str(author.get('id') or '')
        if author_id == me:
            return
        if int(raw.get('type') or 0) not in (0, 19):
            return
        if _is_dm(raw):
            return self._dm(raw, author, author_id)
        self._guild(raw, author, author_id)

    def _dm(self, raw, author, author_id):
        dm = self.gates.get('dm') or {}
        if dm.get('ignore_bots', True) and author.get('bot'):
            return
        if not (raw.get('content') or '').strip():
            return
        channel_id = str(raw.get('channel_id') or '')
        chat = _chat_book(self.persona).setdefault(author_id, {
            'fan_id': author_id, 'channel_id': channel_id,
            'handle': '', 'last_at': 0.0, 'typing_at': 0.0})
        chat['channel_id'] = channel_id
        chat['handle'] = (author.get('global_name') or author.get('username')
                          or chat.get('handle') or '')
        chat['last_at'] = _now()
        book = _chat_book(self.persona)
        book.move_to_end(author_id)
        while len(book) > CHAT_CACHE:
            book.popitem(last=False)
        _msg_book(self.persona, author_id).append(_norm(raw, self.me_id))
        if dm.get('auto_accept', True):
            self.on_accept(self.persona, channel_id, author_id)
        self.on_dm(self.persona, author_id)

    def _guild(self, raw, author, author_id):
        guild_id = str(raw.get('guild_id') or '')
        channel_id = str(raw.get('channel_id') or '')
        allowed = self.allowed_channel(guild_id, channel_id)
        if allowed is None:
            return
        if author.get('bot') or author.get('system'):
            return
        text = (raw.get('content') or '').strip()
        if not text:
            return
        addressed = _mentions_me(raw, self.me_id)
        if not addressed and not self._chime_ok(channel_id, text):
            return
        key = f'{guild_id}:{channel_id}'
        _msg_book(self.persona, key, GUILD_MSG_CACHE).append(_norm(raw, self.me_id))
        self.on_channel(self.persona, guild_id, channel_id, addressed)

    # ── Gates ────────────────────────────────────────────────────────────────

    def allowed_channel(self, guild_id, channel_id):
        """The allowlist entry for this channel, or None. Empty list means she
        says nothing in public anywhere — a missing allowlist should be silence,
        not free rein."""
        for entry in self.gates.get('allow') or []:
            if (str(entry.get('guild')) == str(guild_id)
                    and str(entry.get('channel')) == str(channel_id)):
                return entry
        return None

    def _chime_ok(self, channel_id, text):
        """Whether an unprompted line is allowed here. This is the only place
        she speaks without being spoken to, so it is capped twice: loosely here
        to keep the socket cheap, and authoritatively in the round where the
        count survives a restart."""
        chime = self.gates.get('chime') or {}
        if not chime.get('enabled'):
            return False
        words = [w.lower() for w in (chime.get('keywords') or []) if w]
        if words and not any(w in text.lower() for w in words):
            return False
        cooldown = max(1, int(chime.get('cooldown_min') or 45)) * 60
        if _now() - self._chime_last.get(channel_id, 0) < cooldown:
            return False
        self._chime_last[channel_id] = _now()
        return True

    def _out_ok(self, channel_id):
        day = time.strftime('%Y-%m-%d')
        if day != self._day:
            self._day, self._sent_today = day, 0
        if self._sent_today >= DAILY_OUT_CAP:
            return False
        wait = OUT_FLOOR_SECONDS - (_now() - self._last_out.get(channel_id, 0))
        if wait > 0:
            time.sleep(min(wait, OUT_FLOOR_SECONDS))
        return True

    # ── Catch-up ─────────────────────────────────────────────────────────────

    async def _catch_up(self):
        """A restart comes back with an empty cache and a database full of
        conversations. Anything that arrived while we were gone is still sitting
        in the DM list, so the newest few are re-read and woken once."""
        await asyncio.sleep(CATCHUP_AFTER)
        book = list(_chat_book(self.persona).values())[-CATCHUP_CHATS:]
        for chat in book:
            if not chat.get('last_message_id'):
                continue
            try:
                self.on_dm(self.persona, chat['fan_id'])
            except Exception:
                pass

    # ── Sending ──────────────────────────────────────────────────────────────

    def send(self, fan_id, text, guild=False):
        channel_id = self.channel_for(fan_id, guild)
        if not channel_id:
            raise DR.DiscordApiError(0, 'No Discord channel for that conversation')
        if not self._out_ok(channel_id):
            raise DR.DiscordApiError(0, 'Daily Discord message cap reached')
        sent = self.rest.send(channel_id, text)
        self._last_out[channel_id] = _now()
        self._sent_today += 1
        # Our own line goes into the cache now rather than when the socket
        # echoes it back, so the next round can already see that she answered.
        cap = GUILD_MSG_CACHE if guild else MSG_CACHE
        _msg_book(self.persona, fan_id, cap).append({
            'id': str((sent or {}).get('id') or ''), 'channel_id': channel_id,
            'author_id': self.me_id, 'handle': self.username,
            'content': text, 'timestamp': (sent or {}).get('timestamp') or '',
            'out': True})
        return sent or {}

    def channel_for(self, fan_id, guild=False):
        if guild:
            return str(fan_id).split(':')[-1]
        chat = _chat_book(self.persona).get(str(fan_id))
        return (chat or {}).get('channel_id') or ''


# ── What the adapter calls ───────────────────────────────────────────────────


def runner(persona):
    with _lock:
        return _runners.get(persona)


def register(persona, token, props=None, **callbacks):
    """Return this persona's runner, making one only if the token changed. Two
    live sockets on one user token is the loudest thing an account can do, so
    an existing connection is reused rather than replaced."""
    with _lock:
        held = _runners.get(persona)
        if held and held.token == token and held.alive():
            return held
        if held:
            held.stop()
        made = Runner(persona, token, props, **callbacks)
        _runners[persona] = made
        return made


def configured(persona):
    return bool(runner(persona))


def me(persona):
    return (runner(persona) or Runner(persona, '')).me_id


def chats(persona):
    """Newest conversations first, in the shape the round reads."""
    rows = list(_chat_book(persona).values())
    return sorted(rows, key=lambda c: c.get('last_at') or 0, reverse=True)


def messages(persona, fan_id, want=20):
    """Cached messages, oldest first. A cold cache for a chat we know about is
    filled from Discord once, for that chat alone — never for the whole list."""
    fan_id = str(fan_id)
    book = _msgs.get(persona, {}).get(fan_id)
    if not book:
        live = runner(persona)
        chat = _chat_book(persona).get(fan_id)
        if live and chat and chat.get('channel_id'):
            try:
                rows = live.rest.history(chat['channel_id'], min(want, 20))
            except Exception:
                rows = []
            book = _msg_book(persona, fan_id)
            for raw in rows:
                book.append(_norm(raw, live.me_id))
    return list(book or [])[-want:]


def send(persona, fan_id, text, guild=False):
    live = runner(persona)
    if not live:
        raise DR.DiscordApiError(0, 'Discord is not connected for this persona')
    return live.send(fan_id, text, guild=guild)


def typing(persona, fan_id, guild=False):
    live = runner(persona)
    if not live:
        return
    channel_id = live.channel_for(fan_id, guild)
    if channel_id:
        live.rest.typing(channel_id)


def react(persona, fan_id, message_id, emoji, guild=False):
    live = runner(persona)
    if not (live and message_id):
        return
    channel_id = live.channel_for(fan_id, guild)
    if channel_id:
        live.rest.react(channel_id, message_id, emoji)


def history(persona, fan_id, want=200):
    """The whole conversation so far, for a fan we are meeting for the first
    time. Guild channels never import: a public backlog is not her history."""
    if ':' in str(fan_id):
        return []
    live = runner(persona)
    chat = _chat_book(persona).get(str(fan_id))
    if not (live and chat and chat.get('channel_id')):
        return []
    try:
        rows = live.rest.history(chat['channel_id'], min(want, 100))
    except Exception:
        return []
    return [_norm(r, live.me_id) for r in rows]


def text_of(msg):
    return (msg or {}).get('content') or ''


def msg_id(msg):
    return str((msg or {}).get('id') or '')


def msg_time(msg):
    return (msg or {}).get('timestamp') or ''


def msg_age_minutes(msg):
    stamp = msg_time(msg)
    if not stamp:
        return 0.0
    try:
        from datetime import datetime, timezone
        when = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        return max(0.0, (datetime.now(timezone.utc) - when).total_seconds() / 60.0)
    except ValueError:
        return 0.0


def direction_of(msg, fan_id='', me_id='', recent_out=()):
    """Discord says outright who sent a message, so there is nothing to infer."""
    if (msg or {}).get('out'):
        return 'out'
    return 'in' if text_of(msg) else ''


def chat_online(chat, grace=0):
    """Discord does not tell a user account who is online, so an online filter
    would silently drop everyone. Typing counts as present; nothing else does."""
    if not grace:
        return True
    return (_now() - (chat or {}).get('typing_at', 0)) <= grace * 60


def user_of_chat(chat):
    chat = chat or {}
    return (str(chat.get('fan_id') or ''), chat.get('handle') or '',
            False, str(chat.get('channel_id') or ''))
