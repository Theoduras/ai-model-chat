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
import shutil
import threading
import time
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
VIEWPORT = {'width': 900, 'height': 700}
FRAME_QUALITY = 55
# Everything _apply knows how to do. The route rejects anything else, so the two
# have to be read from the same place.
INPUT_KINDS = ('click', 'move', 'down', 'up', 'type', 'key', 'scroll', 'back')
# How long a half-finished sign-in is kept alive. Long enough to find a phone
# and read a code out of it, short enough that an abandoned tab does not hold a
# browser and an IP for the rest of the day.
ATTEMPT_TTL = 15 * 60
IDLE_TTL = 3 * 60
POLL_SECONDS = 1.5
# How long one replayed path may hold the browser thread. Everything else the
# creator does is queued behind it.
MOVE_BUDGET = 0.1

_rules_sink = None
_attempts = {}
_lock = threading.Lock()
_sink = None


def rules_sink(fn):
    """Where a captured signature sample goes. Set by app.py; without it a
    sample is simply not kept."""
    global _rules_sink
    _rules_sink = fn


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


class Attempt:
    """One creator, one sign-in, one browser."""

    # Only ever replaced, never mutated in place, so one default is safe to
    # share — and status() cannot trip over an attempt built without it.
    signing_sample = {}
    _sampled = False

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
        self.signing_sample = {}
        self.result = {}
        self.touched = time.time()
        self.started = time.time()
        # Diagnostics only: _try_capture_session returns silently on every
        # failure path, so these are the only record of why a sign-in never
        # reaches 'connected'.
        self.probes = 0
        self.capture_note = ''
        self.page_url = ''
        self.cookie_names = []
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
                'result': self.result, 'probes': self.probes,
                'capture_note': self.capture_note, 'page_url': self.page_url,
                'cookie_names': self.cookie_names,
                'signing_sample': self.signing_sample}

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
                self.state, self.error = 'failed', str(e)[:200]

    def _launch(self, pw):
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

    def _drive(self, pw):
        browser, context = self._launch(pw)
        page = context.pages[0] if context.pages else context.new_page()
        self._watch_signing(page)
        page.goto(SIGNIN_URL, wait_until='domcontentloaded', timeout=60000)
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
                self.signing_sample = {
                    'path': of_rules.path_of(request.url), 'time': h['time'],
                    'user_id': h.get('user-id') or '0', 'sign': h['sign'],
                    'app_token': h.get('app-token') or ''}
                if not self._sampled:
                    self._sampled = True
                    logger.info('captured a signature OnlyFans\' own page produced '
                                '(%s)', self.signing_sample['path'])
                if _rules_sink:
                    _rules_sink(self.signing_sample)
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

    def _try_capture_session(self, page, context):
        """Is the creator in yet? If so, take the session and stop.

        The page asks OnlyFans who it is, rather than us doing it: it is already
        signed in and signs its own requests, so a successful answer proves the
        session works before we ever store it.
        """
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
                    'app_token': h.get('app-token') or ''})

    probe = Attempt('', '', proxy=proxy)
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

def start(persona, account, proxy='', user_agent='', viewport=None):
    if not available():
        raise ConnectError('this host has no browser installed, so an account '
                           'cannot be connected here')
    sweep()
    with _lock:
        for a in list(_attempts.values()):
            if a.persona == persona:
                logger.info('of-connect %s dropped: %s started another sign-in',
                            a.id, persona)
                a.close()
                _attempts.pop(a.id, None)
        attempt = Attempt(persona, account, proxy, user_agent, viewport)
        _attempts[attempt.id] = attempt
    return attempt


def get(attempt_id, frame=False):
    # `frame` is for the browser service, which fetches the picture in the same
    # round trip rather than a second one. In this process it is already here.
    with _lock:
        attempt = _attempts.get(attempt_id)
    # The creator sees a miss as 'that sign-in is no longer open', and the only
    # way to tell which of the three ways it went is to have said so at the time.
    if not attempt and attempt_id:
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
    store it. Nothing to hand back here: the sink already put it in the vault."""
    return None


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
