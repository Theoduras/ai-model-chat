"""Regression tests for TikTok posting.

Everything here runs against the stub transport: no token, no network. What is
checked is the part that cannot be checked against a real account safely — that
a blank caption is written rather than posted empty, that a video is what goes
up, that the rotating refresh token is stored and a network blip does not cost
her the connection, and that the planner's own branch reaches the same call.

Run with: python test_tiktok.py
"""
import base64
import json
import os

os.environ.setdefault('GEMINI_API_KEY', 'test')
os.environ.setdefault('DISCORD_WORKER', '0')
os.environ.setdefault('DISCORD_AUTOSTART', '0')
os.environ.setdefault('SECRET_KEY', 'test-secret-for-tiktok')

import app
import growth
import tiktok_oauth as TO
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


_BYTES = base64.b64encode(b'not really an mp4, just bytes').decode()
_VIDEO_URL = f'data:video/mp4;base64,{_BYTES}'
_IMAGE_URL = f'data:image/png;base64,{_BYTES}'
_SESSION = {'refresh_token': 'r1', 'access_token': 'a1',
            'access_expires': 9e12, 'open_id': 'oid', 'username': 'lilith'}


def _store():
    held = {}
    app._get_setting = lambda k: held.get(k, '')
    app._set_setting = lambda k, v: held.__setitem__(k, v)
    return held


def test_a_video_is_what_goes_up():
    fake = TS.FakeRest()
    app._tt_rest = lambda persona: fake
    app._tt_caption = lambda persona, brief: 'whatever'

    result = app._tt_post_now('lilly', [_VIDEO_URL], 'hello')
    check('a video posts', result['publish_id'] and fake.posted[-1]['bytes'] > 0, fake.posted)
    check('and its bytes reach the transport, not a placeholder',
          fake.uploaded[-1] == base64.b64decode(_BYTES))

    threw = ''
    try:
        app._tt_post_now('lilly', [_IMAGE_URL], 'hello')
    except ValueError as e:
        threw = str(e)
    check('a photo is refused rather than half-posted', 'video' in threw.lower(), threw)

    threw = ''
    try:
        app._tt_post_now('lilly', [], '')
    except ValueError as e:
        threw = str(e)
    check('a post with nothing attached is refused', 'Attach' in threw, threw)

    threw = ''
    try:
        app._tt_post_now('lilly', [_VIDEO_URL, _VIDEO_URL], '')
    except ValueError as e:
        threw = str(e)
    check('two videos in one post are refused', 'one video' in threw, threw)


def test_the_mode_follows_the_apps_audit():
    fake = TS.FakeRest()
    app._tt_rest = lambda persona: fake
    app._tt_caption = lambda persona, brief: 'whatever'

    app.TO.direct_post = lambda: False
    result = app._tt_post_now('lilly', [_VIDEO_URL], 'hi')
    check('unaudited, a post goes to her drafts', result['mode'] == 'inbox', result)

    app.TO.direct_post = lambda: True
    result = app._tt_post_now('lilly', [_VIDEO_URL], 'hi')
    check('audited, the same call publishes', result['mode'] == 'direct', result)
    app.TO.direct_post = TO.direct_post


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


def test_not_connected_refuses_before_touching_the_network():
    _store()
    threw = ''
    try:
        app._tt_post_now('lilly', [_VIDEO_URL], 'hi')
    except ValueError as e:
        threw = str(e)
    check('posting without a connection says so instead of calling TikTok',
          'not connected' in threw.lower(), threw)


def test_the_session_round_trips_through_encryption():
    held = _store()
    app._tt_set_session('lilly', _SESSION)
    check('the stored blob is not the token in the clear',
          'r1' not in json.dumps(held), held)
    check('and it comes back out whole', app._tt_session('lilly') == _SESSION)
    app._tt_set_session('lilly', {})
    check('disconnecting leaves nothing to decrypt', app._tt_session('lilly') == {})


def test_a_refreshed_token_is_stored_with_the_new_refresh_token():
    """TikTok rotates the refresh token on every refresh. Keeping the old one
    means the connection dies inside a day."""
    _store()
    stale = dict(_SESSION, access_expires=0)
    app._tt_set_session('lilly', stale)
    auth = TS.FakeAuth()
    app.TO = auth
    app.TO.TikTokAuthError = TS.FakeAuth.TikTokAuthError

    fresh = app._tt_fresh_token('lilly', app._tt_session('lilly'))
    check('a fresh access token comes back', fresh['access_token'] == 'access-1', fresh)
    check('and the rotated refresh token is what is kept',
          app._tt_session('lilly')['refresh_token'] == 'refresh-1',
          app._tt_session('lilly'))
    check('a token still good is not refreshed again',
          app._tt_fresh_token('lilly', app._tt_session('lilly'))['access_token'] == 'access-1'
          and auth.calls == 1, auth.calls)
    app.TO = TO


def test_a_blip_keeps_the_connection_and_a_refusal_ends_it():
    _store()
    app._tt_set_session('lilly', dict(_SESSION, access_expires=0))
    app.TO = TS.FakeAuth(fail='timed out', fatal=False)
    app.TO.TikTokAuthError = TS.FakeAuth.TikTokAuthError
    threw = ''
    try:
        app._tt_fresh_token('lilly', app._tt_session('lilly'))
    except ValueError as e:
        threw = str(e)
    check('a network failure says so', 'Could not reach' in threw, threw)
    check('and leaves her refresh token alone',
          app._tt_session('lilly').get('refresh_token') == 'r1')

    app.TO = TS.FakeAuth(fail='invalid_grant', fatal=True)
    app.TO.TikTokAuthError = TS.FakeAuth.TikTokAuthError
    threw = ''
    try:
        app._tt_fresh_token('lilly', app._tt_session('lilly'))
    except ValueError as e:
        threw = str(e)
    check('a refusal TikTok calls final ends the connection',
          'Connect her account again' in threw, threw)
    check('and clears the session, so the console stops offering to post',
          app._tt_session('lilly') == {})
    app.TO = TO


def test_the_signed_state_is_what_names_the_persona():
    state = TO.sign_state('lilly')
    check('a state round trips', TO.read_state(state) == 'lilly')
    check('a tampered state names nobody', TO.read_state('nova:' + state.split(':', 1)[1]) == '')
    check('so does a malformed one', TO.read_state('nonsense') == '')


def test_the_scope_asked_for_follows_the_audit():
    check('unaudited asks only for the inbox scope',
          'video.upload' in TO.SCOPE_BASE and 'video.publish' not in TO.SCOPE_BASE)
    check('audited asks for publishing too', 'video.publish' in TO.SCOPE_DIRECT)
    check('an unconfigured service refuses to build a sign-in link',
          not TO.configured())


def test_the_transport_chunks_the_way_tiktok_asks():
    rest = TR.Rest({'access_token': 't'})
    check('a small video goes up whole, in one chunk',
          rest._chunking(2 * 1024 * 1024) == (2 * 1024 * 1024, 1))
    check('so does one just inside the single-chunk limit',
          rest._chunking(TR.CHUNK_MAX) == (TR.CHUNK_MAX, 1))
    size = TR.CHUNK_MAX * 3 + 100
    chunk, count = rest._chunking(size)
    check('a bigger one is split', chunk == TR.CHUNK_MAX and count == 3, (chunk, count))
    check('no token means no call at all',
          not TR.Rest({}).configured())


def test_the_planner_publishes_a_tiktok_video_itself():
    check('tiktok is a channel the planner publishes rather than hands over',
          'tiktok' in growth.PUBLISHABLE)
    spec = growth.MEDIA_SUPPORT['tiktok']
    check('and it hands over the bytes itself', spec['how'] == 'upload')
    check('one clip per post', spec['max'] == 1 and spec['kinds'] == ('video',))
    check('TikTok stays locked safe for work', 'tiktok' in growth.SFW_LOCKED)

    fake = TS.FakeRest()
    app._tt_rest = lambda persona: fake
    app._media_row = lambda persona, mid: {'id': mid, 'kind': 'video'}
    app._media_bytes = lambda row: (b'clip-bytes', 'video/mp4')
    app._content_register_add = lambda *a, **k: None
    app._tt_log_post = lambda *a, **k: None
    posted = app._growth_publish('lilly', 'tiktok', 'a caption', media_ids=['vid'])
    check('the queue reaches the same call the console does',
          posted == fake.posted[-1]['publish_id'], fake.posted)
    check('carrying the caption the planner wrote',
          fake.posted[-1]['caption'] == 'a caption')

    app._media_bytes = lambda row: (b'still-bytes', 'image/png')
    app._media_row = lambda persona, mid: {'id': mid, 'kind': 'image'}
    threw = ''
    try:
        app._growth_publish('lilly', 'tiktok', 'a caption', media_ids=['img'])
    except Exception as e:
        threw = str(e)
    check('a planned still is refused before it reaches TikTok',
          'video' in threw.lower(), threw)


def test_the_routes_exist():
    rules = {str(r) for r in app.app.url_map.iter_rules()}
    for path in ('/tiktok', '/tiktok/oauth/start', '/tiktok/oauth/callback',
                 '/api/tiktok/status', '/api/tiktok/connect', '/api/tiktok/post-now'):
        check('%s is registered' % path, path in rules)
    for gone in ('/tiktok/connect', '/api/tiktok/connect/browser',
                 '/api/tiktok/feed', '/api/tiktok/reply'):
        check('%s is gone with the browser transport' % gone, gone not in rules)


def test_the_console_has_what_it_draws():
    page = open('tiktok.html').read()
    for sec in ('sec-account', 'sec-posting'):
        check('%s is a panel on the page' % sec, 'id="%s"' % sec in page)
    check('the post-now call reaches the posting endpoint',
          '/api/tiktok/post-now' in page)
    check('connecting goes through TikTok\'s own approval page',
          '/tiktok/oauth/start' in page)
    check('nothing is left of the sign-in relay', 'site=tiktok' not in page)
    check('the console says where a post actually lands',
          'drafts' in page)


if __name__ == '__main__':
    for fn in (test_a_video_is_what_goes_up,
               test_the_mode_follows_the_apps_audit,
               test_a_blank_caption_is_written_not_left_empty,
               test_not_connected_refuses_before_touching_the_network,
               test_the_session_round_trips_through_encryption,
               test_a_refreshed_token_is_stored_with_the_new_refresh_token,
               test_a_blip_keeps_the_connection_and_a_refusal_ends_it,
               test_the_signed_state_is_what_names_the_persona,
               test_the_scope_asked_for_follows_the_audit,
               test_the_transport_chunks_the_way_tiktok_asks,
               test_the_planner_publishes_a_tiktok_video_itself,
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
