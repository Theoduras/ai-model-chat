"""Regression tests for the Reddit platform.

Everything here runs against the stub transports: no session, no network. What
is checked is the part that cannot be checked against a real account safely --
that the S3 lease is waited on before a submission is made, that a fan-out
writes one row per subreddit rather than one post four times, and above all the
two carve-outs: a public comment never carries a link, and a group chat never
reaches the reply round.

Run with: python test_reddit.py
"""
import base64
import json
import os
import threading

os.environ.setdefault('GEMINI_API_KEY', 'test')
for _off in ('DISCORD_WORKER', 'DISCORD_AUTOSTART', 'REDDIT_WORKER',
             'REDDIT_AUTOSTART', 'GROWTH_QUEUE_WORKER', 'X_WORKER',
             'TELEGRAM_WORKER'):
    os.environ.setdefault(_off, '0')
os.environ.setdefault('SECRET_KEY', 'test-secret-for-reddit')

import app
import growth
import reddit_chat as RC
import reddit_rest as RR
import reddit_stub as RS

FAILURES = []
_PRISTINE = {k: v for k, v in vars(app).items() if callable(v)}


def restore():
    for k, v in _PRISTINE.items():
        setattr(app, k, v)


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  ' + str(detail)) if not ok else ''))
    if not ok:
        FAILURES.append(name)


_PNG = base64.b64encode(b'not really a png, just bytes').decode()
_IMAGE_URL = f'data:image/png;base64,{_PNG}'
_VIDEO_URL = f'data:video/mp4;base64,{_PNG}'
_SESSION = {'cookie': 'reddit_session=abc', 'bearer': 'tok', 'username': 'lilith',
            'user_id': 't2_abc'}


def _connect(fake, subs=(('gonewild', 'f1'),)):
    app._rd_session = lambda p: _SESSION
    app._rd_rest = lambda p: fake
    app._rd_account = lambda p: {'username': 'lilith'}
    app._rd_subs = lambda p: [{'sub': s, 'flair': f, 'flair_text': '', 'nsfw': True}
                              for s, f in subs]


# ── Posting ──────────────────────────────────────────────────────────────────

def test_a_submission_needs_somewhere_to_go():
    fake = RS.FakeRest()
    _connect(fake)
    for bad, word in (({'sub': '', 'kind': 'image'}, 'subreddit'),
                      ({'sub': 'gonewild', 'kind': 'gallery'}, 'kind')):
        threw = ''
        try:
            app._rd_submit('lilly', bad['sub'], bad['kind'], 'a title', b'x', 'image/png')
        except ValueError as e:
            threw = str(e).lower()
        check(f'a post with no {word} is refused up front', word in threw, threw)

    threw = ''
    try:
        app._rd_submit('lilly', 'gonewild', 'image', '   ', b'x', 'image/png')
    except ValueError as e:
        threw = str(e).lower()
    check('a post with no title is refused rather than sent blank', 'title' in threw, threw)

    threw = ''
    try:
        app._rd_submit('lilly', 'gonewild', 'image', 'a title')
    except ValueError as e:
        threw = str(e).lower()
    check('an image post with nothing attached is refused', 'photo' in threw, threw)


def test_the_flair_comes_from_the_subreddit_she_set_up():
    fake = RS.FakeRest()
    _connect(fake, subs=(('gonewild', 'f1'), ('realgirls', 'f9')))
    app._rd_submit('lilly', 'gonewild', 'image', 'a title', b'x', 'image/png')
    check('a post carries the flair that subreddit was given, not a blank one',
          fake.posted[-1]['flair'] == 'f1', fake.posted[-1])
    app._rd_submit('lilly', 'realgirls', 'image', 'a title', b'x', 'image/png')
    check('and each subreddit gets its own', fake.posted[-1]['flair'] == 'f9')
    app._rd_submit('lilly', 'gonewild', 'image', 'a title', b'x', 'image/png', flair='f2')
    check('an explicit flair wins over the stored one', fake.posted[-1]['flair'] == 'f2')


def test_a_blank_title_is_written_not_left_empty():
    fake = RS.FakeRest()
    _connect(fake)
    asked = []
    app._rd_title = lambda p, sub, brief: (asked.append((sub, brief)) or 'a title she wrote')
    out = app._rd_post_now('lilly', 'gonewild', 'image', _IMAGE_URL, '  ', 'the gym today')
    check('an empty title is generated rather than posted blank',
          out['title'] == 'a title she wrote', out)
    check('the optional brief reaches the generator',
          asked == [('gonewild', 'the gym today')], asked)
    out = app._rd_post_now('lilly', 'gonewild', 'image', _IMAGE_URL, 'exactly this', '')
    check('a title the creator wrote is used as-is', out['title'] == 'exactly this')


def test_the_media_kind_has_to_match_the_post_kind():
    fake = RS.FakeRest()
    _connect(fake)
    app._rd_title = lambda p, sub, brief: 'whatever'
    for kind, url, word in (('video', _IMAGE_URL, 'video'), ('image', _VIDEO_URL, 'photo')):
        threw = ''
        try:
            app._rd_post_now('lilly', 'gonewild', kind, url, 'a title')
        except ValueError as e:
            threw = str(e).lower()
        check(f'a {kind} post with the wrong file is refused, not silently posted',
              word in threw, threw)


def test_an_upload_waits_for_reddit_before_submitting():
    """The one ordering bug that cannot be caught by reading the code: Reddit
    validates a submission by fetching the URL, so a post made before the asset
    is processed is a post Reddit rejects."""
    seen = []

    class Traced(RR.Rest):
        def call(self, method, url, body=None, headers=None, raw=False, retries=1,
                 content_type='', bare=False):
            seen.append((method, url, bare))
            if url.endswith(RR.PATH_ASSET):
                return {'args': {'action': 'https://s3.example/up',
                                 'fields': [{'name': 'key', 'value': 'k1'}]},
                        'asset': {'asset_id': 'a1'}}
            if '/api/media/asset/' in url:
                return {'processing_state': 'complete'}
            if url.startswith('https://s3.example'):
                return {}
            return {'json': {'errors': [], 'data': {'name': 't3_1'}}}

    rest = Traced(dict(_SESSION))
    rest.submit_image('gonewild', 'a title', b'bytes', 'image/png', 'f1')
    order = [u for _, u, _b in seen]
    lease = next(i for i, u in enumerate(order) if u.endswith(RR.PATH_ASSET))
    s3 = next(i for i, u in enumerate(order) if u.startswith('https://s3.example'))
    wait = next(i for i, u in enumerate(order) if '/api/media/asset/' in u)
    submit = next(i for i, u in enumerate(order) if u.endswith(RR.PATH_SUBMIT))
    check('the lease comes first, then S3, then the wait, then the submission',
          lease < s3 < wait < submit, order)
    check('her Reddit credentials never travel to Amazon',
          seen[s3][2] is True and seen[submit][2] is False, seen)

    class Failing(Traced):
        def call(self, method, url, body=None, headers=None, raw=False, retries=1,
                 content_type='', bare=False):
            if '/api/media/asset/' in url:
                return {'processing_state': 'failed'}
            return Traced.call(self, method, url, body, headers, raw, retries,
                               content_type, bare)

    threw = False
    try:
        Failing(dict(_SESSION)).submit_image('gonewild', 'a title', b'b', 'image/png')
    except RR.RedditApiError:
        threw = True
    check('a file Reddit could not process never becomes a submission', threw)


def test_a_refusal_wearing_a_200_is_still_a_refusal():
    """Reddit answers a rejected submission with HTTP 200 and the reason inside
    the body, so a 2xx on its own is not success."""
    why = RR._api_errors({'json': {'errors': [['NO_FLAIR', 'You must select a flair']]}})
    check('the reason is read out of the envelope', 'flair' in why.lower(), why)
    check('a clean answer reads as clean', RR._api_errors({'json': {'errors': []}}) == '')

    class Refusing(RR.Rest):
        def call(self, *a, **kw):
            return {'json': {'errors': [['SUBREDDIT_NOTALLOWED', 'you are banned']]}}

    threw = ''
    try:
        Refusing(dict(_SESSION)).submit_text('gonewild', 'a title', 'body')
    except RR.RedditApiError as e:
        threw = str(e)
    check('and it is raised rather than reported as posted', 'banned' in threw, threw)


# ── Comments: the carve-out ──────────────────────────────────────────────────

def test_a_public_comment_never_carries_a_link():
    fake = RS.FakeRest()
    _connect(fake)
    fake.threads = {('p1', 'gonewild'): [
        {'id': 'c1', 'name': 't1_c1', 'author': 'fan', 'body': 'you are gorgeous'},
        {'id': 'c2', 'name': 't1_c2', 'author': 'lilith', 'body': 'thanks!'},
        {'id': 'c3', 'name': 't1_c3', 'author': 'AutoModerator', 'body': 'rules'},
    ]}
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    app._get_setting = lambda k, d=None: store.get(k, d)
    store = {'reddit_replies_lilly': json.dumps({'enabled': True, 'own_posts': True,
                                                 'mentions': False})}
    said = []
    app._persona_text = lambda p, instruction, **kw: (
        said.append(instruction) or 'that is very kind of you')
    app._plat_trace = lambda *a, **kw: None

    n = app._rd_comment_round('lilly')
    check('one real comment is answered', n == 1, fake.comments)
    check('and it answers that comment, not the post', fake.comments[0]['on'] == 't1_c1')
    check('her own reply is never answered again',
          all(c['on'] != 't1_c2' for c in fake.comments), fake.comments)
    check('AutoModerator is left alone',
          all(c['on'] != 't1_c3' for c in fake.comments), fake.comments)
    check('the instruction forbids a link, a page and a DM outright',
          all(w in said[0].lower() for w in ('link', 'subscribing', 'dm')), said[:1])
    check('nothing that went out contains a URL',
          not any('http' in c['text'] for c in fake.comments), fake.comments)

    fake.comments = []
    n = app._rd_comment_round('lilly')
    check('a second round answers nothing twice', n == 0 and not fake.comments)


def test_comment_replies_stay_off_until_switched_on():
    fake = RS.FakeRest()
    _connect(fake)
    store = {}
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    fake.threads = {('p1', 'gonewild'): [
        {'id': 'c1', 'name': 't1_c1', 'author': 'fan', 'body': 'hi'}]}
    check('nothing is answered while the switch is off',
          app._rd_comment_round('lilly') == 0 and not fake.comments)


# ── Chat: the other carve-out ────────────────────────────────────────────────

def test_a_room_is_never_a_fan():
    check('a two-member channel is a DM', RC._is_dm({'member_count': 2}))
    check('anything larger is a room, and is dropped',
          not RC._is_dm({'member_count': 3}) and not RC._is_dm({'member_count': 40}))
    check('member_count is taken from the member list when Reddit omits it',
          RC._is_dm({'members': [{'user_id': 'a'}, {'user_id': 'b'}]})
          and not RC._is_dm({'members': [{'user_id': str(i)} for i in range(5)]}))

    runner = RC.Runner.__new__(RC.Runner)
    runner.persona = 'lilly'
    runner.me_id = 'me'
    runner.session = {}
    runner._seen = RC.deque(maxlen=10)
    runner.gates = {'dm': {}}
    woken = []
    runner.on_dm = lambda p, f: woken.append(f)
    runner._learn_channel = lambda url: ''      # a room, so it names no fan
    RC._chats.pop('lilly', None)
    runner._on_message({'msg_id': '1', 'channel_url': 'room', 'message': 'hello all',
                        'user': {'user_id': 'someone'}, 'ts': 1})
    check('a message from a channel that is not a DM never wakes the round',
          woken == [], woken)

    runner._learn_channel = lambda url: RC._chat_book('lilly').setdefault(
        'fan1', {'fan_id': 'fan1', 'channel_url': url, 'handle': 'fan',
                 'last_at': 0.0, 'typing_at': 0.0}) and 'fan1'
    runner._on_message({'msg_id': '2', 'channel_url': 'dm', 'message': 'hey you',
                        'user': {'user_id': 'fan1'}, 'ts': 2})
    check('a DM does', woken == ['fan1'], woken)

    runner._on_message({'msg_id': '2', 'channel_url': 'dm', 'message': 'hey you',
                        'user': {'user_id': 'fan1'}, 'ts': 2})
    check('and the same message twice only wakes it once', woken == ['fan1'], woken)

    runner._on_message({'msg_id': '3', 'channel_url': 'dm', 'message': 'my own words',
                        'user': {'user_id': 'me'}, 'ts': 3})
    check('her own message coming back over the socket is not a fan writing in',
          woken == ['fan1'], woken)


def test_the_adapter_reads_the_shape_the_round_expects():
    plat = app.PLAT_REDDIT
    check('Reddit is registered as a platform', app.PLATFORMS.get('reddit') is plat)
    check('its fans are keyed apart from every other platform',
          plat.fan_key('7') == 'rd:7'
          and app._platform_of_fan('rd:7') == 'reddit')
    check('DMs run the funnel', plat.has_funnels and plat.has_winback)
    check('but there is no paywall to verify a sale against, so no PPV engine',
          not plat.has_ppv and plat.has_cta)
    check('its settings are namespaced', plat.k('cta', 'lilly') == 'reddit_cta_lilly')

    msg = {'id': '9', 'content': 'hello', 'ts': 1700000000000, 'out': False}
    check('a message reads back through the adapter', plat.text_of(msg) == 'hello'
          and plat.msg_id(msg) == '9')
    check('and its time is handed over as ISO, not Sendbird milliseconds',
          plat.msg_time(msg).startswith('2023-11-'), plat.msg_time(msg))
    check('direction is read, not inferred',
          plat.direction(msg, '', '') == 'in'
          and plat.direction(dict(msg, out=True), '', '') == 'out')


def test_a_missing_chat_token_is_posting_only_not_broken():
    app._rd_session = lambda p: {'cookie': 'c'}     # signed in, no bearer
    why = app.PLAT_REDDIT.reachable('lilly')
    check('a sign-in with no chat token says so plainly',
          'chat' in why.lower() and 'post' in why.lower(), why)
    check('and she still counts as connected, because posting works',
          app.PLAT_REDDIT.connected('lilly'))
    check('no socket is started for a session that cannot reach chat',
          app._rd_connect('lilly') is None)


# ── The session ──────────────────────────────────────────────────────────────

def test_the_session_round_trips_through_encryption():
    store = {}
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)

    session = {'cookie': 'reddit_session=abc', 'bearer': 'secret-token',
               'modhash': 'm', 'chat': {'app_id': 'A'}, 'user_agent': 'test-ua',
               'user_id': 't2_abc', 'username': 'lilith'}
    app._rd_set_session('lilly', session)
    check('nothing readable lands in the setting store',
          'reddit_session=abc' not in json.dumps(store)
          and 'secret-token' not in json.dumps(store), store)
    held = app._rd_session('lilly')
    check('and it comes back exactly as it went in', held == session, held)
    check('the account note records that chat is reachable',
          app._rd_account('lilly').get('chat_ready') is True)

    app._rd_set_session('lilly', {})
    check('clearing it leaves nothing to decrypt', app._rd_session('lilly') == {})
    check('the app keys Reddit accounts apart from the other platforms',
          app._rd_account_id('lilly') == 'rd_lilly')


def test_signing_in_through_the_browser():
    """The sign-in relay, driven as Reddit. Nothing here opens a browser."""
    import of_connect

    check('reddit is a site the relay knows', 'reddit' in of_connect.SITES)
    check('and it opens Reddit, not another platform',
          of_connect.SITES['reddit']['url'].startswith('https://www.reddit.com'))

    made = of_connect.Attempt.__new__(of_connect.Attempt)
    made.site = 'reddit'
    made._site = of_connect.SITES['reddit']
    made.proxy = ''
    made.account = 'rd_lilly'
    made.capture_note = ''
    made.result = {}
    made.probes = 0
    made.state = 'signin'
    made.page_url = ''
    made.cookie_names = []
    made.user_agent = ''
    made._done = threading.Event()
    made._pending_session = None

    class Context:
        def __init__(self, cookies):
            self._cookies = cookies

        def cookies(self, origin):
            return self._cookies

    class Page:
        def __init__(self, who):
            self.who = who

        def evaluate(self, js, args=None):
            if 'navigator.userAgent' in js:
                return 'Mozilla/5.0 test'
            return self.who

    made._capture_reddit(Page(None), Context([{'name': 'loid', 'value': 'x'}]))
    check('no session cookie yet means the sign-in is not finished',
          made.state == 'signin' and made.capture_note == 'awaiting_cookies')

    cookies = [{'name': 'reddit_session', 'value': 'abc'}, {'name': 'loid', 'value': 'x'}]
    who = {'data': {'name': 'lilith', 'id': 't2_abc', 'modhash': 'm'}}
    made._capture_reddit(Page(who), Context(cookies))
    check('a signed-in cookie with no chat token still finishes, unverified',
          made.state == 'connected' and made.capture_note == 'unverified')
    held = made._pending_session
    check('the cookie header carries every cookie the browser held',
          'reddit_session=abc' in (held or {}).get('cookie', ''), held)

    made.capture_note = ''
    made.state = 'signin'
    made._pending_session = None
    made._rd_bearer = 'a-real-token'
    made._rd_chat = {'app_id': 'A', 'url': 'wss://chat'}
    made._capture_reddit(Page(who), Context(cookies))
    check('a capture that saw the chat handshake is marked verified',
          made.capture_note == 'captured')
    held = made._pending_session
    check('and carries the bearer and the handshake the page really used',
          (held or {}).get('bearer') == 'a-real-token'
          and (held or {}).get('chat', {}).get('app_id') == 'A', held)
    check('claim hands it over exactly once',
          of_connect.claim(made) == held and of_connect.claim(made) is None)


# ── The planner ──────────────────────────────────────────────────────────────

def test_one_planned_post_fans_out_to_one_row_per_subreddit():
    app._rd_subs = lambda p: [{'sub': 'gonewild', 'flair': 'f1', 'flair_text': '', 'nsfw': True},
                              {'sub': 'realgirls', 'flair': 'f9', 'flair_text': '', 'nsfw': True}]
    data = {'text': 'a shared title', 'reddit': {
        'kind': 'image', 'stagger_min': 30,
        'subs': [{'sub': 'gonewild', 'title': 'one title'},
                 {'sub': 'r/realgirls'}]}}
    targets, why = app._growth_rd_targets('lilly', 'reddit', data, ['img'])
    check('every subreddit becomes its own target', len(targets) == 2 and not why, why)
    check('each carries the flair that subreddit needs',
          [t['flair'] for t in targets] == ['f1', 'f9'], targets)
    check('a per-subreddit title is kept, and the shared one fills the rest',
          [t['title'] for t in targets] == ['one title', 'a shared title'], targets)
    check('an r/ prefix is accepted and stripped', targets[1]['sub'] == 'realgirls')
    check('the stagger is read from the request', app._growth_rd_stagger(data) == 30)
    check('and defaults rather than firing them all at once',
          app._growth_rd_stagger({'reddit': {}}) == 20)

    _, why = app._growth_rd_targets('lilly', 'reddit', {'text': 't', 'reddit': {'subs': []}}, ['img'])
    check('no subreddit at all is refused at the queue', 'subreddit' in why.lower(), why)

    _, why = app._growth_rd_targets(
        'lilly', 'reddit', {'text': 't', 'reddit': {'kind': 'image', 'subs': [{'sub': 'a'}]}}, [])
    check('an image post with nothing attached is refused at the queue',
          'file' in why.lower(), why)

    _, why = app._growth_rd_targets(
        'lilly', 'reddit',
        {'text': 't', 'reddit': {'kind': 'image', 'subs': [{'sub': 'a'}]}}, ['i1', 'i2'])
    check('two files on one submission is refused', 'one file' in why.lower(), why)

    targets, why = app._growth_rd_targets('lilly', 'x', data, ['img'])
    check('no other channel carries a subreddit at all', (targets, why) == ([], ''))

    check('reddit is a channel the planner publishes to itself',
          'reddit' in growth.PUBLISHABLE
          and growth.queue_status_for('reddit') == 'queued')


def test_the_queue_hands_the_target_to_the_publisher():
    fake = RS.FakeRest()
    _connect(fake)
    app._content_register_add = lambda *a, **kw: None
    app._media_row = lambda persona, mid: {'id': mid, 'kind': 'image'}
    app._media_bytes = lambda row: (b'bytes', 'image/png')
    app._fv_media_id = lambda one: ''
    app._growth_media_check_list = lambda *a, **kw: (['img'], '')

    posted_id = app._growth_publish('lilly', 'reddit', 'a title', media_ids=['img'],
                                    rd_sub='gonewild', rd_flair='f1', rd_kind='image')
    check('the row posts to its own subreddit with its own flair',
          fake.posted[-1]['sub'] == 'gonewild' and fake.posted[-1]['flair'] == 'f1',
          fake.posted[-1])
    check('and the thing id comes back for the queue to store',
          posted_id.startswith('t3_'), posted_id)

    threw = ''
    try:
        app._growth_publish('lilly', 'reddit', 'a title', media_ids=['img'])
    except RuntimeError as e:
        threw = str(e).lower()
    check('a Reddit row with no subreddit on it never posts', 'subreddit' in threw, threw)


def test_each_persona_dials_reddit_from_her_own_address():
    """Reddit blocks this server at the login page outright, so the proxy is
    what makes a sign-in possible at all -- and sharing one across personas
    means one ban takes every model with it."""
    store = {}
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    os.environ.pop('REDDIT_PROXY_TEMPLATE', None)

    check('with nothing set she has no way out at all',
          app._rd_proxy_for('lilly') == '')

    app._rd_set_proxy('lilly', 'http://u1:pw-one-secret@gw.example:823')
    app._rd_set_proxy('nova', 'http://u2:pw-two-secret@gw.example:823')
    check('each persona keeps her own credentials, not a shared pair',
          app._rd_proxy_for('lilly') == 'http://u1:pw-one-secret@gw.example:823'
          and app._rd_proxy_for('nova') == 'http://u2:pw-two-secret@gw.example:823')
    check('and neither password is readable in the setting store',
          'pw-one-secret' not in json.dumps(store)
          and 'pw-two-secret' not in json.dumps(store), store)
    check('the console is shown the host, never the password',
          app._rd_proxy_shown('lilly') == 'u1@gw.example:823'
          and 'pw-one-secret' not in app._rd_proxy_shown('lilly'))

    os.environ['REDDIT_PROXY_TEMPLATE'] = 'http://shared-{country}-{session}:pw@pool:1'
    check('her own still wins over the shared pool',
          app._rd_proxy_for('lilly') == 'http://u1:pw-one-secret@gw.example:823')
    check('a persona without one falls back to the pool, keyed to her',
          app._rd_proxy_for('zara') == 'http://shared-nl-zara:pw@pool:1')
    app._rd_set_proxy('lilly', '')
    check('clearing hers drops her back to the pool rather than to no proxy',
          app._rd_proxy_for('lilly') == 'http://shared-nl-lilly:pw@pool:1')
    os.environ.pop('REDDIT_PROXY_TEMPLATE', None)

    app._rd_set_proxy('lilly', 'http://u1:pw-one-secret@gw.example:823')
    app._rd_set_session('lilly', dict(_SESSION, proxy='http://old:p@gone:1'))
    check('the transport is handed the proxy, so a call cannot leave by another door',
          RR.Rest(app._rd_session('lilly')).proxy == 'http://old:p@gone:1')


def test_the_routes_exist():
    rules = {str(r) for r in app.app.url_map.iter_rules()}
    for path in ('/api/reddit/status', '/api/reddit/connect', '/api/reddit/subreddits',
                 '/api/reddit/flairs', '/api/reddit/replies', '/api/reddit/replies-run',
                 '/api/reddit/auto', '/api/reddit/dm-send', '/api/reddit/trace',
                 '/api/reddit/connect/browser', '/api/reddit/connect/frame',
                 '/api/reddit/connect/input', '/api/reddit/connect/cancel',
                 '/api/reddit/post-now', '/api/reddit/proxy',
                 '/api/reddit/proxy/test',
                 '/reddit', '/reddit/connect'):
        check(f'{path} is served', path in rules)


if __name__ == '__main__':
    for fn in (test_a_submission_needs_somewhere_to_go,
               test_the_flair_comes_from_the_subreddit_she_set_up,
               test_a_blank_title_is_written_not_left_empty,
               test_the_media_kind_has_to_match_the_post_kind,
               test_an_upload_waits_for_reddit_before_submitting,
               test_a_refusal_wearing_a_200_is_still_a_refusal,
               test_a_public_comment_never_carries_a_link,
               test_comment_replies_stay_off_until_switched_on,
               test_a_room_is_never_a_fan,
               test_the_adapter_reads_the_shape_the_round_expects,
               test_a_missing_chat_token_is_posting_only_not_broken,
               test_the_session_round_trips_through_encryption,
               test_each_persona_dials_reddit_from_her_own_address,
               test_signing_in_through_the_browser,
               test_one_planned_post_fans_out_to_one_row_per_subreddit,
               test_the_queue_hands_the_target_to_the_publisher,
               test_the_routes_exist):
        print()
        print(fn.__name__.replace('test_', '').replace('_', ' '))
        try:
            fn()
        finally:
            restore()
    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILED: {FAILURES}')
        raise SystemExit(1)
    print('all reddit tests passed')
