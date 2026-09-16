"""Hosted-browser connect tests. The browser itself is faked."""
import json
import os
import queue
import threading
import time
import logging
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
    """An Attempt without its thread, so the capture logic can be driven by hand.

    Built through the real constructor: hand-assembling one let it drift from
    what Attempt actually holds, and the tests then failed on attributes the
    code was right to expect."""
    a = _ATTEMPT('lilith', account, proxy='http://u:p@nl.proxy.example:8000',
                 drive=False)
    a.id, a.state = 'ofc_test', 'signin'
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
        # Importing app (or of_browser) points the sink somewhere else, and a
        # test that reads the vault has to be told where its own sessions go.
        previous, of_connect._sink = of_connect._sink, None
        self.addCleanup(setattr, of_connect, '_sink', previous)
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


class PageRequestTest(unittest.TestCase):
    """The page makes the whole request, not only the signature in front of it.

    Signing in a browser and sending from the app is two clients as far as
    OnlyFans is concerned: two exit addresses, two handshakes, and an app-token
    taken from rules it is refusing. Here the request leaves from the browser
    that is signed in as her, so there is nothing left to reproduce.
    """

    class _Page:
        def __init__(self, answer):
            self.answer = answer
            self.asked = None

        def evaluate(self, js, arg=None):
            self.asked = arg
            return self.answer

    def _signer(self, answer):
        s = of_connect.Signer.__new__(of_connect.Signer)
        s.account, s.via, s.error = 'acct1', '', ''
        s.signed = s.fetched = 0
        s.page = self._Page(answer)
        return s

    def test_an_answer_comes_back_with_its_status(self):
        s = self._signer({'ok': True, 'status': 200, 'via': 'window.axios',
                          'data': {'list': [1]}})
        self.assertEqual(s._fetch_one({'method': 'GET', 'path': '/api2/v2/chats'}),
                         {'status': 200, 'body': {'list': [1]}})
        self.assertEqual(s.via, 'window.axios')
        self.assertEqual(s.page.asked['path'], '/api2/v2/chats')

    def test_a_refusal_is_an_answer_not_a_failure(self):
        s = self._signer({'ok': False, 'status': 401, 'via': 'window.axios',
                          'data': {'error': 'nope'}})
        got = s._fetch_one({'method': 'GET', 'path': '/api2/v2/users/me'})
        self.assertEqual(got['status'], 401)

    def test_a_page_that_made_no_request_says_why(self):
        s = self._signer({'ok': False, 'status': 0,
                          'error': 'no request interceptor on the page'})
        self.assertEqual(s._fetch_one({'method': 'GET', 'path': '/x'}), {})
        self.assertIn('interceptor', s.error)

    def test_a_signer_that_is_not_live_is_not_asked(self):
        s = self._signer({})
        with mock.patch.object(of_connect.Signer, 'live', return_value=False):
            self.assertEqual(s.fetch('GET', '/x'), {})


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
            'user_id': '99', 'sign': '13190:abc:ff:x', 'app_token': 'tok',
            'headers': {'sign': '13190:abc:ff:x', 'time': '1700000000',
                        'user-id': '99', 'app-token': 'tok'}})
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
        self.assertIs(kept['verified'], False)

    def test_still_refuses_when_there_is_no_session_at_all(self):
        a = bare_attempt()
        a._try_capture_session(self._page(None),
                               self._context([{'name': 'sess', 'value': 's'}]))
        self.assertEqual(a.capture_note, 'no_user_id')


class MissingAttemptTest(unittest.TestCase):
    """The window polls a dead sign-in several times a second."""

    def setUp(self):
        of_connect._missed.clear()

    def test_a_missing_attempt_is_reported_once_not_once_per_poll(self):
        with self.assertLogs('of_connect', level='INFO') as caught:
            for _ in range(40):
                self.assertIsNone(of_connect.get('ofc_gone'))
            logging.getLogger('of_connect').info('end of test')
        said = [line for line in caught.output if 'asked for and not here' in line]
        self.assertEqual(len(said), 1)

    def test_a_different_attempt_is_still_reported(self):
        with self.assertLogs('of_connect', level='INFO') as caught:
            of_connect.get('ofc_one')
            of_connect.get('ofc_two')
        said = [line for line in caught.output if 'asked for and not here' in line]
        self.assertEqual(len(said), 2)


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



class SolveChecksumTest(unittest.TestCase):
    """The solver has to reproduce a rule set that is known to be right before
    it can be trusted with one nobody has published yet."""

    def test_it_recovers_a_published_rule_set_from_one_signature(self):
        r = {'static_param': 'STATIC', 'format': '13190:{}:{:x}:653286c6',
             'checksum_indexes': [3, 9, 17], 'checksum_constant': 272,
             'app_token': 't'}
        s = {'path': '/api2/v2/users/me', 'time': '1789203482721',
             'user_id': '284724130', 'app_token': 't'}
        s['sign'] = of_rules.sign(s['path'], s['user_id'],
                                  when=int(s['time']), r=r)[0]
        other = {'path': '/api2/v2/chats?limit=10', 'time': '1789203499999',
                 'user_id': '284724130'}
        other['sign'] = of_rules.sign(other['path'], other['user_id'],
                                      when=int(other['time']), r=r)[0]
        parts = s['sign'].split(':')
        got = of_connect._solve_checksum(s, 'STATIC', parts[0], parts[1],
                                         parts[2], parts[3], confirm=[other],
                                         shapes=[([3, 9, 17], 't')])
        self.assertEqual(got.get('checksum_indexes'), [3, 9, 17])
        self.assertEqual(got.get('checksum_constant'), 272)


class LiteralScanTest(unittest.TestCase):
    """Adjacent strings are separate literals. With the quote characters inside
    the class, the greedy match ran through the delimiters and returned one blob
    per run — so the value being searched for was never tested on its own."""

    def test_adjacent_strings_come_back_separately(self):
        js = 'var a="aaaaaaaaaa",b="bbbbbbbbbb",c="cccccccccc";'
        self.assertEqual([m.group(1) for m in of_connect._LITERAL_RE.finditer(js)],
                         ['aaaaaaaaaa', 'bbbbbbbbbb', 'cccccccccc'])

    def test_the_in_page_scan_uses_the_same_class(self):
        self.assertIn('''[^"'`\\\\\\s]''', of_connect._LITERALS_JS)


class SafeHeadersTest(unittest.TestCase):
    def test_a_credential_is_kept_as_a_size_not_a_value(self):
        out = of_connect._safe_headers({'Cookie': 'sess=secret', 'x-bc': 'tok',
                                        'sign': '13190:abc:ff:x'})
        self.assertEqual(out['cookie'], '<11 chars>')
        self.assertEqual(out['x-bc'], '<3 chars>')
        self.assertEqual(out['sign'], '13190:abc:ff:x')


class InjectedSignatureTest(unittest.TestCase):
    """The /users/me probe leaves through the same page the listener watches."""

    def test_a_signature_we_injected_is_not_kept_as_the_oracle(self):
        a = bare_attempt()
        page = SigningSampleTest._Page()
        a._watch_signing(page)
        a._injected.append('13190:ours:ff:x')
        page.handler(SigningSampleTest._Request(
            'https://onlyfans.com/api2/v2/users/me',
            {'sign': '13190:ours:ff:x', 'time': '1789217425', 'user-id': '0'}))
        self.assertEqual(a.signing_sample, {})
        page.handler(SigningSampleTest._Request(
            'https://onlyfans.com/api2/v2/chats',
            {'sign': '65034:theirs:ff:y', 'time': '1789203482721', 'user-id': '9'}))
        self.assertEqual(a.signing_sample['sign'], '65034:theirs:ff:y')


class DeriveReportTest(unittest.TestCase):
    """A derivation that finds nothing has to say what it looked at."""

    def test_a_refused_sample_still_comes_back_with_a_reason(self):
        out = of_connect.derive_rules({'sign': 'not-four-parts', 'path': '/x'})
        self.assertEqual(out['rules'], {})
        self.assertIn('four parts', out['report']['why'])

    def test_the_bundle_scan_reports_what_it_fetched(self):
        class _Resp:
            def text(self):
                return 'var a="thisisaliteral";var b="65034x";'

        class _Req:
            def get(self, url, timeout=0):
                return _Resp()

        class _Ctx:
            request = _Req()

        literals, seen = of_connect._bundle_literals(_Ctx(), ['u1', 'u2'],
                                                   marker='65034')
        self.assertIn('thisisaliteral', literals)
        self.assertEqual(seen['ok'], 2)
        self.assertTrue(seen['marker'])
        self.assertEqual(seen['literals'], len(literals))


class SignerSurvivesAFailedProbeTest(unittest.TestCase):
    """One unsigned path must not retire the page.

    `error` was both "why the last probe failed" and "this signer is gone", so
    the first path the page would not sign closed the context and every later
    request fell back to arithmetic signing -- the path OnlyFans is refusing.
    In production `signed: 0, fetched: 0` was the symptom.
    """

    class _Page:
        def __init__(self, closed=False, url='https://onlyfans.com/', title='OnlyFans'):
            self._closed, self._url, self._title = closed, url, title
            self.answers = []

        def is_closed(self):
            return self._closed

        @property
        def url(self):
            return self._url

        def title(self):
            return self._title

        def evaluate(self, js, arg=None):
            return self.answers.pop(0) if self.answers else {}

        def wait_for_timeout(self, ms):
            pass

    def _signer(self, page):
        s = of_connect.Signer.__new__(of_connect.Signer)
        s.account, s.via, s.error, s.fatal = 'acct1', '', '', ''
        s.seen = {}
        s.signed = s.fetched = 0
        s.page = page
        return s

    def test_a_failed_probe_leaves_the_signer_live(self):
        s = self._signer(self._Page())
        s.opened_at = time.time()
        s._thread = mock.Mock(is_alive=lambda: True)
        s._fetch_one({'method': 'GET', 'path': '/api2/v2/chats'})
        self.assertTrue(s.error)
        self.assertEqual(s.fatal, '')
        self.assertTrue(s.live())

    def test_a_closed_page_is_fatal(self):
        s = self._signer(self._Page(closed=True))
        self.assertTrue(s._page_gone())
        s = self._signer(self._Page())
        self.assertFalse(s._page_gone())

    def test_state_says_where_the_page_is(self):
        s = self._signer(self._Page(url='https://onlyfans.com/?blocked',
                                    title='Just a moment...'))
        s.opened_at = time.time()
        s._thread = mock.Mock(is_alive=lambda: True)
        s._look()
        got = s.state()
        self.assertEqual(got['page']['title'], 'Just a moment...')
        self.assertIn('blocked', got['page']['url'])

    def test_where_never_touches_the_page_from_another_thread(self):
        # Playwright's sync objects belong to the browser thread; /health is
        # served from another one, so `state()` must read the cache only.
        s = self._signer(None)
        self.assertEqual(s._where(), {})


class ProbeScanTest(unittest.TestCase):
    """What the probe is willing to read back over CDP.

    The first probe overran its HTTP call because it read every JSON and
    script response, 3MB of bundle among them, and the caller's read timeout
    was the real deadline.
    """

    def test_json_is_worth_reading(self):
        self.assertTrue(of_connect.worth_reading('application/json', 2000))

    def test_the_bundle_is_not(self):
        self.assertFalse(of_connect.worth_reading(
            'application/javascript', 900000))

    def test_a_small_script_could_still_be_configuration(self):
        self.assertTrue(of_connect.worth_reading('text/javascript', 3000))

    def test_an_enormous_json_is_left_alone(self):
        self.assertFalse(of_connect.worth_reading('application/json', 9000000))

    def test_pictures_and_html_are_never_read(self):
        for kind in ('image/webp', 'text/html', 'font/woff2', ''):
            self.assertFalse(of_connect.worth_reading(kind, 1000), kind)

