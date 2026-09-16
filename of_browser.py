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
import sys
import urllib.error
import urllib.request

import of_connect
import of_trace

logger = logging.getLogger(__name__)

# This service has no app.py to configure logging for it, so without this every
# logger.warning in of_connect -- the whole diagnostic trail for a sign-in that
# will not load -- is written nowhere, and a failed sign-in leaves no evidence
# at all in the one service that actually drives the browser.
logging.basicConfig(
    level=os.getenv('BROWSER_LOG_LEVEL', 'INFO').upper(),
    format='%(levelname)s %(name)s %(message)s',
    stream=sys.stderr,
    force=True)


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
                        # Which relay this service is running, so "is the fix
                        # deployed" is one call rather than a guess -- the app
                        # and this deploy separately and either can be behind.
                        'relay': getattr(of_connect, 'RELAY_VERSION', '0'),
                        'guarded': bool(TOKEN), 'display': display,
                        'headless': not display,
                        'browser_path': of_connect.browser_path(),
                        # Lets the app say "this service is an older build"
                        # instead of silently capturing nothing.
                        'signing_capture': hasattr(of_connect, 'sample_now'),
                        # Same purpose: signing through the page is newer than
                        # the capture, so a service that has one and not the
                        # other has to be readable as exactly that.
                        'page_signing': hasattr(of_connect, 'sign_for'),
                        # And again for the request path: an app pointed at a
                        # service that can only sign has to fall back rather
                        # than call a route that is not there.
                        'page_requests': hasattr(of_connect, 'request_for'),
                        'transport_probe': hasattr(of_connect, 'probe_now'),
                        'signers': (of_connect.signer_state()
                                    if hasattr(of_connect, 'signer_state') else []),
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

    @api.route('/sign', methods=['POST'])
    def sign():
        """OnlyFans' own code signing a path of ours. No sign-in, no credentials."""
        d = request.json or {}
        try:
            out = of_connect.sign_now((d.get('path') or '').strip(),
                                      user_id=str(d.get('user_id') or '0'),
                                      proxy=(d.get('proxy') or '').strip())
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)[:200]}), 400
        return jsonify({'ok': True, 'sign': out.get('sign') or {},
                        'report': out.get('report') or {}})

    @api.route('/probe', methods=['POST'])
    def probe():
        """Where the signing lives. No sign-in, no credentials, read-only."""
        d = request.json or {}
        try:
            out = of_connect.probe_now(proxy=(d.get('proxy') or '').strip(),
                                       budget=int(d.get('budget') or 60))
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)[:200]}), 400
        return jsonify({'ok': True, 'probe': out})

    @api.route('/capture', methods=['POST'])
    def capture():
        """static_param off the site signing its own requests. No credentials."""
        d = request.json or {}
        try:
            out = of_connect.capture_param(sample=d.get('sample') or None,
                                           proxy=(d.get('proxy') or '').strip())
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)[:200]}), 400
        return jsonify({'ok': True, 'capture': out.get('capture') or {}})

    @api.route('/sign-for', methods=['POST'])
    def sign_for():
        """One signature for an account, from a page kept open as her.

        The session comes over the wire on every call rather than being held
        here: this service keeps no vault, and a signer that outlived the app's
        idea of the session would go on signing for an account already
        disconnected.
        """
        d = request.json or {}
        account = (d.get('account') or '').strip()
        path = (d.get('path') or '').strip()
        if not (account and path):
            return jsonify({'ok': False, 'error': 'account and path required'}), 400
        try:
            got = of_connect.sign_for(account, d.get('session') or {}, path,
                                      proxy=(d.get('proxy') or '').strip())
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)[:200]}), 400
        return jsonify({'ok': True, 'sign': got or {},
                        'signers': of_connect.signer_state()})

    @api.route('/request-for', methods=['POST'])
    def request_for():
        """One whole OnlyFans request, made by the page that is signed in as her.

        The status OnlyFans gave is carried in the body, not in this response's
        own status: a 401 from OnlyFans is an answer about her session, and
        answering 401 here would be indistinguishable from this service
        refusing the app's token.
        """
        d = request.json or {}
        account = (d.get('account') or '').strip()
        path = (d.get('path') or '').strip()
        method = (d.get('method') or 'GET').strip().upper()
        if not (account and path):
            return jsonify({'ok': False, 'error': 'account and path required'}), 400
        try:
            got = of_connect.request_for(account, d.get('session') or {}, method,
                                         path, d.get('body'),
                                         proxy=(d.get('proxy') or '').strip())
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)[:200]}), 400
        if not got:
            return jsonify({'ok': False, 'error': 'the page made no request'}), 400
        return jsonify({'ok': True, 'status': got.get('status'),
                        'body': got.get('body')})

    @api.route('/derive-rules', methods=['POST'])
    def derive_rules():
        """The current signing rules, worked out of OnlyFans' own bundle."""
        d = request.json or {}
        try:
            out = of_connect.derive_rules(d.get('sample') or {},
                                          proxy=(d.get('proxy') or '').strip())
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)[:200]}), 400
        return jsonify({'ok': True, 'rules': out.get('rules') or {},
                        'report': out.get('report') or {}})

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
                viewport=d.get('viewport') or None,
                site=(d.get('site') or 'onlyfans').strip())
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
        if request.args.get('quality'):
            attempt.ask_quality(request.args.get('quality'))
        if request.args.get('wait'):
            attempt.wait_frame(request.args.get('since') or 0,
                               request.args.get('wait') or 0)
        if request.args.get('frame'):
            out['frame'] = attempt.snapshot(request.args.get('since') or 0)
            out['attempt'] = attempt.status()
        return jsonify(out)

    @api.route('/session/<attempt_id>/frame')
    def frame(attempt_id):
        attempt = of_connect.get(attempt_id)
        if not attempt:
            return jsonify({'ok': False, 'error': 'no such sign-in'}), 404
        return jsonify({'ok': True,
                        'frame': attempt.snapshot(request.args.get('since') or 0)})

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
                        key=d.get('key'), dy=d.get('dy'), url=d.get('url'),
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

    def ask_quality(self, quality):
        # The service was told in the same call that fetched the frame; this is
        # here so a handle and a real attempt answer to the same thing.
        return None

    def snapshot(self, since=0.0):
        # Already here when the caller asked for it up front -- the service
        # applied `since` when it sent it. The fallback is for a browser
        # service too old to send it alongside the status; one too old to know
        # `since` at all simply sends the frame, which is what it did before.
        if self._frame is not None:
            return self._frame
        try:
            return self._remote.call(
                'GET', f'/session/{self.id}/frame?since={since}').get('frame', '')
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

    def start(self, persona, account, proxy='', user_agent='', viewport=None,
              site='onlyfans'):
        out = self.call('POST', '/session',
                        {'persona': persona, 'account': account, 'proxy': proxy,
                         'user_agent': user_agent, 'viewport': viewport,
                         'site': site},
                        timeout=START_TIMEOUT)
        if not out.get('attempt'):
            raise of_connect.ConnectError(out.get('error') or
                                          'the browser service did not start a sign-in')
        return _Handle(self, out['attempt'])

    def get(self, attempt_id, frame=False, since=0.0, quality=0, wait=0.0):
        """The attempt, or None if the service says there is no such sign-in.

        Raises Unreachable if it could not be asked. None has to mean one thing
        only, because the caller turns it into 'that sign-in is no longer open'.
        """
        if not attempt_id:
            return None
        try:
            query = f'?frame=1&since={since}' if frame else ''
            if frame and quality:
                query += f'&quality={int(quality)}'
            if frame and wait:
                query += f'&wait={float(wait)}'
            out = self.call('GET', f'/session/{attempt_id}' + query,
                            timeout=TIMEOUT + float(wait or 0))
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

    def sign_now(self, path, user_id='0', proxy=''):
        out = self.call('POST', '/sign',
                        {'path': path, 'user_id': user_id, 'proxy': proxy},
                        timeout=120)
        return {'sign': out.get('sign') or {}, 'report': out.get('report') or {}}

    def probe_now(self, proxy='', budget=60):
        # Headroom over the probe's own budget: a cold Chromium launch is not
        # inside it, and a read timeout here tells us nothing about the page.
        out = self.call('POST', '/probe', {'proxy': proxy, 'budget': budget},
                        timeout=budget + 180)
        return out.get('probe') or {}

    def capture_param(self, sample=None, proxy=''):
        # Headroom over the capture's own bounded stages (a page load plus a
        # 20s watch) so a cold Chromium launch is not read as a page failure.
        out = self.call('POST', '/capture', {'sample': sample, 'proxy': proxy},
                        timeout=180)
        return out.get('capture') or {}

    def sign_for(self, account, session, path, proxy=''):
        # Short: this sits in front of a real request, so a signer that has
        # stopped answering has to fail fast enough to fall back to arithmetic
        # rather than hold the round open.
        out = self.call('POST', '/sign-for',
                        {'account': account, 'session': session, 'path': path,
                         'proxy': proxy}, timeout=40)
        return out.get('sign') or {}

    def request_for(self, account, session, method, path, body=None, proxy=''):
        # Longer than /sign-for: this is the request itself, not the signature
        # in front of it, so OnlyFans' own latency is inside it -- and the
        # first request for an account also waits for her page to open, which
        # is a browser launch and a site load. Timing out here is not fatal
        # (the caller signs it itself instead) but it costs the round, so the
        # cold case is allowed to finish rather than being cut off every time
        # the signer has aged out.
        try:
            out = self.call('POST', '/request-for',
                            {'account': account, 'session': session,
                             'method': method, 'path': path, 'body': body,
                             'proxy': proxy}, timeout=120)
        except of_connect.ConnectError:
            # A service that could not make the request is not a verdict about
            # the account -- the caller signs it itself instead.
            return {}
        if not out.get('ok'):
            return {}
        return {'status': out.get('status'), 'body': out.get('body')}

    def signer_state(self):
        try:
            return self.call('GET', '/health', timeout=6).get('signers') or []
        except of_connect.ConnectError:
            return []

    def derive_rules(self, sample, proxy=''):
        out = self.call('POST', '/derive-rules', {'sample': sample, 'proxy': proxy},
                        timeout=150)
        return {'rules': out.get('rules') or {}, 'report': out.get('report') or {}}

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
