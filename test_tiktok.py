"""Regression tests for the TikTok poster.

Everything here runs against the stub transport: no session, no network. What
is checked is the part that cannot be checked against a real account safely —
that a video and a set of photos never go out as one post, that a blank
caption is written rather than posted empty, that a reply is drafted before it
is sent, and that the captured session round-trips through encryption the same
way Instagram's does.

Run with: python test_tiktok.py
"""
import base64
import json
import os
import threading

os.environ.setdefault('GEMINI_API_KEY', 'test')
os.environ.setdefault('DISCORD_WORKER', '0')
os.environ.setdefault('DISCORD_AUTOSTART', '0')
os.environ.setdefault('SECRET_KEY', 'test-secret-for-tiktok')

import app
import growth
import tiktok_rest as TR
import tiktok_stub as TS

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
_SESSION = {'cookie': 'sessionid=abc; tt_csrf_token=xyz; msToken=mmm',
            'csrftoken': 'xyz', 'sec_uid': 'sec-lilith', 'user_id': '42'}


def _stub(fake):
    app._tt_rest = lambda persona: fake
    app._tt_caption = lambda persona, brief: 'whatever'


def test_a_video_and_photos_never_ride_in_one_post():
    fake = TS.FakeRest()
    _stub(fake)

    threw = ''
    try:
        app._tt_post_now('lilly', [_VIDEO_URL, _IMAGE_URL], '')
    except ValueError as e:
        threw = str(e)
    check('a video plus a photo is refused rather than half-posted',
          'not both' in threw, threw)

    threw = ''
    try:
        app._tt_post_now('lilly', [], '')
    except ValueError as e:
        threw = str(e)
    check('a post with nothing attached is refused', 'Attach' in threw, threw)


def test_each_kind_reaches_its_own_call():
    fake = TS.FakeRest()
    _stub(fake)

    result = app._tt_post_now('lilly', [_VIDEO_URL], 'hello')
    check('one video goes out as a video post', result['kind'] == 'video', fake.posted)

    result = app._tt_post_now('lilly', [_IMAGE_URL, _IMAGE_URL], 'hello')
    check('several stills go out as one photo post', result['kind'] == 'photo', fake.posted)
    check('and every still reaches the transport, not just the first',
          fake.posted[-1]['stills'] == 2, fake.posted[-1])


def test_a_blank_caption_is_written_not_left_empty():
    fake = TS.FakeRest()
    app._tt_rest = lambda persona: fake
    app._tt_caption = lambda persona, brief: 'she wrote this one'

    result = app._tt_post_now('lilly', [_VIDEO_URL], '   ', 'at the gym')
    check('an empty caption is written in her voice before posting',
          result['caption'] == 'she wrote this one', result)

    result = app._tt_post_now('lilly', [_VIDEO_URL], 'the creator wrote this')
    check('a caption the creator typed is posted as it stands',
          result['caption'] == 'the creator wrote this', result)


def test_a_caption_is_trimmed_to_tiktoks_cap():
    check('the planner and the console write to the same TikTok cap',
          growth.POST_PLATFORMS['tiktok']['cap'] == TR.CAPTION_CAP)
    check('TikTok stays locked safe for work whatever the persona allows',
          'tiktok' in growth.SFW_LOCKED)


def test_not_connected_refuses_before_touching_the_network():
    app._tt_session = lambda p: {}
    threw = ''
    try:
        app._tt_post_now('lilly', [_VIDEO_URL], 'hi')
    except ValueError as e:
        threw = str(e)
    check('posting without a session says so instead of calling TikTok',
          'not connected' in threw.lower(), threw)


def test_the_session_round_trips_through_encryption():
    store = {}
    app._get_setting = lambda k: store.get(k, '')
    app._set_setting = lambda k, v: store.__setitem__(k, v)

    app._tt_set_session('lilly', _SESSION)
    check('the stored blob is not the session in the clear',
          'sessionid=abc' not in json.dumps(store), store)
    check('and it comes back out whole', app._tt_session('lilly') == _SESSION)

    app._tt_set_session('lilly', {})
    check('disconnecting leaves nothing to decrypt', app._tt_session('lilly') == {})

    check('the app keys TikTok accounts apart from Instagram and Discord',
          app._tt_account_id('lilly') == 'tt_lilly')


def test_signing_in_through_the_browser():
    """The sign-in relay, driven as TikTok. Nothing here opens a browser."""
    import of_connect

    check('tiktok is a site the relay knows', 'tiktok' in of_connect.SITES)
    check('and it opens TikTok, not Instagram or OnlyFans',
          of_connect.SITES['tiktok']['url'].startswith('https://www.tiktok.com'))

    made = of_connect.Attempt.__new__(of_connect.Attempt)
    made.site = 'tiktok'
    made._site = of_connect.SITES['tiktok']
    made.proxy = ''
    made.account = 'tt_lilly'
    made.capture_note = ''
    made.result = {}
    made.probes = 0
    made.state = 'signin'
    made.page_url = ''
    made.cookie_names = []
    made.user_agent = ''
    made._done = threading.Event()
    made._pending_session = None
    made._tt_device_id = '7100000000000000000'

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
            if 'user/detail' in js:
                return {'userInfo': {'user': {'secUid': 'sec-lilith'}}}
            return self.who

    made._capture_tiktok(Page(None), Context([{'name': 'tt_csrf_token', 'value': 'xyz'}]))
    check('no sessionid cookie yet means the sign-in is not finished',
          made.state == 'signin' and made.capture_note == 'awaiting_cookies')

    cookies = [{'name': 'sessionid', 'value': 'abc'},
               {'name': 'tt_csrf_token', 'value': 'xyz'},
               {'name': 'msToken', 'value': 'mmm'},
               {'name': 'uid_tt', 'value': '42'}]
    made._capture_tiktok(Page({'data': {'user_id_str': '42', 'username': 'lilith'}}),
                         Context(cookies))
    check('a verified fetch finishes the sign-in', made.state == 'connected')
    check('and marks the capture as verified', made.capture_note == 'captured')
    held = made._pending_session or {}
    check('the cookie header carries every cookie the browser held',
          'sessionid=abc' in held.get('cookie', '') and 'msToken=mmm' in held.get('cookie', ''),
          held)
    check('the csrf token is kept on its own too, for the header every write needs',
          held.get('csrftoken') == 'xyz')
    check('the browser-minted msToken is kept — it cannot be made up later',
          held.get('ms_token') == 'mmm')
    check('so is the device id the page was minted with',
          held.get('device_id') == '7100000000000000000')
    check('and her secUid, which is how her own posts are read back',
          held.get('sec_uid') == 'sec-lilith')
    check('claim hands it over exactly once',
          of_connect.claim(made) == held and of_connect.claim(made) is None)

    made.capture_note = ''
    made.state = 'signin'
    made._capture_tiktok(Page(None), Context(cookies))
    check('a signed-in cookie with no working fetch still finishes, unverified',
          made.state == 'connected' and made.capture_note == 'unverified')


def test_replies_are_drafted_in_her_voice_and_sent_only_when_asked():
    fake = TS.FakeRest()
    app._tt_rest = lambda persona: fake
    app._persona_text = lambda *a, **k: 'haha thank you'

    draft = app._tt_reply_draft('lilly', 'you look great', 'gym day')
    check('a draft comes back in her voice', draft == 'haha thank you', draft)
    check('drafting sends nothing', fake.replied == [])

    fake.reply('7', 'haha thank you', '99')
    check('sending is the separate step', fake.replied[-1]['reply_id'] == '99')


def test_the_planner_can_publish_tiktok_itself():
    check('tiktok is a channel the planner publishes rather than hands over',
          'tiktok' in growth.PUBLISHABLE)
    spec = growth.MEDIA_SUPPORT['tiktok']
    check('and it takes the bytes itself now, not by hand', spec['how'] == 'upload')
    check('a photo post may carry several stills', spec['max'] > 1)
    check('both kinds are allowed, because TikTok takes both',
          set(spec['kinds']) == {'image', 'video'})
    check('a set of stills passes the queue\'s own media check',
          not growth.media_set_reject('tiktok', ['image', 'image']),
          growth.media_set_reject('tiktok', ['image', 'image']))


def test_the_routes_exist():
    rules = {str(r) for r in app.app.url_map.iter_rules()}
    for path in ('/api/tiktok/status', '/api/tiktok/connect',
                 '/api/tiktok/connect/browser', '/api/tiktok/connect/frame',
                 '/api/tiktok/connect/input', '/api/tiktok/connect/cancel',
                 '/api/tiktok/post-now', '/api/tiktok/feed', '/api/tiktok/reply',
                 '/tiktok', '/tiktok/connect'):
        check('%s is registered' % path, path in rules)


def test_the_console_has_what_it_draws():
    page = open('tiktok.html').read()
    for sec in ('sec-account', 'sec-posting', 'sec-comments'):
        check('%s is a panel on the page' % sec, 'id="%s"' % sec in page)
    check('the post-now call reaches the posting endpoint',
          '/api/tiktok/post-now' in page)
    check('the comments panel reads her own posts back',
          '/api/tiktok/feed' in page)
    check('the connect popup reaches the same relay OnlyFans and Instagram use',
          '/tiktok/connect?site=tiktok' in page)


def test_the_transport_signs_its_upload_calls():
    """The upload gateway is AWS-shaped, so a wrong signature is the one
    failure that looks like a dead session instead of a bad request."""
    headers = TR._sig_v4('GET', 'https://vod.example/top/v1?Action=ApplyUploadInner',
                         b'', {'access_key_id': 'AK', 'secret_acess_key': 'SK',
                               'session_token': 'ST'})
    check('the call carries an Authorization header',
          headers['Authorization'].startswith('AWS4-HMAC-SHA256 Credential=AK/'))
    check('and the session token the credentials came with',
          headers['x-amz-security-token'] == 'ST')
    check('an unsigned deployment is visible rather than silently broken',
          TR.SIGNER_URL == '' or isinstance(TR.SIGNER_URL, str))


if __name__ == '__main__':
    for fn in (test_a_video_and_photos_never_ride_in_one_post,
               test_each_kind_reaches_its_own_call,
               test_a_blank_caption_is_written_not_left_empty,
               test_a_caption_is_trimmed_to_tiktoks_cap,
               test_not_connected_refuses_before_touching_the_network,
               test_the_session_round_trips_through_encryption,
               test_signing_in_through_the_browser,
               test_replies_are_drafted_in_her_voice_and_sent_only_when_asked,
               test_the_planner_can_publish_tiktok_itself,
               test_the_routes_exist,
               test_the_console_has_what_it_draws,
               test_the_transport_signs_its_upload_calls):
        restore()
        try:
            fn()
        except Exception as e:
            check(fn.__name__, False, repr(e))
    restore()
    print(f'\n{len(FAILURES)} failing')
    if FAILURES:
        raise SystemExit(1)
