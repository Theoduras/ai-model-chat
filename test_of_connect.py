"""Hosted-browser connect tests. The browser itself is faked."""
import json
import os
import queue
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault('SECRET_KEY', 'test-secret-for-of-session')

import of_connect
import of_rules
import of_session
from test_of_client import use_memory_store


class FakeContext:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self, _url=None):
        return self._cookies


class FakePage:
    def __init__(self, who=None, x_bc='bctoken'):
        self.who = who
        self.x_bc = x_bc

    def evaluate(self, script, arg=None):
        if 'users/me' in script:
            return self.who
        if 'bcTokenSha' in script:
            return self.x_bc
        return 'Mozilla/5.0 (Fake)'


_ATTEMPT = of_connect.Attempt


def bare_attempt(account='acct1'):
    """An Attempt without its thread, so the capture logic can be driven by hand."""
    a = _ATTEMPT.__new__(_ATTEMPT)
    a.id, a.persona, a.account = 'ofc_test', 'lilith', account
    a.proxy, a.user_agent = 'http://u:p@nl.proxy.example:8000', ''
    a.viewport = dict(of_connect.VIEWPORT)
    a.state, a.error, a.result = 'signin', '', {}
    a.frame, a.frame_at = b'', 0.0
    a.signing_sample = {}
    a.probes, a.capture_note = 0, ''
    a.page_url, a.cookie_names = '', []
    a.started = a.touched = time.time()
    a._done = threading.Event()
    a._commands = queue.Queue()
    return a


class ProxyTest(unittest.TestCase):
    def test_credentials_are_split_out(self):
        self.assertEqual(
            of_connect._proxy_options('http://user:pw@nl.proxy.example:8000'),
            {'server': 'http://nl.proxy.example:8000',
             'username': 'user', 'password': 'pw'})

    def test_a_proxy_without_credentials(self):
        self.assertEqual(of_connect._proxy_options('http://nl.proxy.example:8000'),
                         {'server': 'http://nl.proxy.example:8000'})


class CaptureTest(unittest.TestCase):
    def setUp(self):
        use_memory_store()
        of_session.reset_key()
        self.a = bare_attempt()
        p = mock.patch.object(of_rules, 'rules', return_value={
            'static_param': 's', 'format': '{}:{:x}',
            'checksum_indexes': [0], 'checksum_constant': 1, 'app_token': 't'})
        p.start()
        self.addCleanup(p.stop)

    def test_nothing_is_stored_before_the_cookies_exist(self):
        self.a._try_capture_session(FakePage({'id': 9}), FakeContext(
            [{'name': 'fp', 'value': 'x'}]))
        self.assertEqual(self.a.state, 'signin')
        self.assertEqual(of_session.get('acct1'), {})

    def test_nothing_is_stored_when_there_is_no_id_to_be_had(self):
        """A page carrying no id is not a sign-in, whatever else it carries.
        An unconfirmed page that does carry one is kept unverified instead —
        see ConnectDuringARotationTest."""
        self.a._try_capture_session(FakePage(None), FakeContext(
            [{'name': 'sess', 'value': 'a'}]))
        self.assertEqual(self.a.state, 'signin')
        self.assertEqual(of_session.get('acct1'), {})

    def test_a_confirmed_sign_in_is_stored_and_ends_the_attempt(self):
        page = FakePage({'id': 9, 'username': 'lilith', 'name': 'Lilith'})
        self.a._try_capture_session(page, FakeContext(
            [{'name': 'sess', 'value': 'a'}, {'name': 'auth_id', 'value': '9'},
             {'name': '_ga', 'value': 'noise'}]))
        self.assertEqual(self.a.state, 'connected')
        self.assertTrue(self.a._done.is_set())
        stored = of_session.get('acct1')
        self.assertEqual(stored['user_id'], '9')
        self.assertEqual(stored['cookie'], 'sess=a; auth_id=9')
        self.assertEqual(stored['x_bc'], 'bctoken')
        self.assertEqual(stored['proxy'], self.a.proxy)
        self.assertEqual(stored['user_agent'], 'Mozilla/5.0 (Fake)')

    def test_what_the_dashboard_gets_back_has_no_credentials(self):
        self.a._try_capture_session(
            FakePage({'id': 9, 'username': 'lilith'}),
            FakeContext([{'name': 'sess', 'value': 'a'},
                         {'name': 'auth_id', 'value': '9'}]))
        self.assertNotIn('cookie', self.a.result)
        self.assertEqual(self.a.result['username'], 'lilith')


class FakeMouse:
    def __init__(self):
        self.moves = []

    def move(self, x, y):
        self.moves.append((x, y))


class PathTest(unittest.TestCase):
    """The mouse path the human check is watching."""

    def test_a_batch_is_replayed_in_order(self):
        page = mock.Mock(mouse=FakeMouse())
        _ATTEMPT._apply(bare_attempt(), page, 'move', {'points': [
            {'x': 1, 'y': 2, 't': 1000}, {'x': 3, 'y': 4, 't': 1010},
            {'x': 5, 'y': 6, 't': 1020}]})
        self.assertEqual(page.mouse.moves, [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)])

    def test_a_single_point_still_moves(self):
        page = mock.Mock(mouse=FakeMouse())
        _ATTEMPT._apply(bare_attempt(), page, 'move', {'x': 7, 'y': 8})
        self.assertEqual(page.mouse.moves, [(7.0, 8.0)])

    def test_the_gap_between_points_is_what_was_recorded(self):
        self.assertEqual(
            [gap for _x, _y, gap in of_connect._path({'points': [
                {'x': 0, 'y': 0, 't': 0}, {'x': 1, 'y': 1, 't': 20}]})],
            [0.0, 0.02])

    def test_a_long_pause_does_not_hold_the_browser(self):
        gaps = [gap for _x, _y, gap in of_connect._path({'points': [
            {'x': 0, 'y': 0, 't': 0}, {'x': 1, 'y': 1, 't': 9000}]})]
        self.assertEqual(gaps[1], 0.05)


class DrainTest(unittest.TestCase):
    """What the browser thread takes off the queue each time round.

    One command per iteration could not keep up -- a path is replayed in real
    time and a screenshot follows it -- so a click queued behind a few seconds
    of mouse movement landed seconds late, on a page that had moved on.
    """

    def drain(self, *commands):
        a = bare_attempt()
        for c in commands:
            a._commands.put(c)
        return _ATTEMPT._drain(a)

    def test_a_click_is_not_left_behind_its_approach(self):
        batch, quit_now = self.drain(
            ('move', {'points': [{'x': 1, 'y': 1}]}),
            ('down', {'x': 5, 'y': 5}), ('up', {'x': 5, 'y': 5}))
        self.assertEqual([kind for kind, _ in batch], ['move', 'down', 'up'])
        self.assertFalse(quit_now)

    def test_only_the_newest_path_is_replayed(self):
        batch, _ = self.drain(
            ('move', {'points': [{'x': 1, 'y': 1}]}),
            ('move', {'points': [{'x': 2, 'y': 2}]}),
            ('move', {'points': [{'x': 3, 'y': 3}]}))
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0][1]['points'], [{'x': 3, 'y': 3}])

    def test_typing_is_never_dropped_or_reordered(self):
        batch, _ = self.drain(
            ('type', {'text': 'a'}), ('move', {'points': [{'x': 1, 'y': 1}]}),
            ('type', {'text': 'b'}), ('move', {'points': [{'x': 2, 'y': 2}]}),
            ('key', {'key': 'Enter'}))
        # Movement is dropped down to the newest path; everything the creator
        # actually pressed stays, in the order she pressed it. Where the
        # surviving move sits among them does not matter -- a press carries
        # its own coordinates, so it cannot be misplaced by a dropped one.
        self.assertEqual([kind for kind, _ in batch if kind != 'move'],
                         ['type', 'type', 'key'])
        self.assertEqual([kw.get('text') for kind, kw in batch if kind == 'type'],
                         ['a', 'b'])
        moves = [kw for kind, kw in batch if kind == 'move']
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]['points'], [{'x': 2, 'y': 2}])

    def test_quit_stops_the_batch_where_it_was_asked_to(self):
        batch, quit_now = self.drain(
            ('type', {'text': 'a'}), ('quit', {}), ('type', {'text': 'b'}))
        self.assertTrue(quit_now)
        self.assertEqual([kind for kind, _ in batch], ['type'])

    def test_an_idle_queue_asks_for_nothing(self):
        self.assertEqual(self.drain(), ([], False))

    def test_a_long_path_gives_the_thread_back(self):
        # Every gap is the cap, so an unbudgeted replay would sleep 60 * 50ms.
        points = [{'x': i, 'y': i, 't': i * 9000} for i in range(60)]
        page = mock.Mock(mouse=FakeMouse())
        started = time.time()
        _ATTEMPT._apply(bare_attempt(), page, 'move', {'points': points})
        self.assertLess(time.time() - started, of_connect.MOVE_BUDGET + 0.2)
        self.assertEqual(len(page.mouse.moves), 60)


class BrowserTest(unittest.TestCase):
    def test_chrome_is_preferred_over_the_bundled_chromium(self):
        with mock.patch.object(of_connect, 'BROWSER_PATH', ''), \
                mock.patch.object(of_connect.os.path, 'exists',
                                  lambda p: p == '/usr/bin/google-chrome'):
            self.assertEqual(of_connect.browser_path(), '/usr/bin/google-chrome')

    def test_an_explicit_browser_wins(self):
        with mock.patch.object(of_connect, 'BROWSER_PATH', '/somewhere/chrome'):
            self.assertEqual(of_connect.browser_path(), '/somewhere/chrome')


class RegistryTest(unittest.TestCase):
    def setUp(self):
        of_connect._attempts.clear()

    def tearDown(self):
        of_connect._attempts.clear()

    def test_an_abandoned_attempt_is_swept(self):
        a = bare_attempt()
        a.touched = time.time() - (of_connect.IDLE_TTL + 10)
        of_connect._attempts[a.id] = a
        of_connect.sweep()
        self.assertEqual(of_connect._attempts, {})

    def test_a_watched_attempt_survives_a_sweep(self):
        a = bare_attempt()
        of_connect._attempts[a.id] = a
        of_connect.sweep()
        self.assertIn(a.id, of_connect._attempts)

    def test_starting_a_second_sign_in_replaces_the_first(self):
        first = bare_attempt()
        of_connect._attempts[first.id] = first
        with mock.patch.object(of_connect, 'Attempt',
                               new=lambda *a, **k: bare_attempt()), \
                mock.patch.object(of_connect, 'available', return_value=True):
            of_connect.start('lilith', 'acct1')
        self.assertTrue(first._done.is_set())

    def test_a_host_without_a_browser_says_so(self):
        with mock.patch.object(of_connect, 'available', return_value=False):
            with self.assertRaises(of_connect.ConnectError):
                of_connect.start('lilith', 'acct1')

    def test_status_does_not_leak_the_session(self):
        a = bare_attempt()
        a.result = {'username': 'lilith'}
        self.assertEqual(set(a.status()) & {'cookie', 'x_bc'}, set())


class SigningSampleTest(unittest.TestCase):
    """The browser service has no database, so the sample has to ride back to
    the app on the attempt's status."""

    class _Page:
        def __init__(self):
            self.handler = None

        def on(self, event, fn):
            self.handler = fn

    class _Request:
        def __init__(self, url, headers):
            self.url = url
            self.headers = headers

    def _attempt(self):
        return bare_attempt()

    def test_keeps_what_the_page_signed(self):
        attempt, page = self._attempt(), self._Page()
        attempt._watch_signing(page)
        page.handler(self._Request(
            'https://onlyfans.com/api2/v2/chats?limit=10',
            {'sign': '13190:abc:ff:x', 'time': '1700000000', 'user-id': '99',
             'app-token': 'tok'}))
        self.assertEqual(attempt.signing_sample, {
            'path': '/api2/v2/chats?limit=10', 'time': '1700000000',
            'user_id': '99', 'sign': '13190:abc:ff:x', 'app_token': 'tok'})
        self.assertEqual(attempt.status()['signing_sample'],
                         attempt.signing_sample)

    def test_ignores_anything_that_is_not_a_signed_api_call(self):
        attempt, page = self._attempt(), self._Page()
        attempt._watch_signing(page)
        page.handler(self._Request('https://onlyfans.com/api2/v2/chats', {}))
        page.handler(self._Request('https://onlyfans.com/theme.css',
                                   {'sign': 's', 'time': '1'}))
        self.assertEqual(attempt.signing_sample, {})

    def test_carries_no_credentials(self):
        attempt, page = self._attempt(), self._Page()
        attempt._watch_signing(page)
        page.handler(self._Request(
            'https://onlyfans.com/api2/v2/users/me',
            {'sign': 's', 'time': '1', 'cookie': 'sess=secret', 'x-bc': 'device'}))
        self.assertNotIn('cookie', attempt.signing_sample)
        self.assertNotIn('x_bc', attempt.signing_sample)
        self.assertNotIn('secret', json.dumps(attempt.signing_sample))


class ConnectDuringARotationTest(unittest.TestCase):
    """A rule set OnlyFans has moved past must not be why she cannot connect."""

    def _page(self, who):
        page = mock.Mock()
        page.evaluate.side_effect = lambda js, *a: (
            who if 'users/me' in str(js) else 'x')
        return page

    def _context(self, cookies):
        ctx = mock.Mock()
        ctx.cookies.return_value = cookies
        return ctx

    def test_falls_back_to_the_id_in_her_cookies(self):
        a = bare_attempt()
        kept = {}
        with mock.patch.object(of_connect, '_sink', lambda acct, s: kept.update(s)):
            a._try_capture_session(self._page(None), self._context(
                [{'name': 'sess', 'value': 's'}, {'name': 'auth_id', 'value': '4242'}]))
        self.assertEqual(a.capture_note, 'unverified')
        self.assertEqual(kept['user_id'], '4242')

    def test_still_refuses_when_there_is_no_session_at_all(self):
        a = bare_attempt()
        a._try_capture_session(self._page(None),
                               self._context([{'name': 'sess', 'value': 's'}]))
        self.assertEqual(a.capture_note, 'no_user_id')


if __name__ == '__main__':
    unittest.main()


class SampleNowDiagnosticsTest(unittest.TestCase):
    """A capture that finds nothing has to say what it saw. A bare {} is the
    same answer for a blocked page, a slow page and a page that signs nothing,
    and that ambiguity is what made this cost a deploy per guess."""

    class _Page:
        url = 'https://onlyfans.com/'

        def __init__(self, requests):
            self._requests = requests
            self._ctx = None

        def goto(self, *a, **kw):
            for r in self._requests:
                self._ctx.fire(r)

        def wait_for_timeout(self, ms):
            pass

        def title(self):
            return 'Just a moment…'

    class _Context:
        def __init__(self, page):
            page._ctx = self
            self.pages = [page]
            self._on = []

        def on(self, event, fn):
            self._on.append(fn)

        def fire(self, request):
            for fn in self._on:
                fn(request)

        def close(self):
            pass

    def _run(self, requests):
        page = self._Page(requests)
        context = self._Context(page)

        class _PW:
            def __enter__(self_inner):
                return object()

            def __exit__(self_inner, *a):
                return False

        with mock.patch.object(of_connect, '_driver', lambda: (lambda: _PW())), \
                mock.patch.object(of_connect.Attempt, '_launch',
                                  lambda self, pw: (None, context)):
            return of_connect.sample_now(timeout=0)

    def test_a_signed_request_is_the_sample(self):
        got = self._run([Req('https://onlyfans.com/api2/v2/init',
                             {'sign': '13190:a:ff:z', 'time': '17', 'user-id': '0'})])
        self.assertEqual(got['sign'], '13190:a:ff:z')
        self.assertEqual(got['user_id'], '0')

    def test_nothing_signed_comes_back_with_what_the_page_was_doing(self):
        got = self._run([Req('https://onlyfans.com/api2/v2/init', {}),
                         Req('https://onlyfans.com/style.css', {})])
        self.assertNotIn('sign', got)
        saw = got['_saw']
        self.assertEqual((saw['requests'], saw['api'], saw['signed']), (2, 1, 0))
        self.assertIn('moment', saw['title'])


class Req:
    def __init__(self, url, headers):
        self.url = url
        self.headers = headers
