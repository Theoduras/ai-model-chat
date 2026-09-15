"""End-to-end test of the Fanvue webhook, over real HTTP.

Stands up two servers: the app itself, and a stand-in for Fanvue. The app
subscribes against the stand-in exactly as it would against Fanvue, then signed
deliveries are posted back to the app and we check what reached the database —
the sale settled, the funnel credited, the churn recorded, and the bandit
judging the funnel on both.

Needs no API key and no Fanvue account; nothing leaves the machine.
Run with: python test_webhook_e2e.py, or through pytest, which runs the same
script in a subprocess — see test_the_webhook_carries_a_sale_end_to_end below.
"""
import datetime, hashlib, hmac, http.server, json, os, shutil, socket, subprocess, sys, tempfile, threading, time, urllib.request

sys.path.insert(0, '/home/user/ai-model-chat')

def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p

MADE = {}
APP_PORT = 0
fails = []

class Fanvue(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body))); self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path == '/webhooks/subscriptions':
            return self._send(200, {'data': list(MADE.values())})
        self._send(404, {})
    def do_DELETE(self):
        MADE.pop(self.path.rsplit('/', 1)[-1], None); self._send(204, {})
    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(n) or b'{}')
        if self.path == '/webhooks/subscriptions':
            MADE['wh-1'] = {'id': 'wh-1', 'url': body['url'], 'events': body['events'],
                            'createdAt': '2026-01-01T00:00:00Z'}
            return self._send(201, {'id': 'wh-1', 'signingSecret': 'live-secret-abc'})
        self._send(404, {})

def check(name, ok, detail=''):
    print(('  ok   ' if ok else '  FAIL ') + name + ('' if ok else '  ' + str(detail)))
    if not ok: fails.append(name)

def deliver(event_type, data, secret='live-secret-abc', eid=None):
    body = json.dumps({'id': eid or ('ev-' + str(time.time())), 'type': event_type,
                       'data': data}).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), ts.encode() + b'.' + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(f'http://127.0.0.1:{APP_PORT}/webhooks/fanvue', data=body,
                                 method='POST', headers={
                                     'Content-Type': 'application/json',
                                     'X-Fanvue-Signature': f't={ts},v0={sig}'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')

def main():
    global APP_PORT
    # A database of its own. The run writes a sale, a churn and a posterior and
    # then asserts their totals, so a leftover data.db from a previous run makes
    # it fail on 4500 cents where it wants 1500 -- it was only ever green on a
    # directory nobody had run it in.
    data_dir = tempfile.mkdtemp(prefix='webhook-e2e-')
    os.environ['DATA_DIR'] = data_dir
    os.environ['GEMINI_API_KEY'] = 'test'

    FV_PORT, APP_PORT = free_port(), free_port()
    os.environ['PUBLIC_BASE_URL'] = 'https://ai-model-chat-dev.example.run.app'

    import app
    from db import (SessionLocal, init_db, FanProfile, FanEvent, PpvDrop,
                    FunnelAssignment, FunnelPosterior)
    init_db()
    app.FANVUE_API_BASE = f'http://127.0.0.1:{FV_PORT}'

    threading.Thread(target=lambda: http.server.HTTPServer(('127.0.0.1', FV_PORT), Fanvue)
                     .serve_forever(), daemon=True).start()

    # The app itself, on a real socket.
    from werkzeug.serving import make_server
    srv = make_server('127.0.0.1', APP_PORT, app.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.6)

    with app.app.app_context():
        app._set_setting('fanvue_tokens_lilly', json.dumps(
            {'access_token': 'tok', 'scope': 'openid read:self read:chat write:chat read:creator'}))
        app._set_setting('fanvue_creator_lilly', json.dumps({'uuid': 'creator-1', 'handle': 'lilly'}))
        app._set_setting('fanvue_auto_personas', json.dumps(['lilly']))
        app._set_setting('fanvue_funnels_lilly', json.dumps(
            dict(app.FV_FUNNEL_DEFAULTS, enabled=True, avg_sub_cents=1000)))

    print('1. subscribe')
    with app.app.app_context():
        res = app._fv_ensure_webhook('lilly')
    check('subscription created', res.get('ok') and res.get('created'), res)
    check('Fanvue recorded it', 'wh-1' in MADE, MADE)
    check('pointing at PUBLIC_BASE_URL',
          MADE['wh-1']['url'] == 'https://ai-model-chat-dev.example.run.app/webhooks/fanvue',
          MADE['wh-1']['url'])
    check('with the churn events',
          'creator.subscription.deactivated' in MADE['wh-1']['events'], MADE['wh-1']['events'])
    with app.app.app_context():
        check('and the minted secret stored',
              app._fv_stored_hook('lilly').get('secret') == 'live-secret-abc')

    print('2. a fan is in a funnel with an unbought drop')
    with app.app.app_context():
        a = app._fv_assignment('lilly', 'fan-9', 'joe', 'GF',
                               dict(app.FV_FUNNEL_DEFAULTS, enabled=True))
    s = SessionLocal()
    s.add(PpvDrop(persona='lilly', fan_uuid='fan-9', set_id='s1', set_name='Bar',
                  tier_index=0, price_cents=1500, media_uuids='[]', message_uuid='msg-9'))
    s.commit(); s.close()
    check('assigned', a and a['funnel'] == 'F1', a)

    print('3. a purchase arrives')
    code, out = deliver('creator.payment.succeeded',
                        {'creator': {'uuid': 'creator-1'}, 'fan': {'uuid': 'fan-9'},
                         'amount': 1500, 'id': 'FV-1'})
    check('accepted', code == 200 and out.get('ok'), (code, out))
    s = SessionLocal()
    drop = s.query(PpvDrop).filter_by(fan_uuid='fan-9').first()
    check('the drop is settled', drop.paid_at is not None and drop.invoice_id == 'FV-1', drop.paid_at)
    asg = s.get(FunnelAssignment, a['id'])
    check('revenue credited to the funnel', asg.revenue_cents == 1500, asg.revenue_cents)
    s.close()

    print('4. the same event again')
    # Fanvue guarantees at-least-once delivery, so the same sale can arrive twice.
    for _ in range(2):
        code, out = deliver('creator.payment.succeeded',
                            {'creator': {'uuid': 'creator-1'}, 'fan': {'uuid': 'fan-9'},
                             'amount': 1500, 'id': 'FV-1'}, eid='ev-dup')
    check('the replay is ignored', out.get('duplicate') is True, out)
    s = SessionLocal()
    check('and the sale is still counted once',
          s.get(FunnelAssignment, a['id']).revenue_cents == 1500,
          s.get(FunnelAssignment, a['id']).revenue_cents)
    s.close()

    print('5. the fan unsubscribes')
    code, out = deliver('creator.subscription.deactivated',
                        {'creator': {'uuid': 'creator-1'}, 'fan': {'uuid': 'fan-9'}})
    check('accepted', code == 200, (code, out))
    s = SessionLocal()
    p = s.query(FanProfile).filter_by(persona='lilly', fan_uuid='fan-9').first()
    check('churn is recorded', p is not None and p.churned_at is not None, p and p.churned_at)
    kinds = [e.kind for e in s.query(FanEvent).filter_by(fan_uuid='fan-9').all()]
    check('and logged as an event', 'unsub' in kinds, kinds)
    s.close()

    print('6. a forged delivery')
    code, out = deliver('creator.subscription.deactivated',
                        {'creator': {'uuid': 'creator-1'}, 'fan': {'uuid': 'fan-9'}},
                        secret='not-the-secret')
    check('is refused', code == 401, (code, out))

    print('7. the funnel is judged on what happened')
    s = SessionLocal()
    row = s.get(FunnelAssignment, a['id'])
    row.assigned_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=8)
    s.commit(); s.close()
    with app.app.app_context():
        app._fv_bandit_sweep('lilly', dict(app.FV_FUNNEL_DEFAULTS, enabled=True, avg_sub_cents=1000))
    s = SessionLocal()
    post = s.query(FunnelPosterior).filter_by(funnel_id='F1', variant_key='').first()
    rev = s.get(FunnelAssignment, a['id']).revenue_cents
    s.close()
    check('the posterior was updated', post is not None and post.n == 1, post and post.n)
    check('the lost fan is counted', post and post.churn_n == 1, post and post.churn_n)
    # €15 earned against a €10 sub still nets positive, so this one is a win.
    check('and the reward is revenue minus the churn penalty',
          post and post.alpha == 2 and post.beta == 1, post and (post.alpha, post.beta, rev))

    srv.shutdown()
    shutil.rmtree(data_dir, ignore_errors=True)
    print()
    print(('FAILED: ' + ', '.join(fails)) if fails else 'END TO END OK')
    return 1 if fails else 0


def test_the_webhook_carries_a_sale_end_to_end():
    """Run as a subprocess rather than in-process.

    This sets DATA_DIR and PUBLIC_BASE_URL and then imports `app`, which reads
    both at import time. In a pytest process where another module already
    imported `app`, that import is a no-op and the settings would silently not
    apply — so the script needs a process of its own, and gets one here.
    """
    r = subprocess.run([sys.executable, os.path.abspath(__file__)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr


if __name__ == '__main__':
    sys.exit(main())
