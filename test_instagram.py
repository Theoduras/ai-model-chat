"""Regression tests for the Instagram poster.

Everything here runs against the stub transport: no session, no network. What
is checked is the part that cannot be checked against a real account safely —
that a Reel refuses to go out without a video, that a blank caption is
written rather than posted empty, and that the captured session round-trips
through encryption the same way Discord's token does.

Run with: python test_instagram.py
"""
import base64
import json
import os
import threading

os.environ.setdefault('GEMINI_API_KEY', 'test')
os.environ.setdefault('DISCORD_WORKER', '0')
os.environ.setdefault('DISCORD_AUTOSTART', '0')
os.environ.setdefault('SECRET_KEY', 'test-secret-for-instagram')

import app
import instagram_rest as IR
import instagram_stub as IS

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


def test_media_bytes_are_read_and_typed():
    raw, kind = app._ig_media_bytes(_IMAGE_URL)
    check('a photo decodes to bytes and is read as an image',
          kind == 'image' and isinstance(raw, bytes))
    raw, kind = app._ig_media_bytes(_VIDEO_URL)
    check('a video is read as a video', kind == 'video')
    threw = False
    try:
        app._ig_media_bytes('not a data url')
    except ValueError:
        threw = True
    check('anything that is not a data URL is refused', threw)


def test_a_reel_needs_a_video():
    session = {'cookie': 'sessionid=abc; csrftoken=xyz', 'csrftoken': 'xyz'}
    app._ig_session = lambda p: session
    app._ig_caption = lambda p, kind, brief: 'whatever'
    fake = IS.FakeRest()
    app.IR = type('M', (), {'Rest': lambda s: fake, 'InstagramApiError': IR.InstagramApiError})

    threw = False
    try:
        app._ig_post_now('lilly', 'reel', _IMAGE_URL, '', '')
    except ValueError as e:
        threw = 'video' in str(e).lower()
    check('a reel posted with a photo is refused rather than silently posted',
          threw)

    threw = False
    try:
        app._ig_post_now('lilly', 'reel', _VIDEO_URL, '', '')
    except ValueError as e:
        threw = 'length' in str(e).lower()
    check('a video with no duration read off it is refused, not posted with a zero length',
          threw)

    result = app._ig_post_now('lilly', 'reel', _VIDEO_URL, '', '', 1080, 1920, 8000)
    check('a reel with a video and its real dimensions goes through', result['kind'] == 'reel')
    check('it lands on the reel path, not the feed or story one',
          fake.posted[-1]['target'] == 'reel', fake.posted)
    check('the real width/height/duration reach the transport, not zeros',
          (fake.posted[-1]['width'], fake.posted[-1]['height'], fake.posted[-1]['duration_ms'])
          == (1080, 1920, 8000), fake.posted[-1])


def test_kinds_route_to_the_right_call():
    session = {'cookie': 'sessionid=abc; csrftoken=xyz', 'csrftoken': 'xyz'}
    app._ig_session = lambda p: session
    app._ig_caption = lambda p, kind, brief: 'caption for ' + kind
    fake = IS.FakeRest()
    app.IR = type('M', (), {'Rest': lambda s: fake, 'InstagramApiError': IR.InstagramApiError})

    app._ig_post_now('lilly', 'post', _IMAGE_URL, '', '')
    app._ig_post_now('lilly', 'story', _IMAGE_URL, '', '')
    check('a feed post and a story land on their own endpoints',
          [r['target'] for r in fake.posted] == ['post', 'story'], fake.posted)


def test_a_blank_caption_is_written_not_left_empty():
    session = {'cookie': 'sessionid=abc; csrftoken=xyz', 'csrftoken': 'xyz'}
    app._ig_session = lambda p: session
    asked = []
    app._ig_caption = lambda p, kind, brief: (asked.append((kind, brief)) or 'a caption she wrote')
    fake = IS.FakeRest()
    app.IR = type('M', (), {'Rest': lambda s: fake, 'InstagramApiError': IR.InstagramApiError})

    result = app._ig_post_now('lilly', 'post', _IMAGE_URL, '   ', 'the gym today')
    check('an empty caption is generated rather than posted blank',
          result['caption'] == 'a caption she wrote')
    check('the optional brief reaches the generator',
          asked == [('post', 'the gym today')], asked)

    result = app._ig_post_now('lilly', 'post', _IMAGE_URL, 'exactly this', '')
    check('a caption the creator wrote is used as-is, not regenerated',
          result['caption'] == 'exactly this')


def test_not_connected_refuses_before_touching_the_network():
    app._ig_session = lambda p: {}
    threw = False
    try:
        app._ig_post_now('lilly', 'post', _IMAGE_URL, 'hi', '')
    except ValueError as e:
        threw = 'not connected' in str(e).lower()
    check('posting with no session stored is refused up front', threw)


def test_the_session_round_trips_through_encryption():
    store = {}
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)

    session = {'cookie': 'sessionid=abc; csrftoken=xyz', 'csrftoken': 'xyz',
               'app_id': '123', 'user_agent': 'test-ua',
               'user_id': '42', 'username': 'lilith'}
    app._ig_set_session('lilly', session)
    check('nothing readable lands in the setting store',
          'sessionid=abc' not in json.dumps(store), store)
    held = app._ig_session('lilly')
    check('and it comes back exactly as it went in', held == session, held)

    app._ig_set_session('lilly', {})
    check('clearing it leaves nothing to decrypt',
          app._ig_session('lilly') == {})

    check('the app keys Instagram accounts apart from Discord and OnlyFans',
          app._ig_account_id('lilly') == 'ig_lilly')


def test_signing_in_through_the_browser():
    """The sign-in relay, driven as Instagram rather than as Discord or
    OnlyFans. Nothing here opens a browser."""
    import of_connect

    check('instagram is a site the relay knows', 'instagram' in of_connect.SITES)
    check('and it opens Instagram, not Discord or OnlyFans',
          of_connect.SITES['instagram']['url'].startswith('https://www.instagram.com'))

    made = of_connect.Attempt.__new__(of_connect.Attempt)
    made.site = 'instagram'
    made._site = of_connect.SITES['instagram']
    made.proxy = ''
    made.account = 'ig_lilly'
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

    no_session = Context([{'name': 'csrftoken', 'value': 'xyz'}])
    made._capture_instagram(Page(None), no_session)
    check('no sessionid cookie yet means the sign-in is not finished',
          made.state == 'signin' and made.capture_note == 'awaiting_cookies')

    cookies = [{'name': 'sessionid', 'value': 'abc'}, {'name': 'csrftoken', 'value': 'xyz'},
               {'name': 'ds_user_id', 'value': '42'}]
    made._capture_instagram(Page({'user': {'pk': '42', 'username': 'lilith'}}), Context(cookies))
    check('a verified fetch finishes the sign-in', made.state == 'connected')
    check('and marks the capture as verified', made.capture_note == 'captured')
    held = made._pending_session
    check('the cookie header carries every cookie the browser held',
          'sessionid=abc' in (held or {}).get('cookie', '') and
          'csrftoken=xyz' in (held or {}).get('cookie', ''), held)
    check('the csrf token is kept on its own too, for the header every write needs',
          (held or {}).get('csrftoken') == 'xyz')
    check('claim hands it over exactly once',
          of_connect.claim(made) == held and of_connect.claim(made) is None)

    made.capture_note = ''
    made.state = 'signin'
    made._capture_instagram(Page(None), Context(cookies))
    check('a signed-in cookie with no working fetch still finishes, unverified',
          made.state == 'connected' and made.capture_note == 'unverified')


def test_the_routes_exist():
    rules = {str(r) for r in app.app.url_map.iter_rules()}
    for path in ('/api/instagram/status', '/api/instagram/connect',
                 '/api/instagram/connect/browser', '/api/instagram/connect/frame',
                 '/api/instagram/connect/input', '/api/instagram/connect/cancel',
                 '/api/instagram/post-now', '/instagram', '/instagram/connect'):
        check('%s is registered' % path, path in rules)


def test_the_console_has_what_it_draws():
    page = open('instagram.html').read()
    for sec in ('sec-account', 'sec-posting'):
        check('%s is a panel on the page' % sec, 'id="%s"' % sec in page)
    check('the post-now call reaches the posting endpoint',
          '/api/instagram/post-now' in page)
    check('the connect popup reaches the same relay OnlyFans and Discord use',
          '/instagram/connect?site=instagram' in page)


if __name__ == '__main__':
    for fn in (test_media_bytes_are_read_and_typed,
               test_a_reel_needs_a_video,
               test_kinds_route_to_the_right_call,
               test_a_blank_caption_is_written_not_left_empty,
               test_not_connected_refuses_before_touching_the_network,
               test_the_session_round_trips_through_encryption,
               test_signing_in_through_the_browser,
               test_the_routes_exist,
               test_the_console_has_what_it_draws):
        restore()
        try:
            fn()
        except Exception as e:
            check(fn.__name__, False, repr(e))
    restore()
    print(f'\n{len(FAILURES)} failing')
    if FAILURES:
        raise SystemExit(1)
