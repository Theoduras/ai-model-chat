"""Signing tests. No network: the rules are injected."""
import hashlib
import json
import unittest
from unittest import mock

import of_rules


RULES = {
    'static_param': 'u2U2XLXeDx884qvbKCqIaK0EppZtwzne',
    'format': '63708:{}:{:x}:6a7f22a1',
    'checksum_indexes': [25, 23, 28, 7, 32, 11, 25, 29, 20, 6, 1, 39, 30, 23,
                         39, 26, 1, 27, 9, 19, 20, 12, 37, 8, 38, 16, 4, 16,
                         22, 36, 33, 0],
    'checksum_constant': 573,
    'app_token': '33d57ade8c02dbc5a333db99ff9ae26a',
    'remove_headers': ['user-id'],
}


def expected(path, user_id, stamp, rules=RULES):
    msg = '\n'.join([rules['static_param'], str(stamp), path, str(user_id)])
    digest = hashlib.sha1(msg.encode()).hexdigest().encode('ascii')
    total = sum(digest[i] for i in rules['checksum_indexes'])
    return rules['format'].format(digest.decode(),
                                  abs(total + rules['checksum_constant']))


class SignTest(unittest.TestCase):
    def test_matches_the_reference_algorithm(self):
        got, stamp = of_rules.sign('/api2/v2/users/me', '12345',
                                   when=1700000000, r=RULES)
        self.assertEqual(stamp, '1700000000')
        self.assertEqual(got, expected('/api2/v2/users/me', '12345', 1700000000))

    def test_query_string_changes_the_signature(self):
        bare, _ = of_rules.sign('/api2/v2/chats', '1', when=1, r=RULES)
        with_q, _ = of_rules.sign('/api2/v2/chats?limit=10', '1', when=1, r=RULES)
        self.assertNotEqual(bare, with_q)

    def test_logged_out_signs_as_user_zero(self):
        got, _ = of_rules.sign('/api2/v2/init', '', when=5, r=RULES)
        self.assertEqual(got, expected('/api2/v2/init', '0', 5))

    def test_checksum_constants_used_when_the_single_one_is_absent(self):
        rules = dict(RULES)
        rules.pop('checksum_constant')
        rules['checksum_constants'] = [1, 2, 3]
        digest = hashlib.sha1(b'x').hexdigest().encode('ascii')
        self.assertEqual(of_rules._checksum(digest, rules),
                         sum(digest[i] for i in rules['checksum_indexes']) + 6)


class HeaderTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(of_rules, 'rules', return_value=RULES)
        patch.start()
        self.addCleanup(patch.stop)

    def test_carries_the_session_and_drops_retired_headers(self):
        h = of_rules.headers('/api2/v2/chats', {
            'cookie': 'sess=abc; auth_id=99', 'x_bc': 'bctoken',
            'user_agent': 'Mozilla/5.0 (Macintosh)', 'user_id': '99'})
        self.assertEqual(h['cookie'], 'sess=abc; auth_id=99')
        self.assertEqual(h['x-bc'], 'bctoken')
        self.assertEqual(h['user-agent'], 'Mozilla/5.0 (Macintosh)')
        self.assertEqual(h['app-token'], RULES['app_token'])
        self.assertNotIn('user-id', h)  # remove_headers
        self.assertEqual(h['sign'], expected('/api2/v2/chats', '99', int(h['time'])))

    def test_signs_without_a_session(self):
        h = of_rules.headers('/api2/v2/init')
        self.assertNotIn('cookie', h)
        self.assertEqual(h['sign'], expected('/api2/v2/init', '0', int(h['time'])))


class SourceTest(unittest.TestCase):
    def tearDown(self):
        of_rules._rules, of_rules._fetched_at = {}, 0.0
        of_rules.cache_hooks(None, None)

    def test_falls_through_to_the_next_source(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if len(calls) == 1:
                raise OSError('boom')
            if len(calls) == 2:
                return {'static_param': 'x'}  # incomplete, skipped
            return RULES

        with mock.patch.object(of_rules, '_fetch', fetch):
            self.assertEqual(of_rules.refresh()['app_token'], RULES['app_token'])
        self.assertEqual(len(calls), 3)

    def test_every_source_failing_raises(self):
        with mock.patch.object(of_rules, '_fetch', side_effect=OSError('no')):
            with self.assertRaises(of_rules.RulesError):
                of_rules.refresh()

    def test_cache_survives_a_cold_start(self):
        store = {}
        of_rules.cache_hooks(lambda: store.get('v', ''),
                             lambda v: store.__setitem__('v', v))
        with mock.patch.object(of_rules, '_fetch', return_value=RULES):
            of_rules.refresh()
        self.assertEqual(json.loads(store['v'])['rules']['app_token'],
                         RULES['app_token'])
        of_rules._rules, of_rules._fetched_at = {}, 0.0
        with mock.patch.object(of_rules, '_fetch', side_effect=OSError('no')):
            self.assertEqual(of_rules.rules()['app_token'], RULES['app_token'])

    def test_stale_rules_beat_no_rules(self):
        of_rules._rules, of_rules._fetched_at = dict(RULES), 1.0
        with mock.patch.object(of_rules, '_fetch', side_effect=OSError('no')):
            self.assertEqual(of_rules.rules()['app_token'], RULES['app_token'])


class RotationTest(unittest.TestCase):
    def test_recognises_a_rotation(self):
        self.assertTrue(of_rules.stale_response(400, 'Please refresh the page'))
        self.assertTrue(of_rules.stale_response(400, b'{"error":"invalid sign"}'))

    def test_leaves_real_errors_alone(self):
        self.assertFalse(of_rules.stale_response(400, 'chat is blocked'))
        self.assertFalse(of_rules.stale_response(404, 'refresh the page'))
        self.assertFalse(of_rules.stale_response(429, 'slow down'))


class PathTest(unittest.TestCase):
    def test_keeps_the_query(self):
        self.assertEqual(
            of_rules.path_of('https://onlyfans.com/api2/v2/chats?limit=10&offset=0'),
            '/api2/v2/chats?limit=10&offset=0')
        self.assertEqual(of_rules.path_of('https://onlyfans.com/api2/v2/users/me'),
                         '/api2/v2/users/me')


if __name__ == '__main__':
    unittest.main()
