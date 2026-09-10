"""The split browser service, driven over real HTTP.

What matters here is that the client is indistinguishable from the of_connect
module: app.py holds one set of routes and they must work either way. So the
same assertions are made against both, and the credentials must reach the vault
side without ever appearing in what the creator's own browser polls.
"""
import os
import queue
import threading
import time
import unittest
from wsgiref.simple_server import WSGIRequestHandler, make_server

os.environ.setdefault('SECRET_KEY', 'test-secret-for-of-session')

import of_browser
import of_connect

_ATTEMPT = of_connect.Attempt


def bare_attempt(account='acct1', state='signin'):
    a = _ATTEMPT.__new__(_ATTEMPT)
    a.id, a.persona, a.account = 'ofc_test', 'lilith', account
    a.proxy, a.user_agent = '', ''
    a.viewport = dict(of_connect.VIEWPORT)
    a.state, a.error, a.result = state, '', {}
    a.frame, a.frame_at = b'frame-bytes', 0.0
    a.probes, a.capture_note = 0, ''
    a.page_url, a.cookie_names = '', []
    a.started = a.touched = time.time()
    a._done = threading.Event()
    a._commands = queue.Queue()
    return a


class Quiet(WSGIRequestHandler):
    def log_message(self, *a):
        pass


class SplitTest(unittest.TestCase):
    """One attempt, reached through the service instead of in this process."""

    def setUp(self):
        of_browser.TOKEN = 'test-token'
        self.api = of_browser.service()
        self.server = make_server('127.0.0.1', 0, self.api, handler_class=Quiet)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        port = self.server.server_address[1]
        self.remote = of_browser.Remote(f'http://127.0.0.1:{port}', 'test-token')
        self.attempt = bare_attempt()
        of_connect._attempts[self.attempt.id] = self.attempt

    def tearDown(self):
        of_connect._attempts.clear()
        of_connect.session_sink(None)
        self.server.shutdown()
        self.server.server_close()

    def test_an_attempt_reads_the_same_through_the_wire(self):
        near = of_connect.get('ofc_test')
        far = self.remote.get('ofc_test')
        self.assertEqual(near.status(), far.status())
        self.assertEqual(near.persona, far.persona)
        self.assertEqual(near.account, far.account)
        self.assertEqual(near.snapshot(), far.snapshot())

    def test_a_missing_attempt_is_none_on_both_sides(self):
        self.assertIsNone(of_connect.get('ofc_nope'))
        self.assertIsNone(self.remote.get('ofc_nope'))

    def test_input_reaches_the_browser(self):
        self.remote.get('ofc_test').act('click', x=3, y=4)
        kind, kw = self.attempt._commands.get(timeout=2)
        self.assertEqual(kind, 'click')
        self.assertEqual((kw['x'], kw['y']), (3, 4))

    def test_an_unknown_input_is_refused(self):
        with self.assertRaises(of_connect.ConnectError):
            self.remote.get('ofc_test').act('rm -rf', x=1)

    def test_the_token_is_required(self):
        wrong = of_browser.Remote(self.remote.base_url, 'not-the-token')
        self.assertIsNone(wrong.get('ofc_test'))
        with self.assertRaises(of_connect.ConnectError):
            wrong.start('lilith', 'acct1')

    def test_a_captured_session_is_claimed_once_and_never_polled(self):
        session = {'user_id': '7', 'username': 'lilly', 'name': 'Lilly',
                   'cookie': 'sess=secret', 'x_bc': 'bc', 'user_agent': 'UA'}
        # What the browser thread does when the creator is finally in.
        self.attempt.result = of_connect._sink(self.attempt.account, session)
        self.attempt.state = 'connected'

        far = self.remote.get('ofc_test')
        self.assertEqual(far.status()['state'], 'connected')
        # The creator's window polls this. It must carry who she is and nothing
        # that would let its reader act as her.
        polled = repr(far.status())
        self.assertIn('lilly', polled)
        for secret in ('sess=secret', 'bc'):
            self.assertNotIn(secret, polled)

        self.assertEqual(self.remote.claim(far), session)
        # Handed over once: a second claim is empty, so a replayed request
        # cannot fetch the credentials again.
        self.assertIsNone(self.remote.claim(far))

    def test_an_unreachable_service_is_not_an_exploding_console(self):
        dead = of_browser.Remote('http://127.0.0.1:1', 'test-token')
        self.assertFalse(dead.available())
        dead.cancel('ofc_test')          # must not raise
        with self.assertRaises(of_connect.ConnectError):
            dead.start('lilith', 'acct1')
        # get() says Unreachable rather than None: None is reserved for the
        # service answering that the sign-in is gone, and the window ends on it.
        with self.assertRaises(of_browser.Unreachable):
            dead.get('ofc_test')


class RoundTripTest(unittest.TestCase):
    """A frame poll runs a few times a second. Asking twice for what one
    request can carry doubles the load on a service running one instance."""

    def setUp(self):
        of_browser.TOKEN = 'test-token'
        self.paths = []
        api = of_browser.service()

        @api.before_request
        def count():
            from flask import request
            self.paths.append(request.path)

        self.server = make_server('127.0.0.1', 0, api, handler_class=Quiet)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.remote = of_browser.Remote(
            f'http://127.0.0.1:{self.server.server_address[1]}', 'test-token')
        self.attempt = bare_attempt()
        of_connect._attempts[self.attempt.id] = self.attempt

    def tearDown(self):
        of_connect._attempts.clear()
        of_connect.session_sink(None)
        self.server.shutdown()
        self.server.server_close()

    def test_a_frame_poll_is_one_request(self):
        far = self.remote.get('ofc_test', frame=True)
        self.assertEqual(far.snapshot(), of_connect.get('ofc_test').snapshot())
        self.assertEqual(len(self.paths), 1, self.paths)

    def test_an_input_does_not_fetch_the_picture(self):
        self.remote.get('ofc_test').act('click', x=1, y=2)
        self.assertNotIn('frame', ' '.join(self.paths))

    def test_a_service_that_sends_no_frame_still_answers(self):
        # The app and the browser service deploy separately, so one can be
        # older than the other for a while.
        far = of_browser._Handle(self.remote, {'attempt': 'ofc_test'}, None)
        self.assertEqual(far.snapshot(), of_connect.get('ofc_test').snapshot())


class ReachabilityTest(unittest.TestCase):
    """Not being able to ask is not an answer.

    The app turns a None from get() straight into 'that sign-in is no longer
    open' and the creator's window stops. So None has to mean the service said
    so -- one slow second must not end a half-finished login.
    """

    def setUp(self):
        of_browser.TOKEN = 'test-token'
        self.code = 500
        api = of_browser.service()

        @api.route('/session/ofc_broken')
        def broken():
            from flask import jsonify
            return jsonify({'ok': False, 'error': 'boom'}), self.code

        self.server = make_server('127.0.0.1', 0, api, handler_class=Quiet)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.remote = of_browser.Remote(
            f'http://127.0.0.1:{self.server.server_address[1]}', 'test-token')

    def tearDown(self):
        of_connect._attempts.clear()
        of_connect.session_sink(None)
        self.server.shutdown()
        self.server.server_close()

    def test_a_service_that_says_no_such_sign_in_is_believed(self):
        self.assertIsNone(self.remote.get('ofc_gone'))

    def test_a_service_that_cannot_be_reached_is_not(self):
        dead = of_browser.Remote('http://127.0.0.1:1', 'test-token')
        with self.assertRaises(of_browser.Unreachable):
            dead.get('ofc_test')

    def test_a_bad_moment_is_not_a_dead_sign_in(self):
        with self.assertRaises(of_browser.Unreachable):
            self.remote.get('ofc_broken')

    def test_a_refusal_is_still_a_refusal(self):
        # 4xx is the service deciding, not failing. It must not be retried as
        # though the sign-in were still there.
        self.code = 403
        self.assertIsNone(self.remote.get('ofc_broken'))

    def test_old_call_sites_still_catch_it(self):
        # app.py and _Handle catch of_connect.ConnectError in several places
        # that must keep behaving as they did.
        self.assertTrue(issubclass(of_browser.Unreachable, of_connect.ConnectError))
        dead = of_browser.Remote('http://127.0.0.1:1', 'test-token')
        try:
            dead.get('ofc_test')
        except of_connect.ConnectError:
            pass
        else:
            self.fail('Unreachable did not reach an except ConnectError')
        self.assertFalse(dead.available())   # still swallowed
        dead.cancel('ofc_test')              # still swallowed


class WiringTest(unittest.TestCase):
    def test_no_url_means_the_in_process_browser(self):
        of_browser.BASE_URL, of_browser.TOKEN = '', ''
        self.assertIsNone(of_browser.remote())

    def test_a_url_without_a_token_is_not_used(self):
        of_browser.BASE_URL, of_browser.TOKEN = 'https://browser.example', ''
        self.assertIsNone(of_browser.remote())

    def test_both_set_gives_the_service(self):
        of_browser.BASE_URL, of_browser.TOKEN = 'https://browser.example', 'tok'
        self.assertIsInstance(of_browser.remote(), of_browser.Remote)

    def test_the_client_carries_the_modules_own_surface(self):
        # app.py catches of_connect.ConnectError and checks of_connect.INPUT_KINDS
        # whichever side is answering, so these cannot drift apart.
        self.assertIs(of_browser.Remote.ConnectError, of_connect.ConnectError)
        self.assertIs(of_browser.Remote.INPUT_KINDS, of_connect.INPUT_KINDS)
        for name in ('available', 'start', 'get', 'cancel', 'claim'):
            self.assertTrue(hasattr(of_browser.Remote, name), name)
            self.assertTrue(hasattr(of_connect, name), name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
