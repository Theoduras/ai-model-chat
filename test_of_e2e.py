"""End to end, on the real pieces.

A real Chromium is driven through the real connect routes against a local page
standing in for onlyfans.com, then a watcher's event is walked through the real
webhook handler. What is faked is only OnlyFans itself.

Run directly: ONLYFANS_TRANSPORT=direct python3 test_of_e2e.py
"""
import json
import os
import pathlib
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


# A page that behaves like the part of OnlyFans we depend on: a form that sets
# the session cookies and the device token, and an endpoint that confirms them.
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
const realFetch=window.fetch;
window.fetch=function(url,opts){
  if(String(url).includes('/api2/v2/users/me')){
    const signed=document.cookie.includes('sess=');
    return Promise.resolve({ok:signed, json:()=>Promise.resolve(
      signed?{id:4242,username:'e2e_creator',name:'E2E'}:null)});
  }
  return realFetch(url,opts);
};
</script>"""


def _serve(root):
    """A throwaway web server for the stand-in OnlyFans page."""
    import functools
    import http.server
    import threading
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(root))
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd.server_address[1]


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
    if not os.getenv('PLAYWRIGHT_CHROMIUM'):
        for guess in sorted(pathlib.Path('/opt/pw-browsers').glob('chromium-*/chrome-linux/chrome')):
            of_connect.BROWSER_PATH = str(guess)
            break

    attempt = of_connect.start('e2e', 'of_e2e')
    check('a hosted browser opens', attempt is not None)

    frame = ''
    for _ in range(60):
        frame = attempt.snapshot()
        if frame:
            break
        time.sleep(0.5)
    check('it streams a frame of the page', frame.startswith('data:image/jpeg;base64,'))
    check('and nothing is stored before she signs in', of_session.get('of_e2e') == {})

    # Type an email, then press the button — the same input path the panel uses.
    attempt.act('click', x=300, y=130)
    attempt.act('type', text='creator@example.com')
    time.sleep(1.5)
    attempt.act('click', x=300, y=240)

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


if __name__ == '__main__':
    flask_app = browser_flow()
    webhook_flow(flask_app)
    print()
    if FAIL:
        print(f'{len(FAIL)} check(s) failed: ' + '; '.join(FAIL))
        sys.exit(1)
    print(f'All {len(PASS)} checks passed.')
