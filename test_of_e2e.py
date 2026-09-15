"""End to end, on the real pieces.

A real Chromium is driven through the real connect routes against a local page
standing in for onlyfans.com, then a watcher's event is walked through the real
webhook handler. What is faked is only OnlyFans itself.

Run directly: ONLYFANS_TRANSPORT=direct python3 test_of_e2e.py
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

os.environ['ONLYFANS_TRANSPORT'] = 'direct'
os.environ.setdefault('SECRET_KEY', 'e2e-secret')
os.environ.setdefault('ONLYFANS_WEBHOOK_SECRET', 'e2e-webhook')

import of_connect  # noqa: E402
import of_events  # noqa: E402
import of_session  # noqa: E402

# Applied events are remembered by key, so each run needs keys of its own.
RUN = 'e2e-' + uuid.uuid4().hex[:8]

PASS, FAIL = [], []


def check(label, cond):
    (PASS if cond else FAIL).append(label)
    print(('PASS ' if cond else 'FAIL ') + label)


# A stand-in for the part of OnlyFans we depend on: a form that sets the session
# cookies and the device token, and the endpoint that confirms them.
#
# /api2/v2/users/me is answered by the server rather than by a fetch the page
# monkey-patches, because the patched driver evaluates in an isolated world:
# scripts the page defines are not visible to us there. Cookies and storage
# are, which is all the real capture needs.
FAKE_SIGNIN = """<!doctype html><title>OnlyFans</title>
<style>body{font:16px sans-serif;margin:0}
h1{position:fixed;top:20px;left:100px}
#email{position:fixed;top:100px;left:100px;width:400px;height:60px;font-size:18px}
#go{position:fixed;top:200px;left:100px;width:400px;height:80px;font-size:20px}
#out{position:fixed;top:320px;left:100px}</style>
<h1>OnlyFans</h1>
<input id="email" placeholder="Email">
<button id="go" onclick="signIn()">LOG IN</button>
<p id="out"></p>
<script>
function signIn(){
  if(!document.getElementById('email').value){document.getElementById('out').textContent='email?';return;}
  document.cookie='sess=e2e-session-cookie;path=/';
  document.cookie='auth_id=4242;path=/';
  localStorage.setItem('bcTokenSha','e2e-bc-token');
  document.getElementById('out').textContent='signed in';
}
</script>"""


def _serve(root):
    """A throwaway web server for the stand-in OnlyFans page."""
    import http.server
    import threading

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def do_GET(self):
            if not self.path.startswith('/api2/v2/users/me'):
                return super().do_GET()
            signed = 'sess=' in (self.headers.get('Cookie') or '')
            body = json.dumps({'id': 4242, 'username': 'e2e_creator',
                               'name': 'E2E'}).encode() if signed else b'null'
            self.send_response(200 if signed else 401)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd.server_address[1]


def fingerprint():
    """What the human check reads. A browser that announces itself is refused.

    Only Google Chrome can pass this, so on a host that has nothing but the
    Chromium Playwright downloads it is skipped — the image is what this is
    about, and the image carries Chrome.
    """
    if of_connect.browser_path() not in of_connect.CHROME_PATHS:
        print('SKIP the browser does not announce itself (no Chrome on this host)')
        return
    if not os.environ.get('DISPLAY'):
        print('SKIP the browser does not announce itself (no display: run under '
              'xvfb-run, as the image does)')
        return
    # Launched the way an attempt launches one, but on a profile of its own:
    # two browsers cannot share a user data directory.
    probe = of_connect.Attempt.__new__(of_connect.Attempt)
    probe.id, probe.proxy = 'fingerprint-' + uuid.uuid4().hex[:8], ''
    probe.viewport = dict(of_connect.VIEWPORT)
    with of_connect._driver()() as pw:
        _browser, context = probe._launch(pw)
        page = context.new_page()
        # Client hints are only exposed in a secure context, and about:blank is
        # not one. 127.0.0.1 is, as onlyfans.com is over TLS.
        page.goto(of_connect.SIGNIN_URL, wait_until='domcontentloaded')
        seen = page.evaluate("""() => ({
          headless: navigator.userAgent.includes('Headless'),
          webdriver: navigator.webdriver === true,
          hints: !!(navigator.userAgentData || {}).brands,
          h264: !!document.createElement('video')
                  .canPlayType('video/mp4; codecs="avc1.42E01E"')})""")
        context.close()
    shutil.rmtree(probe._profile, ignore_errors=True)
    check('it does not call itself headless', not seen['headless'])
    check('it does not raise the webdriver flag', not seen['webdriver'])
    check('it has the client hints a real Chrome has', seen['hints'])
    check('and the codecs a real Chrome has', seen['h264'])


def browser_flow():
    """Sign in through the hosted browser, exactly as the dashboard drives it."""
    import app as flask_app

    with flask_app.app.app_context():
        of_session.drop('of_e2e')  # a previous run's account is not this run's

    # Served over HTTP, not from a file: cookies are the whole point here and a
    # file:// page cannot set one.
    root = pathlib.Path(tempfile.mkdtemp())
    (root / 'index.html').write_text(FAKE_SIGNIN)
    port = _serve(root)
    of_connect.SIGNIN_URL = f'http://127.0.0.1:{port}/'
    of_connect.COOKIE_ORIGIN = of_connect.SIGNIN_URL
    # Chrome if the host has one, since that is what the image runs; otherwise
    # whatever Chromium Playwright downloaded, so this still runs on a laptop.
    if not of_connect.browser_path():
        for guess in sorted(pathlib.Path('/opt/pw-browsers').glob('chromium-*/chrome-linux/chrome')):
            of_connect.BROWSER_PATH = str(guess)
            break

    attempt = of_connect.start('e2e', 'of_e2e')
    check('a hosted browser opens', attempt is not None)
    fingerprint()

    frame = ''
    for _ in range(60):
        frame = attempt.snapshot()
        if frame:
            break
        time.sleep(0.5)
    check('it streams a frame of the page', frame.startswith('data:image/jpeg;base64,'))
    check('and nothing is stored before she signs in', of_session.get('of_e2e') == {})

    # Every kind the sign-in window forwards has to clear the route's gate, not
    # only Attempt.act. A gate that fell behind the window is what stopped the
    # mouse reaching the browser at all. Sent against an attempt that does not
    # exist, so the real sign-in below is left alone: a kind the route knows
    # gets as far as looking the attempt up (404), one it does not is refused.
    import utils
    real_user, real_active = flask_app._current_user, flask_app._user_is_active
    flask_app._current_user = lambda: {'id': 1, 'is_admin': True}
    flask_app._user_is_active = lambda _u: True
    utils._is_operator = lambda: True
    gate = flask_app.app.test_client()
    refused = [k for k in of_connect.INPUT_KINDS
               if gate.post('/api/onlyfans/connect/input',
                            json={'attempt': 'no-such-attempt', 'kind': k}
                            ).status_code != 404]
    check('the route accepts every input the window sends', not refused)
    check('and still refuses one it does not know',
          gate.post('/api/onlyfans/connect/input',
                    json={'attempt': 'no-such-attempt', 'kind': 'drag'}
                    ).status_code == 400)
    flask_app._current_user, flask_app._user_is_active = real_user, real_active

    # The same input path the sign-in window uses: a mouse that moves, presses
    # and releases, rather than a click that teleports onto the target.
    attempt.act('move', points=[{'x': 250, 'y': 120, 't': 0},
                               {'x': 275, 'y': 125, 't': 12},
                               {'x': 300, 'y': 130, 't': 25}])
    attempt.act('down', x=300, y=130)
    attempt.act('up', x=300, y=130)
    attempt.act('type', text='creator@example.com')
    time.sleep(1.5)
    attempt.act('move', x=300, y=200)
    attempt.act('down', x=300, y=240)
    attempt.act('up', x=300, y=240)

    for _ in range(40):
        if attempt.status()['state'] == 'connected':
            break
        time.sleep(0.5)
    state = attempt.status()
    check('signing in finishes the attempt', state['state'] == 'connected')

    stored = of_session.get('of_e2e')
    check('the session cookie is captured',
          stored.get('cookie') == 'sess=e2e-session-cookie; auth_id=4242')
    check('so is the device token', stored.get('x_bc') == 'e2e-bc-token')
    check('and the user agent it was issued to', bool(stored.get('user_agent')))
    check('the account is who OnlyFans said it was', stored.get('user_id') == '4242')
    check('what the dashboard gets back carries no credentials',
          'cookie' not in state['result'] and state['result']['username'] == 'e2e_creator')

    raw = flask_app._get_setting('onlyfans_vault_of_e2e')
    check('and the stored record is encrypted',
          bool(raw) and 'e2e-session-cookie' not in raw)
    return flask_app


def webhook_flow(flask_app):
    """A watcher event reaches the reply engine through the real endpoint."""
    import hashlib
    import hmac

    with flask_app.app.app_context():
        flask_app._set_setting('onlyfans_account_e2e', 'of_e2e')
        flask_app._set_setting('onlyfans_account_meta_e2e',
                               json.dumps({'onlyfans_id': '4242',
                                           'username': 'e2e_creator'}))
        flask_app._set_setting(flask_app.PLAT_ONLYFANS.k('auto_personas'),
                               json.dumps(['e2e']))

    body = json.dumps({'event': 'messages.received', 'account_id': 'of_e2e',
                       'payload': {'user_id': '7', 'text': 'hey',
                                   'fromUser': {'id': '7', 'username': 'fan7'}}}).encode()
    signature = hmac.new(b'e2e-webhook', body, hashlib.sha256).hexdigest()
    client = flask_app.app.test_client()
    r = client.post('/webhooks/onlyfans', data=body, content_type='application/json',
                    headers={'Signature': signature, 'X-OFAPI-Idempotency-Key': RUN + '-1'})
    check('the webhook accepts a properly signed event', r.status_code == 200)
    check('and it wakes the persona', 'e2e' in flask_app._of_due)

    r = client.post('/webhooks/onlyfans', data=body, content_type='application/json',
                    headers={'Signature': 'deadbeef',
                             'X-OFAPI-Idempotency-Key': RUN + '-2'})
    check('an unsigned event is refused', r.status_code == 401)

    r = client.post('/webhooks/onlyfans', data=body, content_type='application/json',
                    headers={'Signature': signature, 'X-OFAPI-Idempotency-Key': RUN + '-1'})
    check('a redelivery is not applied twice', r.get_json().get('duplicate') is True)

    # The same event through the watcher's own path, which skips the round trip.
    flask_app._of_due.pop('e2e', None)
    of_events.emit('messages.received', 'of_e2e',
                   {'user_id': '9', 'text': 'still there?',
                    'fromUser': {'id': '9', 'username': 'fan9'}}, RUN + '-3')
    check('an event our watcher made takes the same path',
          'e2e' in flask_app._of_due)


def test_the_onlyfans_round_goes_end_to_end():
    """Run as a subprocess: this sets ONLYFANS_TRANSPORT=direct, which
    leaks into test_onlyfans.py when both run in one pytest process."""
    r = subprocess.run([sys.executable, os.path.abspath(__file__)],
                       capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr
    if 'no browser installed' in out:
        import pytest
        pytest.skip('needs a Chromium; the browser flow cannot run here')
    assert r.returncode == 0, out


if __name__ == '__main__':
    flask_app = browser_flow()
    webhook_flow(flask_app)
    print()
    if FAIL:
        print(f'{len(FAIL)} check(s) failed: ' + '; '.join(FAIL))
        sys.exit(1)
    print(f'All {len(PASS)} checks passed.')
