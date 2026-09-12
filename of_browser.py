"""The hosted sign-in browser, as a service of its own.

A sign-in lives in one process's memory: the Chromium driving it cannot be
serialised, so whichever instance opened it is the only one that can finish it.
That is survivable until the app deploys, and then it is not — a new revision is
a new container, and every half-finished sign-in in the old one dies with it,
including the creator's, mid-2FA.

So the browser runs here instead, in a service that is only redeployed when the
browser itself changes. app.py talks to it through `remote()`, which is shaped
like the `of_connect` module so the routes cannot tell which side of the wire
the browser is on. Unset `ONLYFANS_BROWSER_URL` and they get the in-process one
back, unchanged.

The service holds no database. A captured session is kept in memory until the
app claims it over the token-authenticated endpoint, and the creator's own
browser — which polls frames through the app — is never sent it.
"""
import hmac
import json
import logging
import os
import urllib.error
import urllib.request

import of_connect
import of_trace

logger = logging.getLogger(__name__)


class Unreachable(of_connect.ConnectError):
    """The browser service did not answer -- which is not the same as it
    answering that the sign-in is gone.

    Conflating the two ends a live sign-in on one slow response: the creator's
    window is told the browser no longer exists while it is still sitting there
    holding her half-typed password. A ConnectError subclass so that every
    existing `except of_connect.ConnectError` keeps working unchanged.
    """

TOKEN = (os.getenv('ONLYFANS_BROWSER_TOKEN') or '').strip()
BASE_URL = (os.getenv('ONLYFANS_BROWSER_URL') or '').strip().rstrip('/')
# A frame poll is small and constant; opening a browser is neither.
TIMEOUT = 20
START_TIMEOUT = 90


# ── the service ───────────────────────────────────────────────────────────────

def service():
    """The Flask app the browser service runs. Built on call rather than at
    import, so app.py importing this module for the client does not raise a
    second web app nobody serves."""
    from flask import Flask, jsonify, request

    api = Flask(__name__)
    # The app polls /trace for these, so a capture that fails here is readable
    # in the console instead of only in this service's Cloud Run log.
    of_trace.install()
    # A captured session, by account, held until the app claims it. The vault
    # is on the app's side, so this is the only copy until then.
    pending = {}

    def sink(account, session):
        pending[account] = session
        return {'user_id': session.get('user_id', ''),
                'username': session.get('username', ''),
                'name': session.get('name', '')}

    of_connect.session_sink(sink)

    def authed():
        sent = request.headers.get('X-Browser-Token', '')
        return bool(TOKEN) and hmac.compare_digest(sent, TOKEN)

    @api.before_request
    def guard():
        if request.path == '/health':
            return None
        if not authed():
            return jsonify({'ok': False, 'error': 'unauthorized'}), 401
        return None

    @api.route('/health')
    def health():
        display = os.environ.get('DISPLAY') or ''
        return jsonify({'ok': True, 'browser': of_connect.available(),
                        'guarded': bool(TOKEN), 'display': display,
                        'headless': not display,
                        'browser_path': of_connect.browser_path(),
                        # Lets the app say "this service is an older build"
                        # instead of silently capturing nothing.
                        'signing_capture': hasattr(of_connect, 'sample_now'),
                        'build': of_trace.build_id()})

    @api.route('/signing-sample', methods=['POST'])
    def signing_sample():
        """One signature from OnlyFans' own page. No sign-in, no credentials."""
        d = request.json or {}
        try:
            sample = of_connect.sample_now(proxy=(d.get('proxy') or '').strip())
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)[:200]}), 400
        return jsonify({'ok': True, 'sample': sample})

    @api.route('/derive-rules', methods=['POST'])
    def derive_rules():
        """The current signing rules, worked out of OnlyFans' own bundle."""
        d = request.json or {}
        try:
            rules = of_connect.derive_rules(d.get('sample') or {},
                                            proxy=(d.get('proxy') or '').strip())
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)[:200]}), 400
        return jsonify({'ok': True, 'rules': rules})

    @api.route('/trace')
    def trace():
        return jsonify({'ok': True,
                        'lines': of_trace.recent(int(request.args.get('after') or 0))})

    @api.route('/session', methods=['POST'])
    def start():
        d = request.json or {}
        try:
            attempt = of_connect.start(
                (d.get('persona') or '').strip(), (d.get('account') or '').strip(),
                proxy=(d.get('proxy') or '').strip(),
                user_agent=(d.get('user_agent') or '').strip(),
                viewport=d.get('viewport') or None)
        except of_connect.ConnectError as e:
            return jsonify({'ok': False, 'error': str(e)[:200]}), 400
        return jsonify({'ok': True, 'attempt': attempt.status()})

    @api.route('/session/<attempt_id>')
    def status(attempt_id):
        attempt = of_connect.get(attempt_id)
        if not attempt:
            return jsonify({'ok': False, 'error': 'no such sign-in'}), 404
        out = {'ok': True, 'attempt': attempt.status()}
        # The frame poll wants both and would otherwise ask twice; an input
        # wants neither picture nor the 35KB of it.
        if request.args.get('frame'):
            out['frame'] = attempt.snapshot()
        return jsonify(out)

    @api.route('/session/<attempt_id>/frame')
    def frame(attempt_id):
        attempt = of_connect.get(attempt_id)
        if not attempt:
            return jsonify({'ok': False, 'error': 'no such sign-in'}), 404
        return jsonify({'ok': True, 'frame': attempt.snapshot()})

    @api.route('/session/<attempt_id>/input', methods=['POST'])
    def send_input(attempt_id):
        d = request.json or {}
        kind = (d.get('kind') or '').strip()
        if kind not in of_connect.INPUT_KINDS:
            return jsonify({'ok': False, 'error': 'unknown input'}), 400
        attempt = of_connect.get(attempt_id)
        if not attempt:
            return jsonify({'ok': False, 'error': 'no such sign-in'}), 404
        try:
            attempt.act(kind, x=d.get('x'), y=d.get('y'), text=d.get('text'),
                        key=d.get('key'), dy=d.get('dy'),
                        points=(d.get('points') or [])[:60])
        except of_connect.ConnectError as e:
            return jsonify({'ok': False, 'error': str(e)[:200]}), 400
        return jsonify({'ok': True})

    @api.route('/session/<attempt_id>/claim', methods=['POST'])
    def claim(attempt_id):
        """The credentials, handed over once. Only the app ever calls this, and
        only to write them to the vault."""
        attempt = of_connect.get(attempt_id)
        if not attempt:
            return jsonify({'ok': False, 'error': 'no such sign-in'}), 404
        return jsonify({'ok': True, 'session': pending.pop(attempt.account, None)})

    @api.route('/session/<attempt_id>/cancel', methods=['POST'])
    def cancel(attempt_id):
        of_connect.cancel(attempt_id)
        return jsonify({'ok': True})

    return api


# ── the client app.py uses ────────────────────────────────────────────────────

class _Handle:
    """One attempt on the service, shaped like an of_connect.Attempt."""

    def __init__(self, remote, status, frame=None):
        self._remote = remote
        self._status = status
        self._frame = frame
        self.id = status.get('attempt', '')
        self.persona = status.get('persona', '')
        self.account = status.get('account', '')
        self.result = status.get('result') or {}

    def status(self):
        return self._status

    def snapshot(self):
        # Already here when the caller asked for it up front. The fallback is
        # for a browser service too old to send it alongside the status.
        if self._frame is not None:
            return self._frame
        try:
            return self._remote.call('GET', f'/session/{self.id}/frame').get('frame', '')
        except of_connect.ConnectError:
            return ''

    def act(self, kind, **kw):
        self._remote.call('POST', f'/session/{self.id}/input', dict(kw, kind=kind))


class Remote:
    """The browser service, in the shape of the of_connect module."""

    INPUT_KINDS = of_connect.INPUT_KINDS
    ConnectError = of_connect.ConnectError

    def __init__(self, base_url, token):
        self.base_url = base_url
        self.token = token

    def call(self, method, path, body=None, timeout=TIMEOUT):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header('X-Browser-Token', self.token)
        if data:
            req.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode() or '{}')
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            detail = ''
            try:
                detail = (json.loads(e.read().decode() or '{}') or {}).get('error', '')
            except Exception:
                pass
            # A 5xx is the service having a bad moment, not a verdict about the
            # sign-in. Only something it deliberately refused is a real answer.
            wrong = Unreachable if e.code >= 500 else of_connect.ConnectError
            raise wrong(detail or f'the browser service said {e.code}')
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise Unreachable(f'the browser service is unreachable: {e}')

    def available(self):
        try:
            return bool(self.call('GET', '/health', timeout=5).get('browser'))
        except of_connect.ConnectError:
            return False

    def start(self, persona, account, proxy='', user_agent='', viewport=None):
        out = self.call('POST', '/session',
                        {'persona': persona, 'account': account, 'proxy': proxy,
                         'user_agent': user_agent, 'viewport': viewport},
                        timeout=START_TIMEOUT)
        if not out.get('attempt'):
            raise of_connect.ConnectError(out.get('error') or
                                          'the browser service did not start a sign-in')
        return _Handle(self, out['attempt'])

    def get(self, attempt_id, frame=False):
        """The attempt, or None if the service says there is no such sign-in.

        Raises Unreachable if it could not be asked. None has to mean one thing
        only, because the caller turns it into 'that sign-in is no longer open'.
        """
        if not attempt_id:
            return None
        try:
            out = self.call('GET', f'/session/{attempt_id}' + ('?frame=1' if frame else ''))
        except Unreachable:
            raise
        except of_connect.ConnectError:
            return None
        if not out.get('attempt'):
            return None
        return _Handle(self, out['attempt'], out.get('frame') if frame else None)

    def cancel(self, attempt_id):
        if not attempt_id:
            return
        try:
            self.call('POST', f'/session/{attempt_id}/cancel', {})
        except of_connect.ConnectError:
            pass

    def sample_now(self, proxy='', timeout=25):
        out = self.call('POST', '/signing-sample', {'proxy': proxy},
                        timeout=START_TIMEOUT)
        return out.get('sample') or {}

    def derive_rules(self, sample, proxy=''):
        out = self.call('POST', '/derive-rules', {'sample': sample, 'proxy': proxy},
                        timeout=150)
        return out.get('rules') or {}

    def trace(self, after=0):
        try:
            return self.call('GET', f'/trace?after={int(after)}', timeout=8).get('lines') or []
        except of_connect.ConnectError:
            return []

    def claim(self, attempt):
        """The raw session, for the app to put in the vault. The service holds
        no vault of its own, so until this runs the session exists only there."""
        try:
            return self.call('POST', f'/session/{attempt.id}/claim', {}).get('session')
        except of_connect.ConnectError:
            logger.exception('the OnlyFans session could not be claimed')
            return None


def remote():
    """The browser service, or None when the browser runs in this process."""
    if not (BASE_URL and TOKEN):
        return None
    return Remote(BASE_URL, TOKEN)
