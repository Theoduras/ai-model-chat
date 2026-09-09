"""Hosted-browser connect tests. The browser itself is faked."""
import os
import queue
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault('SECRET_KEY', 'test-secret-for-of-session')

import of_connect
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

    def evaluate(self, script):
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

    def test_nothing_is_stored_before_the_cookies_exist(self):
        self.a._try_capture_session(FakePage({'id': 9}), FakeContext(
            [{'name': 'fp', 'value': 'x'}]))
        self.assertEqual(self.a.state, 'signin')
        self.assertEqual(of_session.get('acct1'), {})

    def test_nothing_is_stored_if_onlyfans_does_not_confirm_the_session(self):
        self.a._try_capture_session(FakePage(None), FakeContext(
            [{'name': 'sess', 'value': 'a'}, {'name': 'auth_id', 'value': '9'}]))
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


if __name__ == '__main__':
    unittest.main()
