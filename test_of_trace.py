import json
import logging
import os
import unittest

os.environ.setdefault('GEMINI_API_KEY', 'test')
os.environ.setdefault('ONLYFANS_WORKER', '0')

import app
import of_trace


class TraceTest(unittest.TestCase):

    def setUp(self):
        of_trace.clear()
        of_trace.install()

    def test_a_plumbing_log_line_reaches_the_console(self):
        logging.getLogger('of_client').warning('signature refused: %s', 401)
        line = of_trace.recent()[-1]
        self.assertEqual(line['where'], 'client')
        self.assertEqual(line['level'], 'warning')
        self.assertIn('401', line['text'])

    def test_only_lines_after_the_cursor_come_back(self):
        of_trace.note('rules', 'one')
        first = of_trace.recent()[-1]['id']
        of_trace.note('rules', 'two')
        self.assertEqual([r['text'] for r in of_trace.recent(after=first)], ['two'])

    def test_installing_twice_does_not_double_every_line(self):
        of_trace.install()
        logging.getLogger('of_events').info('polled')
        self.assertEqual(len(of_trace.recent()), 1)

    def test_the_ring_forgets_rather_than_growing(self):
        for i in range(of_trace.MAX + 40):
            of_trace.note('rules', str(i))
        self.assertEqual(len(of_trace.recent(limit=of_trace.MAX + 40)), of_trace.MAX)


if __name__ == '__main__':
    unittest.main()


class BrowserTraceTest(unittest.TestCase):
    """The browser runs in a service of its own, so its lines have to reach the
    console over the wire -- they are the ones that say why a capture found
    nothing."""

    def test_the_service_serves_its_own_ring(self):
        import of_browser
        of_trace.clear()
        of_browser.TOKEN = 'test-token'
        api = of_browser.service()
        of_trace.note('connect', 'no signature captured')
        client = api.test_client()
        r = client.get('/trace', headers={'X-Browser-Token': 'test-token'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual([l['text'] for l in r.get_json()['lines']],
                         ['no signature captured'])


class BuildIdTest(unittest.TestCase):
    def test_stable_and_short(self):
        of_trace._build[0] = ''
        first = of_trace.build_id()
        self.assertEqual(len(first), 10)
        self.assertEqual(first, of_trace.build_id())


class DiagEndpointTest(unittest.TestCase):
    """Read-only, and closed until someone sets a key worth having."""

    def setUp(self):
        self.client = app.app.test_client()
        self._key = os.environ.get('DIAG_KEY')

    def tearDown(self):
        if self._key is None:
            os.environ.pop('DIAG_KEY', None)
        else:
            os.environ['DIAG_KEY'] = self._key

    def test_closed_without_a_key(self):
        os.environ.pop('DIAG_KEY', None)
        self.assertEqual(self.client.get('/api/diag').status_code, 404)

    def test_a_short_key_is_not_a_key(self):
        os.environ['DIAG_KEY'] = 'short'
        self.assertEqual(self.client.get('/api/diag?key=short').status_code, 404)

    def test_the_wrong_key_looks_like_nothing_is_there(self):
        os.environ['DIAG_KEY'] = 'k' * 32
        self.assertEqual(self.client.get('/api/diag?key=' + 'j' * 32).status_code, 404)

    def test_the_right_key_answers_without_credentials(self):
        os.environ['DIAG_KEY'] = 'k' * 32
        r = self.client.get('/api/diag', headers={'X-Diag-Key': 'k' * 32})
        self.assertEqual(r.status_code, 200)
        body = json.dumps(r.get_json())
        for leak in ('cookie', 'x_bc', 'password', 'DIAG_KEY'):
            self.assertNotIn(leak, body)
