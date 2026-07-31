"""Telegram user-account client (MTProto).

The Bot API cannot do two things this platform needs: a bot is always labelled
as a bot, and it can only reply to people who pressed START first. A real user
account has neither limit — it looks like a person and can be written to
directly — so personas that need that run here instead.

This module is deliberately free of Flask and app imports: it takes credentials
and a callback, so `app.py` owns persona/config/storage and this owns MTProto.
Sessions are strings, handed back to the caller to persist wherever it likes.
"""

import asyncio
import threading

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError


def _client(api_id, api_hash, session_str=''):
    return TelegramClient(StringSession(session_str or None), int(api_id), api_hash)


def _run(coro):
    """Run one coroutine on a private loop — Flask request handlers are sync."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()


def send_code(api_id, api_hash, phone):
    """Ask Telegram to text a login code. Returns (session, phone_code_hash) —
    both must be kept, because signing in has to reuse the same session."""
    async def go():
        client = _client(api_id, api_hash)
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
            return client.session.save(), sent.phone_code_hash
        finally:
            await client.disconnect()
    return _run(go())


def sign_in(api_id, api_hash, session_str, phone, code, phone_code_hash, password=None):
    """Complete login. Returns (session, me_dict, needs_password).

    When the account has two-step verification, the first call comes back with
    needs_password=True and the caller re-invokes with the password.
    """
    async def go():
        client = _client(api_id, api_hash, session_str)
        await client.connect()
        try:
            try:
                if password:
                    await client.sign_in(password=password)
                else:
                    await client.sign_in(phone=phone, code=code,
                                         phone_code_hash=phone_code_hash)
            except SessionPasswordNeededError:
                return client.session.save(), None, True
            me = await client.get_me()
            return client.session.save(), {
                'user_id': me.id,
                'username': me.username or '',
                'first_name': me.first_name or '',
                'phone': me.phone or phone,
            }, False
        finally:
            await client.disconnect()
    return _run(go())


def whoami(api_id, api_hash, session_str):
    async def go():
        client = _client(api_id, api_hash, session_str)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                return None
            me = await client.get_me()
            return {'user_id': me.id, 'username': me.username or '',
                    'first_name': me.first_name or '', 'phone': me.phone or ''}
        finally:
            await client.disconnect()
    return _run(go())


def send_message(api_id, api_hash, session_str, peer, text):
    """Message someone directly — no prior contact needed, unlike a bot."""
    async def go():
        client = _client(api_id, api_hash, session_str)
        await client.connect()
        try:
            await client.send_message(peer, text)
            return True
        finally:
            await client.disconnect()
    return _run(go())


class AccountRunner:
    """Holds one persona's account online and answers incoming DMs.

    `plan(chat_id, name, text)` is supplied by the caller and returns
    {'read': seconds, 'cps': chars_per_second, 'chunks': [str, ...]}, or None to
    stay silent. Timing lives here so the typing indicator is genuinely held
    while the reply is "being written".
    """

    def __init__(self, persona, api_id, api_hash, session_str, plan, on_sent=None,
                 on_error=None):
        self.persona = persona
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_str = session_str
        self.plan = plan
        self.on_sent = on_sent
        self.on_error = on_error
        self._thread = None
        self._loop = None
        self._stop = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def alive(self):
        return bool(self._thread and self._thread.is_alive())

    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as e:
            if self.on_error:
                self.on_error(self.persona, str(e)[:300])
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _main(self):
        client = _client(self.api_id, self.api_hash, self.session_str)

        @client.on(events.NewMessage(incoming=True))
        async def handler(event):
            try:
                await self._handle(client, event)
            except Exception as e:
                if self.on_error:
                    self.on_error(self.persona, str(e)[:300])

        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError('Session is no longer authorized — sign in again.')
        await client.run_until_disconnected()

    async def _handle(self, client, event):
        if not event.is_private or event.out:
            return
        text = (event.raw_text or '').strip()
        if not text:
            return
        sender = await event.get_sender()
        if getattr(sender, 'bot', False):
            return
        chat_id = event.chat_id
        name = (getattr(sender, 'username', '') or
                getattr(sender, 'first_name', '') or str(chat_id))

        # Gemini is blocking, so keep it off the event loop.
        plan = await asyncio.to_thread(self.plan, chat_id, name, text)
        if not plan or not plan.get('chunks'):
            return

        initial = max(0.0, float(plan.get('initial_delay', 0)))
        if initial > 0:
            await asyncio.sleep(initial)
        await asyncio.sleep(max(0.0, float(plan.get('read', 0))))
        cps = max(4, int(plan.get('cps', 14)))
        for i, chunk in enumerate(plan['chunks']):
            if not chunk:
                continue
            if i:
                await asyncio.sleep(1.0)
            dur = min(max(len(chunk) / float(cps), 1.2), 11.0)
            async with client.action(event.chat_id, 'typing'):
                await asyncio.sleep(dur)
            await client.send_message(event.chat_id, chunk)
            if self.on_sent:
                self.on_sent(self.persona, chat_id, name, chunk)

        photo_data = plan.get('photo_data')
        if photo_data:
            import base64, io
            raw = photo_data
            if ',' in raw:
                raw = raw.split(',', 1)[1]
            buf = io.BytesIO(base64.b64decode(raw))
            buf.name = 'photo.jpg'
            await client.send_file(event.chat_id, buf)
