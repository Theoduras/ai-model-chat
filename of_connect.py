"""Connecting an OnlyFans account: the hosted browser.

The creator has to sign in to OnlyFans for us, and there is no honest way to do
that on their behalf — Cloudflare's challenge and OnlyFans' 2FA are there to be
answered by a person. So we run a real Chromium here, on the exit IP the account
will keep, and stream it into the dashboard: they see onlyfans.com and type into
it, their password goes to OnlyFans and never to us, and what we keep at the end
is only the session their browser was issued.

Playwright's sync API belongs to the thread that created it, so each attempt
owns a thread and is spoken to through a queue. The dashboard polls frames over
plain HTTP rather than a websocket, because Flask serves this app and a JPEG
every 250ms is enough to type a password into.
"""
import base64
import logging
import os
import queue
import threading
import time
import uuid

import of_session

logger = logging.getLogger(__name__)

BROWSER_PATH = (os.getenv('PLAYWRIGHT_CHROMIUM') or '').strip()
SIGNIN_URL = 'https://onlyfans.com/'
COOKIE_ORIGIN = 'https://onlyfans.com'
VIEWPORT = {'width': 900, 'height': 700}
FRAME_QUALITY = 55
# How long a half-finished sign-in is kept alive. Long enough to find a phone
# and read a code out of it, short enough that an abandoned tab does not hold a
# browser and an IP for the rest of the day.
ATTEMPT_TTL = 15 * 60
IDLE_TTL = 3 * 60
POLL_SECONDS = 1.5

_attempts = {}
_lock = threading.Lock()


class ConnectError(RuntimeError):
    pass


def available():
    """Whether this host can run the hosted browser at all."""
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


class Attempt:
    """One creator, one sign-in, one browser."""

    def __init__(self, persona, account, proxy='', user_agent='', viewport=None):
        self.id = 'ofc_' + uuid.uuid4().hex[:16]
        self.persona = persona
        self.account = account
        self.proxy = proxy
        self.user_agent = user_agent
        self.viewport = viewport or dict(VIEWPORT)
        self.state = 'starting'
        self.error = ''
        self.frame = b''
        self.frame_at = 0.0
        self.result = {}
        self.touched = time.time()
        self.started = time.time()
        self._commands = queue.Queue()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f'of-connect-{self.id}')
        self._thread.start()

    # ── what the request handlers call ────────────────────────────────────────

    def act(self, kind, **kw):
        """Queue one input for the browser. Returns immediately: the creator's
        next frame poll is what shows them it happened."""
        self.touched = time.time()
        if self._done.is_set():
            raise ConnectError('this sign-in has finished')
        self._commands.put((kind, kw))

    def status(self):
        self.touched = time.time()
        return {'attempt': self.id, 'state': self.state, 'error': self.error,
                'persona': self.persona, 'account': self.account,
                'width': self.viewport['width'], 'height': self.viewport['height'],
                'expires_in': max(0, int(ATTEMPT_TTL - (time.time() - self.started))),
                'result': self.result}

    def snapshot(self):
        """The latest frame as a data URL, or '' before the first one."""
        self.touched = time.time()
        if not self.frame:
            return ''
        return 'data:image/jpeg;base64,' + base64.b64encode(self.frame).decode()

    def close(self):
        self._done.set()
        self._commands.put(('quit', {}))

    def expired(self):
        now = time.time()
        return (now - self.started > ATTEMPT_TTL) or (now - self.touched > IDLE_TTL)

    # ── the browser thread ────────────────────────────────────────────────────

    def _run(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.state, self.error = 'failed', 'this host has no browser installed'
            return
        try:
            with sync_playwright() as pw:
                self._drive(pw)
        except Exception as e:
            logger.exception('OnlyFans connect attempt failed')
            if self.state != 'connected':
                self.state, self.error = 'failed', str(e)[:200]

    def _launch(self, pw):
        opts = {'headless': True, 'args': ['--no-sandbox', '--disable-dev-shm-usage']}
        if BROWSER_PATH:
            opts['executable_path'] = BROWSER_PATH
        if self.proxy:
            opts['proxy'] = _proxy_options(self.proxy)
        browser = pw.chromium.launch(**opts)
        context = browser.new_context(
            viewport=self.viewport, user_agent=self.user_agent or None,
            locale='en-US', timezone_id=os.getenv('ONLYFANS_TZ', 'Europe/Amsterdam'))
        return browser, context

    def _drive(self, pw):
        browser, context = self._launch(pw)
        page = context.new_page()
        page.goto(SIGNIN_URL, wait_until='domcontentloaded', timeout=60000)
        self.state = 'signin'
        last_check = 0.0
        while not self._done.is_set():
            try:
                kind, kw = self._commands.get(timeout=0.25)
                if kind == 'quit':
                    break
                self._apply(page, kind, kw)
            except queue.Empty:
                pass
            except Exception as e:
                logger.debug('input to the hosted browser failed: %s', str(e)[:120])
            self._capture(page)
            if self.state == 'signin' and time.time() - last_check > POLL_SECONDS:
                last_check = time.time()
                self._try_capture_session(page, context)
            if self.expired() and self.state != 'connected':
                self.state, self.error = 'expired', 'the sign-in timed out'
                break
        try:
            context.close()
            browser.close()
        except Exception:
            pass
        self._done.set()

    def _apply(self, page, kind, kw):
        if kind == 'click':
            page.mouse.click(float(kw['x']), float(kw['y']))
        elif kind == 'move':
            # Forwarded because the human check watches for it. A cursor that
            # teleports to a checkbox and clicks is exactly what it fails.
            page.mouse.move(float(kw['x']), float(kw['y']))
        elif kind == 'down':
            page.mouse.move(float(kw['x']), float(kw['y']))
            page.mouse.down()
        elif kind == 'up':
            page.mouse.move(float(kw['x']), float(kw['y']))
            page.mouse.up()
        elif kind == 'type':
            page.keyboard.insert_text(str(kw.get('text') or '')[:200])
        elif kind == 'key':
            page.keyboard.press(str(kw.get('key') or 'Enter')[:20])
        elif kind == 'scroll':
            page.mouse.wheel(0, float(kw.get('dy') or 0))
        elif kind == 'back':
            page.go_back()

    def _capture(self, page):
        if time.time() - self.frame_at < 0.2:
            return
        try:
            self.frame = page.screenshot(type='jpeg', quality=FRAME_QUALITY,
                                         timeout=5000)
            self.frame_at = time.time()
        except Exception:
            pass

    def _try_capture_session(self, page, context):
        """Is the creator in yet? If so, take the session and stop.

        The page asks OnlyFans who it is, rather than us doing it: it is already
        signed in and signs its own requests, so a successful answer proves the
        session works before we ever store it.
        """
        cookies = context.cookies(COOKIE_ORIGIN)
        names = {c['name'] for c in cookies}
        if not {'sess', 'auth_id'} <= names:
            return
        try:
            who = page.evaluate(
                "() => fetch('/api2/v2/users/me', {credentials:'include'})"
                ".then(r => r.ok ? r.json() : null).catch(() => null)")
        except Exception:
            return
        if not (isinstance(who, dict) and who.get('id')):
            return
        try:
            x_bc = page.evaluate("() => localStorage.getItem('bcTokenSha') || ''")
            agent = page.evaluate('() => navigator.userAgent')
        except Exception:
            x_bc, agent = '', self.user_agent
        session = {'user_id': str(who['id']), 'username': who.get('username') or '',
                   'name': who.get('name') or '',
                   'cookie': of_session.cookie_string(cookies), 'x_bc': x_bc or '',
                   'user_agent': agent or self.user_agent, 'proxy': self.proxy}
        self.result = of_session.put(self.account, session)
        self.state = 'connected'
        self._done.set()


def _proxy_options(proxy):
    """Playwright wants the credentials split out of the proxy URL."""
    rest = proxy.split('://', 1)[-1]
    scheme = proxy.split('://', 1)[0] if '://' in proxy else 'http'
    creds, _, host = rest.rpartition('@')
    out = {'server': f'{scheme}://{host}'}
    if creds:
        user, _, password = creds.partition(':')
        out['username'], out['password'] = user, password
    return out


# ── the registry ──────────────────────────────────────────────────────────────

def start(persona, account, proxy='', user_agent='', viewport=None):
    if not available():
        raise ConnectError('this host has no browser installed, so an account '
                           'cannot be connected here')
    sweep()
    with _lock:
        for a in list(_attempts.values()):
            if a.persona == persona:
                a.close()
                _attempts.pop(a.id, None)
        attempt = Attempt(persona, account, proxy, user_agent, viewport)
        _attempts[attempt.id] = attempt
    return attempt


def get(attempt_id):
    with _lock:
        return _attempts.get(attempt_id)


def cancel(attempt_id):
    with _lock:
        attempt = _attempts.pop(attempt_id, None)
    if attempt:
        attempt.close()


def sweep():
    """Drop attempts nobody is watching. Every entry holds a browser and an IP."""
    with _lock:
        stale = [a for a in _attempts.values()
                 if a.expired() or a._done.is_set() and a.state != 'connected']
    for a in stale:
        a.close()
        with _lock:
            _attempts.pop(a.id, None)
