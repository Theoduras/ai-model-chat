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
every 50ms is enough to type a password into.
"""
import hashlib
import base64
import collections
import json
import logging
import os
import queue
import re
import shutil
import threading
import time
import urllib.parse
import uuid

import of_rules
import of_session

logger = logging.getLogger(__name__)

# Where to look for a browser, best first. Real Google Chrome rather than the
# Chromium Playwright downloads: the open-source build has no H.264, calls
# itself HeadlessChrome and exposes no userAgentData, and the human check reads
# all three. The bundled Chromium is the last resort, for a laptop with no
# Chrome on it.
CHROME_PATHS = ('/usr/bin/google-chrome', '/usr/bin/google-chrome-stable',
                '/opt/google/chrome/chrome')
BROWSER_PATH = (os.getenv('ONLYFANS_CHROME')
                or os.getenv('PLAYWRIGHT_CHROMIUM') or '').strip()
SIGNIN_URL = 'https://onlyfans.com/'
COOKIE_ORIGIN = 'https://onlyfans.com'
# Which site a sign-in is for. Everything that makes this a *browser* rather
# than an OnlyFans client — the input relay, the frame capture, the profile, the
# anti-detection setup — works for any login page, so the parts that do not are
# named here rather than spread through the module. The only real difference is
# what a finished sign-in leaves behind and where to look for it.
SITES = {
    'onlyfans': {'url': SIGNIN_URL, 'origin': COOKIE_ORIGIN, 'prefix': 'ofc_'},
    'discord': {'url': 'https://discord.com/login', 'origin': 'https://discord.com',
                'prefix': 'dcc_'},
    'instagram': {'url': 'https://www.instagram.com/accounts/login/',
                  'origin': 'https://www.instagram.com', 'prefix': 'igc_'},
    'reddit': {'url': 'https://www.reddit.com/login', 'origin': 'https://www.reddit.com',
               'prefix': 'rdc_'},
    'tiktok': {'url': 'https://www.tiktok.com/login', 'origin': 'https://www.tiktok.com',
               'prefix': 'ttc_'},
}
VIEWPORT = {'width': 900, 'height': 700}
FRAME_QUALITY = 55
# Everything _apply knows how to do. The route rejects anything else, so the two
# have to be read from the same place.
INPUT_KINDS = ('click', 'move', 'down', 'up', 'type', 'key', 'scroll', 'back')
# How long a half-finished sign-in is kept alive. Long enough to find a phone
# and read a code out of it, short enough that an abandoned tab does not hold a
# browser and an IP for the rest of the day.
ATTEMPT_TTL = 20 * 60
# Three minutes was too short for the thing this window exists for: finding a
# phone, opening an email, reading a code back. The window closing under
# someone mid-sign-in costs the whole attempt, which is worse than a browser
# held for a few more minutes.
IDLE_TTL = 8 * 60
POLL_SECONDS = 1.5
# How long one replayed path may hold the browser thread. Everything else the
# creator does is queued behind it.
MOVE_BUDGET = 0.1

_attempts = {}
_lock = threading.Lock()
_sink = None


def session_sink(fn):
    """Where a captured session goes. The default is the vault; the browser
    service overrides it, because it carries no database of its own."""
    global _sink
    _sink = fn


class ConnectError(RuntimeError):
    pass


def _driver():
    """Playwright, patched if the patched build is installed.

    patchright is a drop-in fork that closes the leaks the check looks for --
    the CDP Runtime.enable call and the webdriver flag -- at the protocol level.
    Doing the same from an init script does not work: the script is itself
    visible to the page.
    """
    try:
        from patchright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        from playwright.sync_api import sync_playwright
        return sync_playwright


def browser_path():
    """The browser to drive, or '' to let the driver pick its own."""
    if BROWSER_PATH:
        return BROWSER_PATH
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    return shutil.which('google-chrome') or ''


def available():
    """Whether this host can run the hosted browser at all."""
    try:
        _driver()
    except ImportError:
        return False
    return True


# Credentials, and nothing else: a request OnlyFans accepted is the only
# specification of what one should look like, and keeping four of its fifteen
# headers is what turned "which header is wrong" into one guess per deploy.
SECRET_HEADERS = ('cookie', 'x-bc', 'authorization')


def _safe_headers(headers):
    """Every header of a request OnlyFans accepted, secrets replaced by size."""
    out = {}
    for name, value in (headers or {}).items():
        name = str(name).lower()
        if name in SECRET_HEADERS:
            out[name] = f'<{len(value or "")} chars>'
        else:
            out[name] = str(value)[:200]
    return out


class Attempt:
    """One creator, one sign-in, one browser."""

    # Only ever replaced, never mutated in place, so one default is safe to
    # share — and status() cannot trip over an attempt built without it.
    signing_sample = {}
    # What the site answered when it refused a sign-in, and the address this
    # browser was seen at. Replaced, never mutated, so a default is safe to share.
    login_errors = ()
    exit_ip = ''
    exit_error = ''
    _sampled = False
    # Same reason: an attempt assembled field by field rather than constructed
    # still has to be able to say which site it is for.
    site = 'onlyfans'
    _site = SITES['onlyfans']

    def __init__(self, persona, account, proxy='', user_agent='', viewport=None,
                 drive=True, site='onlyfans'):
        self.site = site if site in SITES else 'onlyfans'
        self._site = SITES[self.site]
        self.id = self._site['prefix'] + uuid.uuid4().hex[:16]
        self.persona = persona
        self.account = account
        self.proxy = proxy
        self.user_agent = user_agent
        self.viewport = viewport or dict(VIEWPORT)
        self.state = 'starting'
        self.error = ''
        self.frame = b''
        self.frame_at = 0.0
        self.signing_sample = {}
        self.result = {}
        self.touched = time.time()
        self.started = time.time()
        # Diagnostics only: _try_capture_session returns silently on every
        # failure path, so these are the only record of why a sign-in never
        # reaches 'connected'.
        self.probes = 0
        self.capture_note = ''
        # Signatures this attempt injected into the page. A sample must come
        # from OnlyFans, never from us.
        self._injected = collections.deque(maxlen=8)
        self._warned_injected = False
        self.page_url = ''
        self.cookie_names = []
        self._commands = queue.Queue()
        self._done = threading.Event()
        self._thread = None
        # A probe borrows _launch and drives the browser on the caller's own
        # thread. Starting this one too would put a second Chrome on the same
        # profile directory, which Chrome refuses -- and the refusal surfaces as
        # the probe failing, not the thread nobody asked for.
        if drive:
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
                'persona': self.persona, 'account': self.account, 'site': self.site,
                'width': self.viewport['width'], 'height': self.viewport['height'],
                'expires_in': max(0, int(ATTEMPT_TTL - (time.time() - self.started))),
                'result': self.result, 'probes': self.probes,
                'frame_at': round(self.frame_at, 3),
                'capture_note': self.capture_note, 'page_url': self.page_url,
                'cookie_names': self.cookie_names,
                'login_errors': list(getattr(self, 'login_errors', [])),
                'exit_ip': getattr(self, 'exit_ip', ''),
                'proxy_set': bool(self.proxy),
                'signing_sample': self.signing_sample}

    def snapshot(self, since=0.0):
        """The latest frame as a data URL, or '' before the first one.

        `since` is the frame the window is already showing. The page is
        screenshotted five times a second at most, so a poll faster than that
        would otherwise re-send a picture identical to the one on screen --
        35KB a time, several times a second, for nothing.
        """
        self.touched = time.time()
        if not self.frame:
            return ''
        try:
            if self.frame_at and float(since or 0) >= self.frame_at:
                return ''
        except (TypeError, ValueError):
            pass
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
            sync_playwright = _driver()
        except ImportError:
            self.state, self.error = 'failed', 'this host has no browser installed'
            return
        try:
            with sync_playwright() as pw:
                self._drive(pw)
        except Exception as e:
            logger.exception('OnlyFans connect attempt failed')
            if self.state != 'connected':
                self.state, self.error = 'failed', self._why_it_died(e)

    def _why_it_died(self, e):
        """The failure, and whether a proxy was in play.

        A login page that refuses to load at all is nearly always the exit IP,
        and the operator's first question is whether their proxy was actually
        being used -- which the raw Playwright error cannot answer, so it is
        said here rather than guessed at from the outside.
        """
        detail = str(e)[:200]
        blocked = ('ERR_HTTP_RESPONSE_CODE_FAILURE' in detail
                   or 'ERR_TUNNEL_CONNECTION_FAILED' in detail
                   or 'ERR_CONNECTION' in detail)
        if not blocked:
            return detail
        where = self._site.get('host') or self.site
        seen = getattr(self, 'exit_ip', '')
        at = f' The browser was seen at {seen}.' if seen else ''
        if seen and self.proxy:
            return (f'{where} refused the connection, and the browser was leaving '
                    f'through the proxy at {seen} — so that address is the one '
                    f'being turned away. Try a different exit IP. ({detail[:80]})')
        if self.proxy and getattr(self, 'exit_error', ''):
            return (f'The browser could not reach the internet through the proxy '
                    f'that is set, so it never got to {where}. Check the host, '
                    f'port and credentials. ({self.exit_error[:100]})')
        if not self.proxy:
            return (f'{where} refused the connection and no proxy was in use, so '
                    f'this came from the server\u2019s own address. Set a proxy for '
                    f'this model before signing in.{at} ({detail[:80]})')
        return (f'{where} refused the connection through the proxy that is set. '
                f'That address is blocked or the credentials are wrong \u2014 try a '
                f'different exit IP. ({detail[:80]})')

    def _launch(self, pw, bypass_csp=False):
        """A browser the check has no reason to refuse.

        Headful whenever there is a display to be headful on -- Xvfb provides
        one in the container. A headless page is never focused, and the check
        does not complete on a page it believes nobody is looking at.

        The window is sized instead of the viewport, and the profile is a real
        one on disk, because both of the shortcuts show: an overridden viewport
        leaves innerWidth disagreeing with outerWidth, and a browser with no
        profile behind it is not one anybody signs in with.
        """
        self._profile = f'/tmp/of-profile-{self.id}'
        opts = {
            'headless': not os.environ.get('DISPLAY'),
            'args': ['--no-sandbox', '--disable-dev-shm-usage',
                     '--window-size={width},{height}'.format(**self.viewport)],
            'no_viewport': True,
            'locale': 'en-US',
            'timezone_id': os.getenv('ONLYFANS_TZ', 'Europe/Amsterdam'),
        }
        if bypass_csp:
            # So an inline <script> we append runs in the page's own world:
            # patchright puts add_init_script and evaluate in an isolated world
            # that cannot see the site's axios, and a real script element is
            # the way into the main world -- but only if CSP lets it run.
            opts['bypass_csp'] = True
        path = browser_path()
        if path:
            opts['executable_path'] = path
        if self.proxy:
            opts['proxy'] = _proxy_options(self.proxy)
        # No user_agent override: sending one the browser was not built with
        # leaves it disagreeing with its own client hints, which is worse than
        # the agent we would be hiding. What OnlyFans issued the session to is
        # read off the page afterwards.
        context = pw.chromium.launch_persistent_context(self._profile, **opts)
        return None, context

    def _note_exit_ip(self, page):
        """The address the site sees, measured from inside this browser.

        A proxy checked from the app service proves nothing about this one:
        they are separate Cloud Run services and only this one opens the
        sign-in. When the login page will not load, the first thing worth
        knowing is whether Chrome is even leaving through the proxy that was
        handed to it, and that can only be asked here.
        """
        try:
            page.goto('https://api.ipify.org?format=json',
                      wait_until='domcontentloaded', timeout=20000)
            seen = page.evaluate('() => document.body.innerText')
            self.exit_ip = (json.loads(seen or '{}') or {}).get('ip') or ''
        except Exception as e:
            self.exit_ip = ''
            self.exit_error = str(e)[:160]
            logger.warning('of-connect %s could not reach an echo service: %s',
                           self.id, self.exit_error)
            return
        logger.warning('of-connect %s browser exits at %s (proxy %s)', self.id,
                       self.exit_ip, 'set' if self.proxy else 'NOT set')

    def _drive(self, pw):
        browser, context = self._launch(pw)
        page = context.pages[0] if context.pages else context.new_page()
        if self.site == 'discord':
            self._watch_discord(page)
        elif self.site == 'instagram':
            self._watch_instagram(page)
        elif self.site == 'reddit':
            self._watch_reddit(page)
        elif self.site == 'tiktok':
            self._watch_tiktok(page)
        else:
            self._watch_signing(page)
        try:
            page.goto(self._site['url'], wait_until='domcontentloaded', timeout=60000)
        except Exception as e:
            # A malformed first response (ERR_HTTP_RESPONSE_CODE_FAILURE and
            # friends) is what an anti-bot edge does to a single suspect
            # request more often than a real block -- the same address is
            # frequently let through on the very next try.
            logger.warning('of-connect %s first navigation failed, retrying: %s',
                           self.id, str(e)[:160])
            self._note_exit_ip(page)
            page.goto(self._site['url'], wait_until='domcontentloaded', timeout=60000)
        # What the window is told to scale by has to be the size of the frames
        # it actually gets. Sizing the window rather than overriding the
        # viewport means the page is a little smaller than we asked for -- the
        # browser's own chrome -- so the real size is measured, not assumed.
        try:
            width, height = page.evaluate('() => [innerWidth, innerHeight]')
            if width and height:
                self.viewport = {'width': int(width), 'height': int(height)}
        except Exception:
            pass
        self.state = 'signin'
        last_check = 0.0
        while not self._done.is_set():
            batch, quit_now = self._drain()
            for kind, kw in batch:
                try:
                    self._apply(page, kind, kw)
                except Exception as e:
                    logger.warning('input to the hosted browser failed: %s', str(e)[:120])
            if quit_now:
                break
            self._capture(page)
            if self.state == 'signin' and time.time() - last_check > POLL_SECONDS:
                last_check = time.time()
                self._try_capture_session(page, context)
            if self.expired() and self.state != 'connected':
                self.state, self.error = 'expired', 'the sign-in timed out'
                break
        try:
            context.close()
            if browser:
                browser.close()
        except Exception:
            pass
        shutil.rmtree(self._profile, ignore_errors=True)
        self._done.set()

    def _drain(self):
        """Everything queued right now, in order, with stale movement dropped.

        One command per loop iteration cannot keep up: a move is replayed in
        real time and a screenshot follows it, so the queue grows faster than
        it empties and the click behind it lands seconds late. Only the newest
        path is worth replaying -- the older ones describe a cursor that has
        already moved on -- but everything else is a discrete act the creator
        performed and is kept exactly as it came.
        """
        batch, quit_now = [], False
        try:
            batch.append(self._commands.get(timeout=0.25))
        except queue.Empty:
            return [], False
        while True:
            try:
                batch.append(self._commands.get_nowait())
            except queue.Empty:
                break
        if any(kind == 'quit' for kind, _ in batch):
            quit_now = True
            batch = batch[:[kind for kind, _ in batch].index('quit')]
        moves = [i for i, (kind, _) in enumerate(batch) if kind == 'move']
        if len(moves) > 1:
            keep = set(moves[:-1])
            batch = [c for i, c in enumerate(batch) if i not in keep]
        return batch, quit_now

    def _apply(self, page, kind, kw):
        if kind == 'click':
            page.mouse.click(float(kw['x']), float(kw['y']))
        elif kind == 'move':
            # Forwarded because the human check watches for it. A cursor that
            # teleports to a checkbox and clicks is exactly what it fails, and
            # so is one that arrives in a few evenly spaced hops -- so the
            # window sends the path as it was actually drawn and it is replayed
            # here at the speed it was drawn at.
            spent = 0.0
            for x, y, gap in _path(kw):
                # The pauses are what make the path look drawn rather than
                # computed, but they are also the browser thread standing
                # still. Past the budget the remaining points are walked at
                # full speed rather than held onto.
                if gap and spent < MOVE_BUDGET:
                    time.sleep(gap)
                    spent += gap
                page.mouse.move(x, y)
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

    def _watch_signing(self, page):
        """Keep the newest request OnlyFans' own page signed.

        The page signs its own API calls, so one of them is a worked example:
        for this exact path, time and user id, this is the signature OnlyFans
        expects. That turns "are our rules current" into arithmetic.

        It is kept on the attempt rather than written anywhere, because the
        browser service has no database — like the session, it goes back to the
        app with the attempt's status. Nothing here reads the cookie or the
        device token, only the four public values a signature is built from.
        """
        def seen(request):
            try:
                if '/api2/v2/' not in request.url:
                    return
                h = request.headers
                if not (h.get('sign') and h.get('time')):
                    return
                # Our own /users/me probe goes out through this page carrying
                # headers we built, so without this the oracle ends up holding
                # our arithmetic and verifying our rules against themselves.
                if h['sign'] in self._injected:
                    if not self._warned_injected:
                        self._warned_injected = True
                        logger.info('ignoring a signature we generated ourselves')
                    return
                self.signing_sample = {
                    'path': of_rules.path_of(request.url), 'time': h['time'],
                    'user_id': h.get('user-id') or '0', 'sign': h['sign'],
                    'app_token': h.get('app-token') or '',
                    'headers': _safe_headers(h)}
                if not self._sampled:
                    self._sampled = True
                    logger.info('captured a signature OnlyFans\' own page produced '
                                '(%s)', self.signing_sample['path'])
            except Exception as e:
                logger.debug('could not keep a signing sample: %s', str(e)[:120])
        # The context, not the page: a sign-in navigates and the human check
        # can open a page of its own, and a request made outside the one page
        # object we happen to hold is invisible.
        for target in (getattr(page, 'context', None), page):
            try:
                target.on('request', seen)
                return
            except Exception as e:
                logger.debug('could not watch signing: %s', str(e)[:120])

    def _capture(self, page):
        if time.time() - self.frame_at < 0.2:
            return
        try:
            self.frame = page.screenshot(type='jpeg', quality=FRAME_QUALITY,
                                         timeout=5000)
            self.frame_at = time.time()
        except Exception:
            pass

    def _watch_discord(self, page):
        """Catch the credentials off Discord's own client as it uses them.

        Discord deletes `window.localStorage` in its client specifically to stop
        the token being read out of the page, so it is taken the way it actually
        travels: on the Authorization header of the first API call the signed-in
        client makes. The same request carries the two things that are otherwise
        guesswork — the client build the account really identified with, and the
        user agent — and a gateway whose fingerprint disagrees with the browser
        it claims to be is the loudest thing an automated account can do.
        """
        def seen(request):
            try:
                if '/api/' not in request.url or 'discord.com' not in request.url:
                    return
                headers = {k.lower(): v for k, v in (request.headers or {}).items()}
                token = (headers.get('authorization') or '').strip()
                # A bot token would be prefixed; a user's is bare. An OAuth
                # bearer belongs to some embedded app, not to her account.
                if not token or ' ' in token:
                    return
                self._dc_seen = {
                    'token': token,
                    'super_properties': headers.get('x-super-properties') or '',
                    'user_agent': headers.get('user-agent') or '',
                }
            except Exception:
                pass

        def identified(ws):
            # The client opens the gateway itself on load, and its IDENTIFY
            # carries the capabilities bitfield — a number that changes with
            # Discord's own releases and cannot be read from anywhere else.
            # Taking it from the real client is the difference between matching
            # the account's own browser and guessing at it.
            def frame(payload):
                try:
                    sent = json.loads(payload)
                    if sent.get('op') == 2:
                        self._dc_caps = int((sent.get('d') or {}).get('capabilities') or 0)
                except Exception:
                    pass
            try:
                ws.on('framesent', frame)
            except Exception:
                pass

        try:
            page.on('request', seen)
            page.on('websocket', identified)
        except Exception:
            pass

    def _capture_discord(self, page, context):
        """Finished once the captured token answers as somebody.

        The token is checked by using it, because holding one is not the same as
        holding a working one. It goes on the Authorization header: Discord does
        not authenticate by cookie, so a `credentials: 'include'` fetch is a 401
        whether or not anyone is signed in — which is exactly what made this sit
        on 'awaiting_login' forever while the operator watched their own Discord
        load in the window.
        """
        held = getattr(self, '_dc_seen', None)
        if not held:
            self.capture_note = 'awaiting_login'
            return
        build, capabilities = 0, int(getattr(self, '_dc_caps', 0) or 0)
        try:
            raw = json.loads(base64.b64decode(held['super_properties']).decode())
            build = int(raw.get('client_build_number') or 0)
        except Exception:
            pass
        me = {}
        try:
            me = page.evaluate(
                '''async (token) => {
                     const r = await fetch('/api/v9/users/@me',
                                           {headers: {authorization: token}});
                     return r.ok ? await r.json() : {};
                   }''', held['token'])
        except Exception:
            pass
        if not (me or {}).get('id'):
            self.capture_note = 'awaiting_login'
            return
        session = {'user_id': str(me.get('id')),
                   'username': me.get('username') or '',
                   'name': me.get('global_name') or me.get('username') or '',
                   'token': held['token'],
                   'super_properties': held['super_properties'],
                   'build': build, 'capabilities': capabilities,
                   'user_agent': held['user_agent'],
                   'proxy': self.proxy, 'verified': True}
        self.capture_note = 'captured'
        self.result = {'user_id': session['user_id'], 'username': session['username'],
                       'name': session['name'], 'build': build}
        if _sink:
            _sink(self.account, session)
        else:
            # No sink means we are running inside the app rather than the
            # browser service, and Discord's credentials live in its own
            # encrypted row rather than the OnlyFans vault — so they wait here
            # for claim() instead of being written from a module that cannot
            # reach the database.
            self._pending_session = session
        self.state = 'connected'
        self._done.set()

    def _watch_instagram(self, page):
        """Catch the app id Instagram's own page sends. The cookie is what
        actually authenticates a request; this only saves a stale default
        constant from being the reason a capture fails after a release."""
        def seen(request):
            try:
                if 'instagram.com' not in request.url:
                    return
                headers = {k.lower(): v for k, v in (request.headers or {}).items()}
                app_id = headers.get('x-ig-app-id')
                if app_id:
                    self._ig_app_id = app_id
            except Exception:
                pass
        try:
            page.on('request', seen)
        except Exception:
            pass

    def _capture_instagram(self, page, context):
        """Finished once the cookies Instagram issued answer as somebody.

        Instagram authenticates by cookie, the same as OnlyFans, so what is
        kept is the cookie header plus the CSRF token every write call needs —
        not a bearer token like Discord's.
        """
        self.probes += 1
        try:
            self.page_url = page.url
        except Exception:
            pass
        cookies = context.cookies(self._site['origin'])
        names = {c['name'] for c in cookies}
        self.cookie_names = sorted(names)
        if 'sessionid' not in names:
            self.capture_note = 'awaiting_cookies'
            return
        csrftoken = next((c['value'] for c in cookies if c['name'] == 'csrftoken'), '')
        app_id = getattr(self, '_ig_app_id', '') or '936619743392459'
        cookie_header = '; '.join(f"{c['name']}={c['value']}" for c in cookies)
        try:
            agent = page.evaluate('() => navigator.userAgent')
        except Exception:
            agent = self.user_agent
        try:
            who = page.evaluate(
                '''async ([app_id, csrftoken]) => {
                     const r = await fetch('/api/v1/accounts/current_user/?edit=true',
                         {credentials: 'include',
                          headers: {'x-ig-app-id': app_id, 'x-csrftoken': csrftoken,
                                    'x-requested-with': 'XMLHttpRequest'}});
                     return r.ok ? await r.json() : null;
                   }''', [app_id, csrftoken])
        except Exception:
            who = None
        user = ((who or {}).get('user') or {}) if isinstance(who, dict) else {}
        if not user.get('pk'):
            # Plainly signed in even if that probe was refused -- ds_user_id is
            # Instagram's own cookie record of who this is.
            ds_user_id = next((c['value'] for c in cookies if c['name'] == 'ds_user_id'), '')
            if not ds_user_id:
                self.capture_note = 'no_user_id'
                return
            user = {'pk': ds_user_id, 'username': ''}
            self.capture_note = 'unverified'
        else:
            self.capture_note = 'captured'
        session = {'user_id': str(user['pk']), 'username': user.get('username') or '',
                   'cookie': cookie_header, 'csrftoken': csrftoken, 'app_id': app_id,
                   'user_agent': agent or self.user_agent, 'proxy': self.proxy,
                   'verified': self.capture_note == 'captured'}
        self.result = {'user_id': session['user_id'], 'username': session['username']}
        if _sink:
            _sink(self.account, session)
        else:
            self._pending_session = session
        self.state = 'connected'
        self._done.set()

    def _watch_reddit(self, page):
        """Take Reddit's own bearer token and chat handshake off the page.

        Reddit's web app authenticates gql and oauth calls with a bearer token
        the cookies alone will not give us, and reaches chat through a Sendbird
        deployment whose app id and websocket host are published nowhere and
        move between releases. Both are read off requests the real page makes,
        for the same reason Discord's build number is captured rather than
        guessed: a constant we invented is a constant that is wrong after the
        next deploy, and a client that looks nothing like the browser that
        signed in is the thing that loses the account.
        """
        def answered(response):
            """What Reddit said when it turned a sign-in down.

            "Server error. Try again later." is the banner Reddit's page shows
            for every refusal, so the page itself tells us nothing. The reason
            is in the response behind it -- a rate limit, a blocked IP, a
            failed bot check -- and without recording it here the only evidence
            of a failed sign-in is a screenshot of that banner.
            """
            try:
                url = response.url or ''
                if 'reddit.com' not in url or response.status < 400:
                    return
                if not any(hit in url for hit in ('login', 'oauth', 'token',
                                                  'gql', 'api/')):
                    return
                body = ''
                try:
                    body = (response.text() or '')[:300]
                except Exception:
                    pass
                self.login_errors = (getattr(self, 'login_errors', []) + [{
                    'url': url.split('?')[0][:160], 'status': response.status,
                    'body': body}])[-6:]
                logger.warning('reddit sign-in refused: %s -> %s %s',
                               url.split('?')[0][:120], response.status, body[:200])
            except Exception:
                pass

        def seen(request):
            try:
                url = request.url or ''
                headers = {k.lower(): v for k, v in (request.headers or {}).items()}
                auth = headers.get('authorization') or ''
                if auth.lower().startswith('bearer ') and 'reddit' in url:
                    self._rd_bearer = auth.split(' ', 1)[1].strip()
                if 'sendbird' in url.lower():
                    sb = dict(getattr(self, '_rd_chat', {}) or {})
                    sb['url'] = url
                    for name in ('session-key', 'app-id', 'sendbird'):
                        if headers.get(name):
                            sb[name.replace('-', '_')] = headers[name]
                    self._rd_chat = sb
            except Exception:
                pass
        try:
            page.on('request', seen)
            page.on('response', answered)
        except Exception:
            pass

    def _capture_reddit(self, page, context):
        """Finished once Reddit's cookies answer as somebody.

        The cookie header is what authenticates www.reddit.com; the bearer
        token watched above is what oauth.reddit.com and chat want. A capture
        without the bearer is still a usable session -- posting and commenting
        go through the cookie -- so it is kept and marked, rather than thrown
        away for the sake of the half that only chat needs.
        """
        self.probes += 1
        try:
            self.page_url = page.url
        except Exception:
            pass
        cookies = context.cookies(self._site['origin'])
        names = {c['name'] for c in cookies}
        self.cookie_names = sorted(names)
        if 'reddit_session' not in names and 'token_v2' not in names:
            self.capture_note = 'awaiting_cookies'
            return
        cookie_header = '; '.join(f"{c['name']}={c['value']}" for c in cookies)
        try:
            agent = page.evaluate('() => navigator.userAgent')
        except Exception:
            agent = self.user_agent
        try:
            who = page.evaluate(
                """async () => {
                     const r = await fetch('/api/me.json', {credentials: 'include'});
                     return r.ok ? await r.json() : null;
                   }""")
        except Exception:
            who = None
        data = ((who or {}).get('data') or {}) if isinstance(who, dict) else {}
        if not data.get('name'):
            self.capture_note = 'no_user_id'
            return
        bearer = getattr(self, '_rd_bearer', '') or ''
        self.capture_note = 'captured' if bearer else 'unverified'
        session = {'user_id': str(data.get('id') or ''), 'username': data.get('name') or '',
                   'cookie': cookie_header, 'modhash': data.get('modhash') or '',
                   'bearer': bearer, 'chat': getattr(self, '_rd_chat', {}) or {},
                   'user_agent': agent or self.user_agent, 'proxy': self.proxy,
                   'verified': bool(bearer)}
        self.result = {'user_id': session['user_id'], 'username': session['username']}
        if _sink:
            _sink(self.account, session)
        else:
            self._pending_session = session
        self.state = 'connected'
        self._done.set()

    def _watch_tiktok(self, page):
        """Catch the device id TikTok's own page sends on every API call.

        Unlike Instagram's app id this one is per browser, not a constant: it
        is minted when the page first loads and every later call carries it.
        A call from a session claiming a different device is what TikTok
        answers with an empty body.
        """
        def seen(request):
            try:
                if 'tiktok.com' not in request.url or 'device_id=' not in request.url:
                    return
                query = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(request.url).query)
                device_id = (query.get('device_id') or [''])[0]
                if device_id and device_id.isdigit():
                    self._tt_device_id = device_id
            except Exception:
                pass
        try:
            page.on('request', seen)
        except Exception:
            pass

    def _capture_tiktok(self, page, context):
        """Finished once the cookies TikTok issued answer as somebody.

        TikTok authenticates by cookie like Instagram does, but a write call
        also needs the CSRF token, the device id its own page was minted with
        and the msToken -- which its anti-bot script re-mints in the browser
        and cannot be made up here. All four are read off the real session.
        """
        self.probes += 1
        try:
            self.page_url = page.url
        except Exception:
            pass
        cookies = context.cookies(self._site['origin'])
        names = {c['name'] for c in cookies}
        self.cookie_names = sorted(names)
        if 'sessionid' not in names:
            self.capture_note = 'awaiting_cookies'
            return
        by_name = {c['name']: c['value'] for c in cookies}
        cookie_header = '; '.join(f"{c['name']}={c['value']}" for c in cookies)
        try:
            agent = page.evaluate('() => navigator.userAgent')
        except Exception:
            agent = self.user_agent
        try:
            who = page.evaluate(
                """async () => {
                     const r = await fetch('/passport/web/account/info/',
                         {credentials: 'include'});
                     return r.ok ? await r.json() : null;
                   }""")
        except Exception:
            who = None
        data = ((who or {}).get('data') or {}) if isinstance(who, dict) else {}
        user_id = str(data.get('user_id_str') or data.get('user_id') or '')
        username = data.get('username') or ''
        if user_id:
            self.capture_note = 'captured'
        else:
            # Plainly signed in even if that probe was refused -- TikTok keeps
            # its own record of who this is in the session cookie pair.
            user_id = by_name.get('uid_tt') or by_name.get('sid_tt') or ''
            if not user_id:
                self.capture_note = 'no_user_id'
                return
            self.capture_note = 'unverified'
        sec_uid = ''
        if username:
            try:
                got = page.evaluate(
                    """async (name) => {
                         const r = await fetch('/api/user/detail/?uniqueId=' +
                             encodeURIComponent(name), {credentials: 'include'});
                         return r.ok ? await r.json() : null;
                       }""", username)
                sec_uid = (((got or {}).get('userInfo') or {}).get('user')
                           or {}).get('secUid') or ''
            except Exception:
                sec_uid = ''
        session = {'user_id': user_id, 'username': username, 'sec_uid': sec_uid,
                   'cookie': cookie_header,
                   'csrftoken': by_name.get('tt_csrf_token') or '',
                   'ms_token': by_name.get('msToken') or '',
                   'device_id': getattr(self, '_tt_device_id', ''),
                   'user_agent': agent or self.user_agent, 'proxy': self.proxy,
                   'verified': self.capture_note == 'captured'}
        self.result = {'user_id': user_id, 'username': username}
        if _sink:
            _sink(self.account, session)
        else:
            self._pending_session = session
        self.state = 'connected'
        self._done.set()


    def _try_capture_session(self, page, context):
        """Is the creator in yet? If so, take the session and stop.

        The page asks OnlyFans who it is, rather than us doing it: it is already
        signed in and signs its own requests, so a successful answer proves the
        session works before we ever store it.
        """
        if self.site == 'discord':
            self.probes += 1
            return self._capture_discord(page, context)
        if self.site == 'instagram':
            return self._capture_instagram(page, context)
        if self.site == 'reddit':
            return self._capture_reddit(page, context)
        if self.site == 'tiktok':
            return self._capture_tiktok(page, context)
        self.probes += 1
        try:
            self.page_url = page.url
        except Exception:
            pass
        cookies = context.cookies(COOKIE_ORIGIN)
        names = {c['name'] for c in cookies}
        self.cookie_names = sorted(names)
        # 'sess' is the one cookie every signed-in page carries; 'auth_id' used
        # to come with it but OnlyFans no longer always sets it, and the
        # /users/me fetch below is the actual proof either way -- this is only
        # a cheap pre-check to skip a fetch when there is plainly no session yet.
        if 'sess' not in names:
            self.capture_note = 'awaiting_cookies'
            return
        try:
            x_bc = page.evaluate("() => localStorage.getItem('bcTokenSha') || ''")
            agent = page.evaluate('() => navigator.userAgent')
        except Exception:
            x_bc, agent = '', self.user_agent
        # OnlyFans now rejects an unsigned /users/me the same as an
        # unauthenticated one -- cookies alone are not enough. Sign it the
        # same way of_client signs every other call; user_id '0' is correct
        # here, this is the request that tells us our own id.
        headers = of_rules.headers('/api2/v2/users/me',
                                    session={'x_bc': x_bc, 'user_agent': agent})
        # cookie/user-agent/referer are forbidden fetch() headers -- the real
        # browser already sends its own, correctly, without our help.
        for name in ('cookie', 'user-agent', 'referer'):
            headers.pop(name, None)
        if headers.get('sign'):
            self._injected.append(headers['sign'])
        try:
            who = page.evaluate(
                "([h]) => fetch('/api2/v2/users/me', {credentials:'include', headers:h})"
                ".then(r => r.ok ? r.json() : null).catch(() => null)", [headers])
        except Exception:
            self.capture_note = 'me_failed'
            return
        cookie = of_session.cookie_string(cookies)
        if not (isinstance(who, dict) and who.get('id')):
            # A rotation makes our own signature unacceptable, and that must not
            # be the reason a creator cannot connect: the page is plainly signed
            # in, so take the id OnlyFans put in her cookies and let the console
            # say it is unverified rather than refusing to finish.
            auth_id = next((c['value'] for c in cookies
                            if c.get('name') == 'auth_id' and c.get('value')), '')
            if not auth_id:
                self.capture_note = 'no_user_id'
                return
            who = {'id': auth_id}
            self.capture_note = 'unverified'
        else:
            self.capture_note = 'captured'
        verified = self.capture_note == 'captured'
        session = {'user_id': str(who['id']), 'username': who.get('username') or '',
                   'name': who.get('name') or '',
                   'cookie': cookie, 'x_bc': x_bc or '',
                   'user_agent': agent or self.user_agent, 'proxy': self.proxy,
                   'verified': verified}
        self.result = (_sink or of_session.put)(self.account, session)
        self.state = 'connected'
        self._done.set()


def sample_now(proxy='', timeout=25):
    """One signature OnlyFans' own page produced, without signing in to anything.

    A logged-out page signs its API calls exactly the same way, as user 0, so
    this is a complete oracle: it says which rule set is current whether or not
    an account can be connected. Nothing is stored and no credentials exist —
    the browser is opened, the first signed request is read off it, and it is
    closed again.
    """
    got = {}
    saw = {'requests': 0, 'api': 0, 'signed': 0}

    def seen(request):
        saw['requests'] += 1
        if '/api2/v2/' not in request.url:
            return
        saw['api'] += 1
        h = request.headers
        if not (h.get('sign') and h.get('time')):
            return
        saw['signed'] += 1
        if got:
            return
        got.update({'path': of_rules.path_of(request.url), 'time': h['time'],
                    'user_id': h.get('user-id') or '0', 'sign': h['sign'],
                    'app_token': h.get('app-token') or '',
                    'headers': _safe_headers(h)})

    probe = Attempt('', '', proxy=proxy, drive=False)
    with _driver()() as pw:
        context = probe._launch(pw)[1]
        try:
            page = context.pages[0] if context.pages else context.new_page()
            context.on('request', seen)
            try:
                page.goto(SIGNIN_URL, wait_until='domcontentloaded', timeout=30000)
            except Exception as e:
                logger.info('signature capture could not load the page: %s', str(e)[:120])
            until = time.time() + timeout
            while not got and time.time() < until:
                page.wait_for_timeout(250)
            if not got:
                # A bare {} is indistinguishable from every cause. What the
                # page was doing separates "blocked before it loaded" from
                # "loaded and signs nothing".
                try:
                    saw['url'], saw['title'] = page.url[:120], (page.title() or '')[:80]
                except Exception:
                    pass
        finally:
            try:
                context.close()
            except Exception:
                pass
    if got:
        logger.info('captured a signature with no sign-in (%s)', got['path'])
        return got
    logger.warning('no signature captured: %s requests, %s to the API, %s of them '
                   'signed; page was %s (%s)', saw['requests'], saw['api'],
                   saw['signed'], saw.get('url', 'unknown'), saw.get('title', ''))
    return {'_saw': saw}


_LITERALS_JS = """() => {
  const out = new Set();
  // Every script the page actually loaded, not only the tags standing in the
  // DOM now: the signing code rides in a chunk imported on demand, which is
  // why scanning script[src] alone finds a few hundred strings and misses it.
  const urls = new Set([...document.querySelectorAll('script[src]')].map(s => s.src));
  for (const e of performance.getEntriesByType('resource')) {
    if (/\\.m?js(\\?|$)/.test(e.name) || e.initiatorType === 'script') urls.add(e.name);
  }
  for (const t of document.querySelectorAll('script:not([src])')) {
    for (const m of t.textContent.matchAll(/["'`]([^"'`\\\\\\s]{8,256})["'`]/g)) out.add(m[1]);
  }
  // The bundles come from a CDN on another origin, where fetch() from the page
  // is refused and returns nothing -- which is why scanning from in here reads
  // only the inline scripts. The URLs go back instead, and the browser's own
  // request context fetches them, which no cross-origin rule applies to.
  return {literals: [...out].slice(0, 50000), urls: [...urls].slice(0, 200)};
}"""


# The class must exclude what can end a string -- quotes, a backslash, any
# space. With those inside it the greedy quantifier ran straight through the
# delimiters and returned one blob per run of adjacent strings, so the literal
# being looked for was never tested on its own.
_LITERAL_RE = re.compile(r'''["'`]([^"'`\\\s]{8,256})["'`]''')


def _bundle_literals(context, urls, marker=''):
    """Every string in the scripts the page loaded, fetched as the browser.

    `context.request` carries the page's cookies and origin and answers to no
    cross-origin rule, so the CDN chunks that fetch() inside the page cannot
    read come back in full here.
    """
    out, seen = set(), {'urls': len(urls), 'ok': 0, 'bytes': 0, 'marker': False}
    for url in urls[:200]:
        try:
            body = context.request.get(url, timeout=20000).text()
        except Exception:
            continue
        seen['ok'] += 1
        seen['bytes'] += len(body)
        if marker and marker in body:
            seen['marker'] = True
        out.update(m.group(1) for m in _LITERAL_RE.finditer(body))
        if len(out) > 400000:
            break
    seen['literals'] = len(out)
    # Whether the revision OnlyFans is signing with appears anywhere in what we
    # fetched separates "reading the wrong files" from "the parameter is not a
    # plain string in the right ones" -- two very different problems.
    logger.info('rule derivation fetched %s of %s scripts, %s bytes; the current '
                'revision %s in them', seen['ok'], seen['urls'], seen['bytes'],
                'appears' if seen['marker'] else 'does not appear')
    return list(out), seen


def derive_rules(sample, proxy='', timeout=45):
    """Work the current signing rules out of OnlyFans' own JavaScript.

    The published mirrors go stale every rotation, and a rotation changes
    `static_param` — a literal in the site's bundle, not something a signature
    can be inverted back into. But the bundle is right there in the page, and a
    captured signature says which literal it is: the one whose SHA-1 over that
    request reproduces it. From there the checksum constant is arithmetic.

    Nothing is trusted on the way out: the result only leaves here if it
    reproduces the signature OnlyFans itself produced.
    """
    report = {'why': '', 'urls': 0, 'ok': 0, 'bytes': 0, 'literals': 0,
              'marker': False, 'confirm': 0}
    if not (sample and sample.get('sign') and sample.get('path')):
        report['why'] = 'no usable sample to derive against'
        return {'rules': {}, 'report': report}
    parts = str(sample['sign']).split(':')
    if len(parts) != 4:
        report['why'] = 'the sample signature is not four parts'
        return {'rules': {}, 'report': report}
    prefix, digest, checksum, suffix = parts
    # One signature cannot prove a shape: the constant absorbs any choice of
    # indexes for a single digest. A second signature the page produced is what
    # separates the real positions from an arithmetic coincidence.
    confirm = []

    def seen(request):
        if '/api2/v2/' not in request.url:
            return
        h = request.headers
        if h.get('sign') and h.get('time') and h['sign'] != sample['sign']:
            confirm.append({'path': of_rules.path_of(request.url), 'time': h['time'],
                            'user_id': h.get('user-id') or '0', 'sign': h['sign']})

    probe = Attempt('', '', proxy=proxy, drive=False)
    literals = []
    with _driver()() as pw:
        context = probe._launch(pw)[1]
        try:
            page = context.pages[0] if context.pages else context.new_page()
            context.on('request', seen)
            try:
                page.goto(SIGNIN_URL, wait_until='networkidle', timeout=timeout * 1000)
            except Exception as e:
                logger.info('rule derivation could not settle the page: %s', str(e)[:120])
            try:
                found = page.evaluate(_LITERALS_JS) or {}
                literals = list(found.get('literals') or [])
                fetched, seen = _bundle_literals(context, found.get('urls') or [],
                                                 marker=prefix)
                literals += fetched
                report.update(seen)
            except Exception as e:
                logger.warning('rule derivation could not read the bundle: %s', str(e)[:120])
        finally:
            try:
                context.close()
            except Exception:
                pass
    static_param = ''
    for candidate in literals:
        msg = '\n'.join([candidate, str(sample['time']), sample['path'],
                          str(sample.get('user_id') or '0')])
        if hashlib.sha1(msg.encode('utf-8')).hexdigest() == digest:
            static_param = candidate
            break
    report['confirm'] = len(confirm)
    if not static_param:
        report['why'] = ('none of %s literals signs the captured request'
                         % len(literals))
        logger.warning('rule derivation read %s literals from the bundle and none '
                       'of them signs the captured request (%s)', len(literals),
                       sample.get('path', '')[:60])
        return {'rules': {}, 'report': report}
    if not confirm:
        report['why'] = 'found the static param, no second signature to prove it'
        logger.warning('found the static param but the page produced no second '
                       'signature to prove the checksum positions with')
        return {'rules': {}, 'report': report}
    rules = _solve_checksum(sample, static_param, prefix, digest, checksum, suffix,
                            confirm=confirm[:4])
    if not rules:
        report['why'] = 'found the static param, no checksum shape reproduces it'
        logger.warning('found the static param but no checksum shape reproduces '
                       'the signature')
        return {'rules': {}, 'report': report}
    logger.info('derived the current OnlyFans signing rules from the page (%s)', prefix)
    return {'rules': rules, 'report': report}


def _solve_checksum(sample, static_param, prefix, digest, checksum, suffix,
                    confirm=(), shapes=None):
    """The checksum is a sum over fixed positions of the digest plus a constant.

    The positions move rarely and the published sets still carry them, so each
    known shape is tried in turn: the constant is whatever makes that shape
    produce this signature, and `of_rules.verify` is the judge.
    """
    shapes = list(shapes or [])
    for url in ([] if shapes else of_rules.RULES_SOURCES):
        try:
            published = of_rules._fetch(url)
        except Exception:
            continue
        idx = published.get('checksum_indexes')
        if idx:
            shapes.append((idx, published.get('app_token') or ''))
    for indexes, app_token in shapes:
        total = sum(ord(digest[i]) for i in indexes if i < len(digest))
        for base, fmt in ((16, '{}:{}:{{}}:{{:x}}:{}'), (10, '{}:{}:{{}}:{{}}:{}')):
            try:
                wanted = int(checksum, base)
            except ValueError:
                continue
            rules = {'static_param': static_param,
                     'format': fmt.format('', prefix, suffix).lstrip(':'),
                     'checksum_indexes': list(indexes),
                     'checksum_constant': wanted - total,
                     'app_token': sample.get('app_token') or app_token,
                     'revision': prefix}
            if of_rules.verify(sample, rules) is not True:
                continue
            if all(of_rules.verify(c, rules) is True for c in confirm):
                return rules
    return {}


def _path(kw):
    """The points of one move, as (x, y, seconds to wait first).

    A batch carries each point's own timestamp; a lone point is still accepted
    so a single move is nothing special. The gap is capped because a replay
    that pauses holds the browser thread and every other input behind it.
    """
    points = kw.get('points') or [{'x': kw.get('x'), 'y': kw.get('y')}]
    out, previous = [], None
    for point in points[:60]:
        at = point.get('t')
        gap = 0.0
        if previous is not None and at is not None:
            gap = min(max((float(at) - previous) / 1000.0, 0.0), 0.05)
        if at is not None:
            previous = float(at)
        out.append((float(point['x']), float(point['y']), gap))
    return out


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

def start(persona, account, proxy='', user_agent='', viewport=None, site='onlyfans'):
    if not available():
        raise ConnectError('this host has no browser installed, so an account '
                           'cannot be connected here')
    sweep()
    with _lock:
        for a in list(_attempts.values()):
            # Only the same site: signing into Discord is not a reason to throw
            # away a half-finished OnlyFans sign-in the same creator is holding
            # a phone for.
            if a.persona == persona and getattr(a, 'site', 'onlyfans') == site:
                logger.info('of-connect %s dropped: %s started another %s sign-in',
                            a.id, persona, site)
                a.close()
                _attempts.pop(a.id, None)
        attempt = Attempt(persona, account, proxy, user_agent, viewport, site=site)
        _attempts[attempt.id] = attempt
    return attempt


# Attempts already reported missing, so the report is made once each rather
# than once per poll. Bounded: an id that stops being asked for stops mattering.
_missed = collections.OrderedDict()
MISS_RELOG = 60
MISS_REMEMBERED = 50


def _say_missed(attempt_id):
    now = time.time()
    with _lock:
        if now - _missed.get(attempt_id, 0.0) < MISS_RELOG:
            return False
        _missed[attempt_id] = now
        _missed.move_to_end(attempt_id)
        while len(_missed) > MISS_REMEMBERED:
            _missed.popitem(last=False)
    return True


def get(attempt_id, frame=False, since=0.0):
    # `frame` is for the browser service, which fetches the picture in the same
    # round trip rather than a second one. In this process it is already here.
    with _lock:
        attempt = _attempts.get(attempt_id)
    # The creator sees a miss as 'that sign-in is no longer open', and the only
    # way to tell which of the three ways it went is to have said so at the
    # time. Once, though: the window polls a frame twenty times a second and its
    # status alongside, so a sign-in that ended left seventy identical lines a
    # second in the log everyone reads to find out what happened.
    if not attempt and attempt_id and _say_missed(attempt_id):
        logger.info('of-connect %s asked for and not here; holding %s',
                    attempt_id, sorted(_attempts))
    return attempt


def cancel(attempt_id):
    with _lock:
        attempt = _attempts.pop(attempt_id, None)
    if attempt:
        logger.info('of-connect %s dropped: cancelled', attempt.id)
        attempt.close()


def claim(attempt):
    """The raw session of a finished attempt, for a caller that still has to
    store it. Usually nothing to hand back — the sink already put it in the
    vault — but a site that keeps its credentials somewhere else leaves them
    here, once."""
    held = getattr(attempt, '_pending_session', None)
    if held is not None:
        attempt._pending_session = None
    return held


def sweep():
    """Drop attempts nobody is watching. Every entry holds a browser and an IP."""
    with _lock:
        stale = [a for a in _attempts.values()
                 if a.expired() or a._done.is_set() and a.state != 'connected']
    for a in stale:
        logger.info('of-connect %s dropped: swept after %.0fs idle in state %s',
                    a.id, time.time() - a.touched, a.state)
        a.close()
        with _lock:
            _attempts.pop(a.id, None)


# Every signed request the page makes, recorded from before its own scripts
# run: the signature is built inside the app's request interceptor, so the way
# out is the only place it can be read.
_SIGN_HOOK_JS = """() => {
  window.__ofsigs = [];
  const keep = (url, headers) => {
    if (!/\\/api2\\/v2\\//.test(url)) return;
    window.__ofsigs.push({url: url, headers: headers});
  };
  const open_ = XMLHttpRequest.prototype.open;
  const set_ = XMLHttpRequest.prototype.setRequestHeader;
  const send_ = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) {
    this.__ofurl = u; this.__ofh = {}; return open_.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) {
    if (this.__ofh) this.__ofh[String(k).toLowerCase()] = v;
    return set_.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    try { keep(this.__ofurl || '', this.__ofh || {}); } catch (e) {}
    return send_.apply(this, arguments);
  };
  const fetch_ = window.fetch;
  window.fetch = function (input, init) {
    try {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      const h = {};
      new Headers((init && init.headers) || (input && input.headers) || {})
        .forEach((v, k) => { h[String(k).toLowerCase()] = v; });
      keep(url, h);
    } catch (e) {}
    return fetch_.apply(this, arguments);
  };
  // A page that wants an untampered fetch takes one out of a fresh iframe's
  // contentWindow, which our patching above never touched. That is the shape
  // the probe found: signed requests on the wire, nothing in __ofsigs, and no
  // worker anywhere to blame. So every frame this document makes gets the same
  // treatment, at the moment it gets a window to patch.
  const patch = (w) => {
    if (!w || w.__ofpatched) return;
    try {
      w.__ofpatched = true;
      const xo = w.XMLHttpRequest && w.XMLHttpRequest.prototype;
      if (xo) {
        const o_ = xo.open, s_ = xo.setRequestHeader, d_ = xo.send;
        xo.open = function (m, u) {
          this.__ofurl = u; this.__ofh = {}; return o_.apply(this, arguments);
        };
        xo.setRequestHeader = function (k, v) {
          if (this.__ofh) this.__ofh[String(k).toLowerCase()] = v;
          return s_.apply(this, arguments);
        };
        xo.send = function () {
          try { keep(this.__ofurl || '', this.__ofh || {}); } catch (e) {}
          return d_.apply(this, arguments);
        };
      }
      const f_ = w.fetch;
      if (f_) w.fetch = function (input, init) {
        try {
          const url = typeof input === 'string' ? input : (input && input.url) || '';
          const h = {};
          new w.Headers((init && init.headers) || (input && input.headers) || {})
            .forEach((v, k) => { h[String(k).toLowerCase()] = v; });
          keep(url, h);
        } catch (e) {}
        return f_.apply(this, arguments);
      };
      window.__offrames = (window.__offrames || 0) + 1;
    } catch (e) {}
  };
  const watch = (node) => {
    try {
      if (!node || String(node.tagName).toLowerCase() !== 'iframe') return;
      patch(node.contentWindow);
      node.addEventListener('load', () => patch(node.contentWindow));
    } catch (e) {}
  };
  const add_ = Node.prototype.appendChild;
  Node.prototype.appendChild = function (node) {
    const out = add_.apply(this, arguments);
    watch(node);
    return out;
  };
  const ins_ = Node.prototype.insertBefore;
  Node.prototype.insertBefore = function (node) {
    const out = ins_.apply(this, arguments);
    watch(node);
    return out;
  };
  try {
    new MutationObserver((records) => {
      for (const r of records) for (const n of r.addedNodes) watch(n);
    }).observe(document.documentElement || document, {childList: true, subtree: true});
  } catch (e) {}
  // The main frame is patched by the code above, not by patch(), so it was
  // never counted. Without that, "nothing recorded" and "never installed"
  // both read as zero and the probe cannot tell them apart.
  window.__ofmain = (window.__ofmain || 0) + 1;
  window.__offetch = window.fetch;
}"""


# What in the page can be asked to make a signed request. The signer is a
# closure inside a request interceptor and cannot be called directly, but
# whatever owns that interceptor can be, and it signs whatever path it is
# handed.
_SIGNER_JS = """(path) => {
  const report = {vue: false, globals: [], via: '', error: ''};
  const callers = [];
  const looks = (o) => {
    try {
      return o && o.interceptors && o.interceptors.request && typeof o.get === 'function';
    } catch (e) { return false; }
  };
  for (const k of Object.getOwnPropertyNames(window)) {
    let v;
    try { v = window[k]; } catch (e) { continue; }
    if (looks(v)) { report.globals.push(k); callers.push(['window.' + k, v]); }
  }
  const root = document.querySelector('#app') || document.body.firstElementChild;
  const vm = root && (root.__vue__ || root.__vue_app__);
  report.vue = !!vm;
  if (vm) {
    for (const name of ['$api', '$axios', '$http', 'axios']) {
      const v = vm[name] || (vm.config && vm.config.globalProperties
                             && vm.config.globalProperties[name]);
      if (looks(v)) callers.push(['vue.' + name, v]);
    }
  }
  for (const [name, inst] of callers) {
    try { inst.get(path); report.via = name; break; }
    catch (e) { report.error = String(e).slice(0, 120); }
  }
  return report;
}"""


# The same owner, asked for the whole answer rather than only the headers it
# signed with. This is what makes the page the client: the request leaves from
# the browser that is signed in as her, so there is no signature to reproduce,
# no app-token to guess and no second exit IP to explain.
_REQUEST_JS = """(arg) => {
  const looks = (o) => {
    try {
      return o && o.interceptors && o.interceptors.request && typeof o.get === 'function';
    } catch (e) { return false; }
  };
  const callers = [];
  for (const k of Object.getOwnPropertyNames(window)) {
    let v;
    try { v = window[k]; } catch (e) { continue; }
    if (looks(v)) callers.push(['window.' + k, v]);
  }
  const root = document.querySelector('#app') || document.body.firstElementChild;
  const vm = root && (root.__vue__ || root.__vue_app__);
  if (vm) {
    for (const name of ['$api', '$axios', '$http', 'axios']) {
      const v = vm[name] || (vm.config && vm.config.globalProperties
                             && vm.config.globalProperties[name]);
      if (looks(v)) callers.push(['vue.' + name, v]);
    }
  }
  if (!callers.length) return {ok: false, status: 0, via: '', error: 'no request interceptor on the page'};
  const opts = {method: arg.method, url: arg.path};
  if (arg.body !== null && arg.body !== undefined) opts.data = arg.body;
  // Whichever owner takes it, the way the signer tries them in turn: one of
  // these objects owns the interceptor and the others may refuse outright.
  let call = null, name = '', why = '';
  for (const [owner, inst] of callers) {
    try { call = inst.request(opts); name = owner; break; }
    catch (e) { why = String(e).slice(0, 120); }
  }
  if (!call) return {ok: false, status: 0, via: '', error: why || 'nothing on the page would make the request'};
  // A refusal is an answer: axios throws on any 4xx, and the status is the
  // whole point of asking.
  call = Promise.resolve(call).then(
    (r) => ({ok: true, status: r.status, via: name, data: r.data}),
    (e) => {
      const r = e && e.response;
      return {ok: false, via: name, status: r ? r.status : 0,
              data: r ? r.data : null, error: String(e).slice(0, 200)};
    });
  // The page is driven from a request thread with its own deadline; a promise
  // that never settles would hold that thread open to the end of it.
  const gaveup = new Promise((res) => setTimeout(
    () => res({ok: false, status: 0, via: name, error: 'the page did not answer in time'}),
    arg.ms || 30000));
  return Promise.race([call, gaveup]);
}"""


def _cookies_for(session):
    """The stored session's cookie header, as cookies a context can be given.

    The session is kept as the header string the page sent, because that is
    what a signed request needs. Putting a page back into that session means
    taking it apart again.
    """
    out = []
    for part in str((session or {}).get('cookie') or '').split(';'):
        name, _, value = part.strip().partition('=')
        if name and value:
            out.append({'name': name, 'value': value, 'domain': '.onlyfans.com',
                        'path': '/', 'secure': True})
    return out


class Signer:
    """A page of OnlyFans' own, kept open, that signs paths on demand.

    `static_param` is not in the bundle any more -- 3.1MB of it was read and
    the revision OnlyFans signs with is not in there -- so there is nothing
    left to reconstruct it from. What is left is the code that does the
    signing, running in a page, and it will sign whatever that page asks for.

    Two things follow, and both are why `sign_now` could not simply be called
    per request. It launches a browser and loads the site every time, which is
    half a minute a signature; and it signs as whoever the page is, which
    logged out is user 0 -- and a signature made over user 0 is refused for a
    request made as her. So the page is opened once, with her cookie in it, and
    kept.

    Playwright's sync API belongs to the thread that made it, and this is
    driven from Flask request threads, so the browser lives on its own thread
    and is spoken to through a queue.
    """

    # Long enough that a quiet hour does not cost a relaunch, short enough that
    # a page which has quietly stopped signing is replaced rather than retried.
    MAX_AGE = 3600
    MAX_IDLE = 900

    def __init__(self, account, session, proxy=''):
        self.account = account
        self.session = session or {}
        self.proxy = proxy
        self.id = uuid.uuid4().hex[:8]
        self.error = ''
        # `error` is why the last probe failed; `fatal` is why the page is
        # gone. Conflating them meant one unsigned path retired the page for
        # good, and every later request fell back to arithmetic signing.
        self.fatal = ''
        self.seen = {}
        self.via = ''
        self.signed = 0
        self.fetched = 0
        self.opened_at = 0.0
        self.used_at = 0.0
        self.page = None
        self._q = queue.Queue()
        self._ready = threading.Event()
        self._thread = None

    # ── the public side, called from request threads ─────────────────────────

    def start(self, timeout=120):
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f'of-signer-{self.id}')
        self._thread.start()
        self._ready.wait(timeout)
        return self.live()

    def live(self):
        return bool(self._thread and self._thread.is_alive() and not self.fatal
                    and self.opened_at)

    def stale(self):
        now = time.time()
        return (now - self.opened_at > self.MAX_AGE
                or (self.used_at and now - self.used_at > self.MAX_IDLE))

    def sign(self, path, timeout=25):
        """One signature, or {} and the reason on `self.error`."""
        if not self.live():
            return {}
        box = queue.Queue(1)
        self._q.put(('sign', path, box))
        try:
            got = box.get(timeout=timeout)
        except queue.Empty:
            return {}
        self.used_at = time.time()
        if got:
            self.signed += 1
        return got

    def fetch(self, method, path, body=None, timeout=45):
        """One whole request as this account, made by her own browser.

        Returns {'status', 'body'} — a refusal included, because a 401 from
        OnlyFans is an answer about the session and has to reach the caller as
        one. {} means the page never answered, and the caller falls back.
        """
        if not self.live():
            return {}
        box = queue.Queue(1)
        self._q.put(('fetch', {'method': method, 'path': path, 'body': body,
                               'ms': int(max(1, timeout - 5) * 1000)}, box))
        try:
            got = box.get(timeout=timeout)
        except queue.Empty:
            return {}
        self.used_at = time.time()
        if got:
            self.fetched += 1
        return got

    def close(self):
        self._q.put(('stop', '', None))

    def state(self):
        return {'account': self.account, 'live': self.live(), 'via': self.via,
                'signed': self.signed, 'fetched': self.fetched,
                'error': self.error[:200], 'fatal': self.fatal[:200],
                'age': int(time.time() - self.opened_at) if self.opened_at else None,
                'page': self._where()}

    def _where(self):
        """What the page was last showing. Its absence is what made the last
        round of diagnostics guesswork: a signer that signs nothing and an
        interstitial look identical from here. Read from the cache, not the
        page -- Playwright's sync objects belong to the browser thread, and
        `state()` is called from whichever thread serves /health."""
        return dict(self.seen)

    # ── the browser side, all on its own thread ──────────────────────────────

    def _run(self):
        probe = Attempt('', '', proxy=self.proxy, drive=False)
        try:
            with _driver()() as pw:
                context = probe._launch(pw)[1]
                try:
                    self._open(context)
                    self._serve(context)
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass
        except Exception as e:
            self.error = self.fatal = str(e)[:200]
            logger.warning('signer %s could not start: %s', self.id, self.error)
        finally:
            self.opened_at = 0.0
            self._ready.set()
            # The probe owns the profile directory this signer ran on, and a
            # signer that ends without removing it leaves a Chrome profile
            # behind for the life of the instance -- on a filesystem that is
            # this instance's memory.
            try:
                shutil.rmtree(probe._profile, ignore_errors=True)
            except Exception:
                pass

    def _open(self, context):
        cookies = _cookies_for(self.session)
        if not cookies:
            raise ConnectError('this session has no cookie to sign with')
        context.add_cookies(cookies)
        # Before the first navigation: the interceptor is installed by the
        # site's own code, and the hook has to be in place before it runs.
        context.add_init_script('(' + _SIGN_HOOK_JS + ')()')
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(SIGNIN_URL, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(3000)
        self.page = page
        self._look()
        self.opened_at = time.time()
        self._ready.set()

    def _serve(self, context):
        while True:
            try:
                what, payload, box = self._q.get(timeout=60)
            except queue.Empty:
                if self.stale():
                    return
                continue
            if what == 'stop':
                return
            try:
                got = (self._fetch_one(payload) if what == 'fetch'
                       else self._sign_one(payload))
            except Exception as e:
                self.error = str(e)[:200]
                if self._page_gone():
                    self.fatal = self.error
                got = {}
            if got:
                self.error = ''
            else:
                self._look()
            if box is not None:
                box.put(got)
            if not self.live():
                return

    def _look(self):
        try:
            self.seen = {'url': (self.page.url or '')[:120],
                         'title': (self.page.title() or '')[:80],
                         'at': int(time.time())}
        except Exception:
            pass

    def _page_gone(self):
        try:
            return self.page is None or self.page.is_closed()
        except Exception:
            return True

    def _sign_one(self, path):
        page = self.page
        # The recorder is emptied first: a signature for an earlier path is
        # still sitting in it, and the wrong one is worse than none.
        page.evaluate('() => { window.__ofsigs = []; }')
        found = page.evaluate(_SIGNER_JS, path) or {}
        self.via = found.get('via') or self.via
        if not found.get('via'):
            self.error = 'no request interceptor on the page'
            return {}
        until = time.time() + 12
        while time.time() < until:
            for s in (page.evaluate('() => window.__ofsigs || []') or []):
                h = s.get('headers') or {}
                if of_rules.path_of(s.get('url') or '') != path:
                    continue
                if h.get('sign') and h.get('time'):
                    return {'path': path, 'time': h['time'],
                            'user_id': h.get('user-id') or '0',
                            'sign': h['sign'],
                            'app_token': h.get('app-token') or ''}
            page.wait_for_timeout(250)
        self.error = 'the page did not sign our path'
        return {}

    def _fetch_one(self, ask):
        got = self.page.evaluate(_REQUEST_JS, ask) or {}
        if not got.get('status'):
            # The site's client is closured out of reach on the current build,
            # which is the same wall the signer hit. The page can still make the
            # request itself; we sign it, which is the half we can do.
            got = self._fetch_signed(ask) or got
        self.via = got.get('via') or self.via
        if not got.get('status'):
            self.error = str(got.get('error') or 'the page made no request')[:200]
            return {}
        return {'status': int(got['status']), 'body': got.get('data')}

    def _fetch_signed(self, ask):
        try:
            headers = of_rules.headers(ask['path'], self.session)
        except Exception as e:
            self.error = 'could not sign for the page: ' + str(e)[:140]
            return {}
        return self.page.evaluate(_FETCH_JS, dict(ask, headers=headers)) or {}


_signers = {}
_signers_lock = threading.Lock()


def signer(account, session, proxy=''):
    """The open signer for an account, started if there is not one.

    Kept per account because a signature carries the user id the page is
    signed in as: one creator's page cannot sign another's request.
    """
    with _signers_lock:
        have = _signers.get(account)
        if have and have.live() and not have.stale():
            return have
        if have:
            try:
                have.close()
            except Exception:
                pass
            _signers.pop(account, None)
        made = Signer(account, session, proxy)
    made.start()
    with _signers_lock:
        _signers[account] = made
    return made


def signer_state():
    with _signers_lock:
        return [s.state() for s in _signers.values()]


def sign_for(account, session, path, proxy=''):
    """Sign one path as this account, through OnlyFans' own page."""
    return signer(account, session, proxy).sign(path)


def request_for(account, session, method, path, body=None, proxy=''):
    """One whole OnlyFans request as this account, made by her own page.

    Signing a path and then sending the request ourselves leaves the two
    halves in different places: the signature comes from a browser here, the
    request from the app's own address with its own TLS handshake. Making the
    request where the signature is made is the only arrangement OnlyFans sees
    as one client.
    """
    return signer(account, session, proxy).fetch(method, path, body)


# When the site's own client cannot be reached, the page is still the thing we
# want: its TLS handshake, its client hints, its cookies. Only the signature has
# to come from us -- and it can, because the rules are checked against
# signatures OnlyFans made for her own requests before they are ever adopted.
#
# fetch() refuses to set cookie, user-agent or referer from script; all three
# are the page's own anyway, which is the entire point of asking it.
_FETCH_JS = """(arg) => {
  const head = {};
  for (const k of Object.keys(arg.headers || {})) {
    const low = k.toLowerCase();
    if (low === 'cookie' || low === 'user-agent' || low === 'referer') continue;
    head[k] = arg.headers[k];
  }
  const opts = {method: arg.method, headers: head, credentials: 'include'};
  if (arg.body !== null && arg.body !== undefined) {
    opts.body = JSON.stringify(arg.body);
    head['content-type'] = 'application/json';
  }
  const call = fetch(arg.path, opts).then(
    (r) => r.text().then((t) => {
      let data = null;
      try { data = t ? JSON.parse(t) : null; } catch (e) { data = t.slice(0, 500); }
      return {ok: r.ok, status: r.status, via: 'fetch', data: data};
    }),
    (e) => ({ok: false, status: 0, via: 'fetch', error: String(e).slice(0, 200)}));
  const gaveup = new Promise((res) => setTimeout(
    () => res({ok: false, status: 0, via: 'fetch', error: 'the page did not answer in time'}),
    arg.ms || 30000));
  return Promise.race([call, gaveup]);
}"""


def _mainworld_sign(page, context, path):
    """Ask the page's own axios to sign our path, from inside the page's world.

    A real <script> element runs in the main world whatever patchright does to
    add_init_script and evaluate, so this is where the site's request
    interceptor is reachable. The signature is read off the wire, tagged by a
    nonce so it is never confused with a request OnlyFans made itself. The
    report says what the main world could see -- how many window keys, which
    axios owners -- so an empty result separates "isolated" from "closured".
    """
    nonce = 'mw' + hashlib.sha1(str(time.time()).encode()).hexdigest()[:10]
    marked = path + ('&' if '?' in path else '?') + '_ofmw=' + nonce
    got = {}

    def seen(r):
        if nonce not in r.url:
            return
        h = r.headers
        if h.get('sign') and h.get('time'):
            got.update({'path': of_rules.path_of(r.url), 'time': h['time'],
                        'user_id': h.get('user-id') or '0', 'sign': h['sign'],
                        'app_token': h.get('app-token') or ''})

    context.on('request', seen)
    code = """
    (function () {
      var report = {vue: false, globals: [], via: '', error: '', winkeys: 0,
                    searched: [], candidates: []};
      var path = %s;
      // A raw axios instance, or a Nuxt $api wrapper: the wrapper has get/post
      // but no interceptors, and it is what most of the site calls through.
      function looks(o) {
        try {
          if (!o) return false;
          if (o.interceptors && o.interceptors.request
              && typeof o.get === 'function') return true;
          return typeof o.get === 'function' && typeof o.post === 'function'
                 && (typeof o === 'function' || typeof o.create === 'function'
                     || typeof o.request === 'function');
        } catch (e) { return false; }
      }
      var callers = [];
      function offer(name, o) {
        if (looks(o)) { report.candidates.push(name); callers.push([name, o]); }
      }
      function scan(name, obj) {
        report.searched.push(name);
        if (!obj) return;
        var keys; try { keys = Object.getOwnPropertyNames(obj); } catch (e) { return; }
        for (var i = 0; i < keys.length; i++) {
          var v; try { v = obj[keys[i]]; } catch (e) { continue; }
          offer(name + '.' + keys[i], v);
        }
      }
      var names = Object.getOwnPropertyNames(window);
      report.winkeys = names.length;
      for (var i = 0; i < names.length; i++) {
        var v; try { v = window[names[i]]; } catch (e) { continue; }
        if (looks(v)) { report.globals.push(names[i]); offer('window.' + names[i], v); }
      }
      // Vue 3 puts __vue_app__ on whatever element it mounted to, which is not
      // always #app; find it by scanning rather than assuming.
      var root = null, app = null;
      var all = document.querySelectorAll('*');
      for (var e = 0; e < all.length && !app; e++) {
        if (all[e].__vue_app__) { root = all[e]; app = all[e].__vue_app__; }
      }
      if (!root) root = document.querySelector('#app')
                        || (document.body && document.body.firstElementChild);
      if (!app && root) app = root.__vue_app__;
      var vm = (root && root.__vue__) || app;
      report.vue = !!(vm || app);
      report.appfound = !!app;
      // Nuxt 3 provides its helpers through the app's inject context, not
      // globalProperties: app._context.provides holds them under $-keys.
      try {
        var ctx = app && (app._context || (app._instance && app._instance.appContext));
        if (ctx) {
          scan('provides', ctx.provides);
          scan('globalProperties', ctx.config && ctx.config.globalProperties);
        }
      } catch (e) { report.error = ('ctx: ' + e).slice(0, 120); }
      try {
        var nx = window.$nuxt || (typeof window.useNuxtApp === 'function'
                                  && window.useNuxtApp());
        if (nx) { scan('nuxt', nx); scan('nuxt.$', nx.$); }
      } catch (e) {}
      try { if (window.__NUXT__) scan('__NUXT__', window.__NUXT__); } catch (e) {}
      for (var k = 0; k < callers.length; k++) {
        try { callers[k][1].get(path); report.via = callers[k][0]; break; }
        catch (e) { report.error = String(e).slice(0, 120); }
      }
      document.documentElement.setAttribute('data-ofmw', JSON.stringify(report));
    })();
    """ % json.dumps(marked)
    try:
        page.add_script_tag(content=code)
    except Exception as e:
        return {'error': 'main-world inject failed: ' + str(e)[:120]}, {}
    until = time.time() + 12
    while not got and time.time() < until:
        page.wait_for_timeout(250)
    try:
        raw = page.evaluate(
            "() => document.documentElement.getAttribute('data-ofmw') || '{}'")
        report = json.loads(raw or '{}')
    except Exception as e:
        report = {'error': 'could not read the main-world report: ' + str(e)[:100]}
    return report, got


def sign_now(path, user_id='0', proxy='', timeout=30):
    """A signature for a path of ours, made by OnlyFans' own code.

    A rotation changes `static_param`, and it is not a string in the bundle:
    2.4MB of it was read and the revision OnlyFans signs with is not in there,
    so there is nothing left to reconstruct it from. The page holding the
    signer is asked to sign the path we need instead, through whichever of its
    own objects owns the request interceptor.

    Nothing here logs in. A signature covers the path, the time and the user
    id; identity is the cookie, and that stays in the app.
    """
    report = {'via': '', 'vue': False, 'globals': [], 'seen': 0, 'why': '',
              'isolated': {}, 'mainworld': {}}
    probe = Attempt('', '', proxy=proxy, drive=False)
    with _driver()() as pw:
        context = probe._launch(pw, bypass_csp=True)[1]
        try:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(SIGNIN_URL, wait_until='domcontentloaded',
                          timeout=timeout * 1000)
            except Exception as e:
                report['why'] = 'the page did not load: ' + str(e)[:120]
                return {'sign': {}, 'report': report}
            page.wait_for_timeout(3000)
            # What evaluate's world can see, kept only to sit beside the main
            # world's view: the difference between the two is the whole answer
            # to whether the signer is out of reach because it is isolated or
            # because it is closured.
            try:
                report['isolated'] = page.evaluate(_SIGNER_JS, path) or {}
            except Exception as e:
                report['isolated'] = {'error': str(e)[:120]}
            mw_report, got = _mainworld_sign(page, context, path)
            report['mainworld'] = mw_report
            report['via'] = mw_report.get('via', '')
            report['vue'] = mw_report.get('vue', False)
            report['globals'] = mw_report.get('globals', [])
            if got.get('sign'):
                return {'sign': {'path': path, 'time': got['time'],
                                 'user_id': got.get('user_id') or user_id,
                                 'sign': got['sign'],
                                 'app_token': got.get('app_token') or ''},
                        'report': report}
            report['why'] = (mw_report.get('error')
                             or ('the main world saw %s window keys and no request '
                                 'interceptor' % mw_report.get('winkeys', 0)))
            return {'sign': {}, 'report': report}
        finally:
            try:
                context.close()
            except Exception:
                pass


_CAPTURE_HOOK = r"""
(function () {
  if (window.__ofcapOn) return; window.__ofcapOn = 1;
  var caps = [], sigs = [], workers = [], params = [];
  var n = {digest: 0, encode: 0, fetch: 0, xhr: 0, worker: 0, join: 0};
  function boot() {
    // Whether the SPA actually ran under our rewrite: without this, quiet
    // counters could mean "main world signs nothing" or "bundle never booted".
    try {
      var app = document.querySelector('#app');
      return {href: String(location.href).slice(0, 200),
              nodes: document.querySelectorAll('*').length,
              mounted: !!(app && app.children && app.children.length)};
    } catch (e) { return {href: '', nodes: 0, mounted: false}; }
  }
  function flush() {
    try {
      document.documentElement.setAttribute(
        'data-ofcap', JSON.stringify({caps: caps, sigs: sigs, n: n,
                                      workers: workers, params: params,
                                      boot: boot()}));
    } catch (e) {}
  }
  var stashed = 0;
  function stashWorker(url) {
    // A blob: worker is built from an in-memory Blob and never hits the wire,
    // but its URL is fetchable from the page that made it. Pull the source and
    // park it in a hidden node so the isolated-world evaluate can read it back
    // across the shared DOM -- the signing code, and its static_param, ride in
    // here, not in any script the wire ever saw.
    try {
      if (String(url).indexOf('blob:') !== 0 || stashed >= 8) return;
      var id = 'ofworker' + stashed; stashed++;
      fetch(url).then(function (r) { return r.text(); }).then(function (src) {
        try {
          var el = document.createElement('script');
          el.type = 'text/plain'; el.id = id; el.textContent = src;
          document.documentElement.appendChild(el);
          n.wsrc = (n.wsrc || 0) + 1; flush();
        } catch (e) {}
      }).catch(function () {});
    } catch (e) {}
  }
  function addWorker(url) {
    try { url = String(url);
      if (url && workers.indexOf(url) < 0 && workers.length < 12) {
        workers.push(url.slice(0, 200)); flush();
      }
      stashWorker(url);
    } catch (e) {}
  }
  // The signed message is static_param + "\n" + time + "\n" + path + "\n" + id.
  // The path may or may not carry the /api2 prefix, so match on the shape that
  // is always there -- a newline, a 10-13 digit timestamp, a newline -- rather
  // than on the path. Counters below say whether any hashing happened at all,
  // so an empty caps list means "not hashed in the main world", not "filtered".
  function record(s) {
    try {
      if (typeof s !== 'string') return;
      if (!/\n\d{10,13}\n/.test(s) && s.indexOf('/api2') < 0) return;
      if (caps.indexOf(s) < 0 && caps.length < 12) { caps.push(s.slice(0, 400)); flush(); }
    } catch (e) {}
  }
  function asString(d) {
    try {
      if (typeof d === 'string') return d;
      if (d && d.buffer) return new TextDecoder().decode(d);
      return new TextDecoder().decode(new Uint8Array(d));
    } catch (e) { return ''; }
  }
  function addSig(v) {
    try { v = String(v);
      if (v && sigs.length < 8 && sigs.indexOf(v) < 0) { sigs.push(v.slice(0, 90)); flush(); }
    } catch (e) {}
  }
  try {
    var TE = TextEncoder.prototype.encode;
    TextEncoder.prototype.encode = function (s) { n.encode++; record(s); return TE.apply(this, arguments); };
  } catch (e) {}
  try {
    if (window.crypto && crypto.subtle && crypto.subtle.digest) {
      var D = crypto.subtle.digest.bind(crypto.subtle);
      crypto.subtle.digest = function (algo, data) { n.digest++; record(asString(data)); return D(algo, data); };
    }
  } catch (e) {}
  try {
    var SH = XMLHttpRequest.prototype.setRequestHeader;
    XMLHttpRequest.prototype.setRequestHeader = function (k, v) {
      n.xhr++;
      try { if (String(k).toLowerCase() === 'sign') addSig(v); } catch (e) {}
      return SH.apply(this, arguments);
    };
  } catch (e) {}
  // The wire showed signed requests this hook did not, which means the sign
  // header is set through fetch, not XHR: read it out of the fetch init.
  try {
    var F = window.fetch;
    window.fetch = function (input, init) {
      n.fetch++;
      try {
        var h = init && init.headers;
        if (h) {
          if (typeof h.get === 'function') { var s = h.get('sign'); if (s) addSig(s); }
          else { for (var k in h) if (String(k).toLowerCase() === 'sign') addSig(h[k]); }
        }
      } catch (e) {}
      return F.apply(this, arguments);
    };
  } catch (e) {}
  // The sha1 is a pure-JS impl (no crypto.subtle/TextEncoder), so hook the
  // message construction instead of the hash. The signed message is
  // [static_param, time, path, user_id].join('\n'), so a '\n' join whose result
  // has the signing shape hands us static_param as this[0] directly.
  try {
    var AJ = Array.prototype.join;
    Array.prototype.join = function (sep) {
      var out = AJ.apply(this, arguments);
      try {
        if (sep === '\n' && this.length >= 3 && typeof out === 'string'
            && (/\n\d{10,13}\n/.test(out) || out.indexOf('/api2') >= 0)) {
          n.join++;
          var p = String(this[0]);
          if (p && params.indexOf(p) < 0 && params.length < 12) {
            params.push(p.slice(0, 200)); flush();
          }
        }
      } catch (e) {}
      return out;
    };
  } catch (e) {}
  // Worker constructors, in case a future page moves signing off-thread.
  try {
    var W = window.Worker;
    if (W) {
      window.Worker = function (url, opts) { n.worker++; addWorker(url); return new W(url, opts); };
      window.Worker.prototype = W.prototype;
    }
  } catch (e) {}
  try {
    var SW = window.SharedWorker;
    if (SW) {
      window.SharedWorker = function (url, opts) { n.worker++; addWorker(url); return new SW(url, opts); };
      window.SharedWorker.prototype = SW.prototype;
    }
  } catch (e) {}
  try {
    if (navigator.serviceWorker && navigator.serviceWorker.register) {
      var REG = navigator.serviceWorker.register.bind(navigator.serviceWorker);
      navigator.serviceWorker.register = function (url) { n.worker++; addWorker(url); return REG.apply(this, arguments); };
    }
  } catch (e) {}
  flush();
})();
"""


def _match_param(candidates, oparts, oracle):
    """Which candidate static_param reproduces the oracle signature, if any.

    A candidate is proven by re-signing the oracle's own request with it and
    getting the oracle's digest back -- the same test derive_rules uses, but
    against values captured live rather than literals from the bundle.
    """
    if len(oparts) != 4 or not oracle.get('path'):
        return ''
    want = oparts[1]
    otime = str(oracle.get('time') or '')
    opath = oracle['path']
    ouid = str(oracle.get('user_id') or '0')
    for cand in candidates or []:
        msg = '\n'.join([cand, otime, opath, ouid])
        if hashlib.sha1(msg.encode('utf-8')).hexdigest() == want:
            return cand
    return ''


def capture_param(sample=None, proxy='', timeout=30):
    """Recover static_param by hooking the hash input, not the request client.

    The client is closured out of reach -- not on window, the Vue app, or
    Nuxt's provides -- so it cannot be asked to sign. But whatever computes the
    signature hashes static_param + "\\n" + time + "\\n" + path + "\\n" + id,
    and that plaintext starts with the param itself. If the site hashes in JS
    (crypto.subtle.digest, or a bundled sha1 fed by TextEncoder.encode), the
    hook reads the param straight off the site signing its own load requests.

    The hook must run before the bundle, and patchright's add_init_script is
    isolated, so it is injected by rewriting only the top document. Every stage
    is time-bounded and the context is always closed in the finally, so a stall
    cannot wedge the single browser instance this shares -- the earlier wedge
    came from routing every resource with no timeout and no teardown, and this
    routes only the document, fetches with a timeout, and continues on error.

    If nothing sign-shaped is ever hashed in JS, that is the answer too: the
    signing is in WASM or a Worker, and the JS route is closed.
    """
    report = {'caps': [], 'sigs': [], 'params': [], 'param': '', 'matched': False,
              'revision': '', 'counts': {}, 'booted': {}, 'workers': [],
              'js': [], 'wasm': [], 'confirm': 0, 'why': ''}
    # Every script and wasm response off the wire, so worker scripts,
    # importScripts, dynamic-import chunks and .wasm -- none of which the DOM
    # <script src> scan sees -- are named for the derivation to read next.
    sources = {}
    # The oracle: a genuine signature OnlyFans produced. Its digest is what a
    # recovered static_param has to reproduce, which is what makes a match a
    # proof rather than a guess. It is passed in from the app process, where the
    # sample lives -- of_rules.sample() is empty in this browser service.
    oracle = sample or of_rules.sample() or {}
    oparts = str(oracle.get('sign') or '').split(':')
    # A second genuine signature, so the checksum positions can be pinned later
    # (Stage C) rather than fitted to one digest by coincidence.
    confirm = []

    def on_signed(request):
        try:
            if '/api2/v2/' not in request.url:
                return
            h = request.headers
            sign = h.get('sign')
            if (sign and h.get('time') and sign != oracle.get('sign')
                    and len(confirm) < 4):
                confirm.append({'path': of_rules.path_of(request.url),
                                'time': h['time'], 'user_id': h.get('user-id') or '0',
                                'sign': sign})
        except Exception:
            pass

    def on_response(resp):
        try:
            url = resp.url
            ct = (resp.headers.get('content-type') or '').lower()
            kind = ''
            if 'wasm' in ct or url.split('?')[0].endswith('.wasm'):
                kind = 'wasm'
            elif 'javascript' in ct or url.split('?')[0].endswith('.js'):
                kind = 'js'
            if kind and url not in sources:
                sources[url] = {'url': url[:200], 'kind': kind,
                                'size': int(resp.headers.get('content-length') or 0)}
        except Exception:
            pass

    probe = Attempt('', '', proxy=proxy, drive=False)
    with _driver()() as pw:
        context = probe._launch(pw, bypass_csp=True)[1]
        try:
            context.on('response', on_response)
            context.on('request', on_signed)

            page = context.pages[0] if context.pages else context.new_page()
            try:
                # No document rewrite: rewriting the top navigation trips
                # Cloudflare, which served a challenge page rather than OnlyFans
                # (the "workers" a rewrite run saw were Cloudflare's). Load the
                # real page the way sign_now/derive_rules do, then inject with
                # add_script_tag -- the method that already gets past the
                # challenge -- so the calls we count are OnlyFans' own.
                page.goto(SIGNIN_URL, wait_until='domcontentloaded',
                          timeout=timeout * 1000)
            except Exception as e:
                report['why'] = 'the page did not load: ' + str(e)[:120]
                return {'capture': report}
            try:
                page.add_script_tag(content=_CAPTURE_HOOK)
            except Exception as e:
                report['why'] = 'hook injection failed: ' + str(e)[:120]
            until = time.time() + 20
            while time.time() < until:
                page.wait_for_timeout(500)
                try:
                    raw = page.evaluate(
                        "() => document.documentElement.getAttribute('data-ofcap') || ''")
                except Exception:
                    raw = ''
                if raw:
                    try:
                        data = json.loads(raw)
                    except Exception:
                        data = {}
                    report['caps'] = data.get('caps', [])
                    report['sigs'] = data.get('sigs', [])
                    report['counts'] = data.get('n', {})
                    report['booted'] = data.get('boot', {})
                    report['workers'] = data.get('workers', [])
                    report['params'] = data.get('params', [])
                    # The join hook hands us candidate static_params; stop as soon
                    # as one reproduces the oracle rather than waiting out the clock.
                    if _match_param(report['params'], oparts, oracle):
                        break
            report['confirm'] = len(confirm)
            inv = list(sources.values())
            report['js'] = [s for s in inv if s['kind'] == 'js']
            report['wasm'] = [s for s in inv if s['kind'] == 'wasm']
            # The join hook is the recovery path: a captured this[0] whose sha1
            # over the oracle's own request reproduces the oracle signature is
            # static_param, proven against a genuine signature.
            param = _match_param(report['params'], oparts, oracle)
            if param:
                report['param'] = param
                report['matched'] = True
                report['revision'] = oparts[0]
            else:
                c = report['counts'] or {}
                if not (len(oparts) == 4 and oracle.get('path')):
                    report['why'] = 'no usable oracle sample to match against'
                elif not report['params']:
                    report['why'] = ('no signing message seen (%s joins, %s xhr, '
                                     '%s confirm); signing happens before the hook '
                                     'installs or is not an Array.join' %
                                     (c.get('join', 0), c.get('xhr', 0),
                                      report['confirm']))
                else:
                    report['why'] = ('captured %s join candidates but none signs '
                                     'the oracle; the message shape differs' %
                                     len(report['params']))
            return {'capture': report}
        finally:
            try:
                context.close()
            except Exception:
                pass


_PROBE_JS = """(nonce) => {
  const out = {sw: [], wasm: [], fetched: null, error: ''};
  try {
    for (const e of performance.getEntriesByType('resource'))
      if (String(e.name).includes('.wasm')) out.wasm.push(String(e.name).slice(-90));
  } catch (e) { out.error = 'resources: ' + e; }
  const regs = (navigator.serviceWorker && navigator.serviceWorker.getRegistrations)
    ? navigator.serviceWorker.getRegistrations() : Promise.resolve([]);
  return regs.then(rs => {
    for (const r of rs) {
      const w = r.active || r.installing || r.waiting;
      out.sw.push({scope: String(r.scope).slice(0, 90),
                   script: w ? String(w.scriptURL).slice(-90) : '',
                   state: w ? w.state : 'none'});
    }
    // Does a plain page-level fetch come back signed? If a ServiceWorker owns
    // the signing it signs this on the way out, and we need nothing else.
    return fetch('/api2/v2/users/me?__probe=' + nonce, {credentials: 'include'})
      .then(r => { out.fetched = r.status; return out; })
      .catch(e => { out.fetched = 0; out.error = (out.error + ' fetch: ' + e).trim();
                    return out; });
  }).catch(e => { out.error = (out.error + ' sw: ' + e).trim(); return out; });
}"""

# A signing input has to survive being sent to us, so it is a longish opaque
# token. Anything shorter matches half the minified bundle.
_PARAMISH = re.compile(r'[A-Za-z0-9+/=_-]{28,48}')


def worth_reading(kind, length):
    """Whether a response body is worth pulling back over CDP to search.

    The scan is the only unbounded stage of the probe and the only speculative
    one: it is looking for rules delivered at runtime. Those arrive as data, so
    JSON is worth reading and a chunk of the bundle is not -- re-reading 3MB of
    minified script to regex it is the work the bundle derivation already did
    and lost, and it is what made the first probe overrun its HTTP call.
    """
    kind = (kind or '').lower()
    if 'json' in kind:
        return length <= MAX_BODY
    if 'javascript' in kind:
        # Small enough to be configuration rather than code.
        return 0 < length <= 40000
    return False


MAX_BODY = 400000
MAX_BODIES = 40


def probe_now(proxy='', budget=60):
    """Where OnlyFans' signing actually lives, read off a logged-out page.

    Three rounds of diagnostics established that the signing inputs are not
    strings in the bundle and that the site never touches the main world's
    fetch/XHR, which leaves a Worker, a ServiceWorker or WASM. Which one it is
    decides whether the signer can be reached at all, and that is not
    something the app service can find out -- only a browser can. So it is
    asked here, and the answer is reported rather than acted on.

    The whole thing runs inside one budget, because it answers one HTTP call
    and the caller's read timeout is the real deadline. Every stage reports
    what it cost, so a probe that runs out of time names its own cause instead
    of costing another round of guessing.

    Nothing signs in and nothing is stored.
    """
    found = {'workers': [], 'service_workers': [], 'wasm': [],
             'our_fetch_signed': None, 'our_fetch_status': None,
             'rules_in_bodies': [], 'scanned': 0, 'skipped': 0, 'ms': {}, 'why': '',
             # The two numbers that settle it. `api` is what Chromium put on
             # the wire; `hooked` is what our patched fetch/XHR recorded. Three
             # rounds inferred this gap from separate runs instead of measuring
             # it in one, and got the cause wrong as a result.
             'api': 0, 'hooked': 0, 'frames_patched': 0, 'page': {}, 'responses': 0,
             'hook': {}, 'driver': _driver().__module__.split('.')[0],
             'signed': 0, 'signed_example': ''}
    began = deadline = time.time()
    deadline += max(20, budget)

    def spent(stage, since):
        found['ms'][stage] = int((time.time() - since) * 1000)

    def left():
        return deadline - time.time()

    nonce = hashlib.sha1(str(time.time()).encode()).hexdigest()[:12]
    want = of_rules.sample() or {}
    found['revision'] = revision = (of_rules.format_of(want).split(':')[0]
                                    if want else '')
    bodies, ours = [], {}

    def response(r):
        if len(bodies) >= MAX_BODIES:
            return
        h = r.headers
        try:
            length = int(h.get('content-length') or 0)
        except ValueError:
            length = 0
        found['responses'] += 1
        if worth_reading(h.get('content-type'), length or 1):
            bodies.append(r)
        else:
            found['skipped'] += 1

    def request(r):
        if '/api2/v2/' in r.url and nonce not in r.url:
            found['api'] += 1
            # The number that actually settles it, read off the wire the way
            # sample_now reads it -- not from the main-world hook, which
            # patchright runs in an isolated world where it sees nothing.
            h = r.headers
            if h.get('sign') and h.get('time'):
                found['signed'] += 1
                if not found.get('signed_example'):
                    found['signed_example'] = (h.get('sign') or '')[:60]
        if nonce in r.url:
            h = r.headers
            ours['signed'] = bool(h.get('sign') and h.get('time'))
            ours['sign'] = (h.get('sign') or '')[:60]

    probe = Attempt('', '', proxy=proxy, drive=False)
    at = time.time()
    with _driver()() as pw:
        context = probe._launch(pw)[1]
        spent('launch', at)
        try:
            context.add_init_script('(' + _SIGN_HOOK_JS + ')()')
            page = context.pages[0] if context.pages else context.new_page()
            page.on('worker', lambda w: found['workers'].append(str(w.url)[-90:]))
            context.on('request', request)
            context.on('response', response)
            try:
                context.on('serviceworker',
                           lambda w: found['service_workers'].append(str(w.url)[-90:]))
            except Exception:
                pass
            at = time.time()
            try:
                page.goto(SIGNIN_URL, wait_until='domcontentloaded',
                          timeout=int(min(20, max(5, left()))) * 1000)
            except Exception as e:
                spent('load', at)
                found['why'] = 'the page did not load: ' + str(e)[:120]
                return found
            page.wait_for_timeout(4000)
            spent('load', at)
            at = time.time()
            try:
                got = page.evaluate(_PROBE_JS, nonce) or {}
            except Exception as e:
                found['why'] = 'could not look: ' + str(e)[:140]
                got = {}
            for row in got.get('sw') or []:
                found['service_workers'].append(row)
            found['wasm'] = got.get('wasm') or []
            found['our_fetch_status'] = got.get('fetched')
            if got.get('error'):
                found['why'] = (found['why'] + ' ' + str(got['error'])[:140]).strip()
            page.wait_for_timeout(1000)
            spent('evaluate', at)
            try:
                found['hooked'] = len(page.evaluate('() => window.__ofsigs || []') or [])
                found['frames_patched'] = page.evaluate('() => window.__offrames || 0')
                # Whether our fetch is still the one installed decides between
                # "the hook never ran" and "the hook ran and was bypassed".
                found['hook'] = page.evaluate('''() => ({
                    main: window.__ofmain || 0,
                    sigs: typeof window.__ofsigs,
                    ours: window.fetch === window.__offetch,
                    native: /\\[native code\\]/.test(String(window.fetch))})''')
                found['frames'] = [f.url[:90] for f in page.frames][:8]
            except Exception:
                pass
            try:
                found['page'] = {'url': page.url[:120], 'title': (page.title() or '')[:80]}
            except Exception:
                pass
            found['our_fetch_signed'] = ours.get('signed')
            found['our_fetch_sign'] = ours.get('sign', '')
            # Does any response carry the revision OnlyFans signs with, or
            # something param-shaped near it? If so the rules arrive over the
            # wire and we can keep our own set current.
            at = time.time()
            for r in bodies:
                if left() <= 2:
                    found['why'] = (found['why'] + ' ran out of time with %s bodies '
                                    'unread' % (len(bodies) - found['scanned'])).strip()
                    break
                try:
                    text = r.text()
                except Exception:
                    continue
                found['scanned'] += 1
                if revision and revision in text:
                    where = text.index(revision)
                    near = text[max(0, where - 400):where + 400]
                    found['rules_in_bodies'].append(
                        {'url': r.url[:120], 'params': _PARAMISH.findall(near)[:8]})
            spent('scan', at)
        finally:
            try:
                context.close()
            except Exception:
                pass
    found['ms']['total'] = int((time.time() - began) * 1000)
    logger.info('transport probe in %sms on %s (%s): %s workers, %s service '
                'workers, %s wasm, %s frames patched; %s API requests on the wire '
                'and %s recorded by the hook; our fetch %s and %s signed; %s of %s '
                'bodies scanned, %s carry revision %s',
                found['ms']['total'], (found['page'].get('url') or '?'),
                (found['page'].get('title') or ''), len(found['workers']),
                len(found['service_workers']), len(found['wasm']),
                found['frames_patched'], found['api'], found['hooked'],
                found['our_fetch_status'],
                'was' if found['our_fetch_signed'] else 'was not',
                found['scanned'], len(bodies), len(found['rules_in_bodies']),
                revision or '?')
    return found
