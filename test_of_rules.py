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

        with mock.patch.object(of_rules, 'RULES_SOURCES', ('a', 'b', 'c')), \
                mock.patch.object(of_rules, '_fetch', fetch):
            self.assertEqual(of_rules.refresh()['app_token'], RULES['app_token'])
        self.assertEqual(len(calls), 3)

    def test_a_source_still_serving_the_rejected_revision_is_skipped(self):
        newer = dict(RULES, static_param='NEWER', format='13190:{}:{:x}:653286c6')

        def fetch(url):
            return RULES if url == 'stale' else newer

        with mock.patch.object(of_rules, 'RULES_SOURCES', ('stale', 'fresh')), \
                mock.patch.object(of_rules, '_fetch', fetch):
            got = of_rules.refresh(reject=of_rules.fingerprint(RULES))
        self.assertEqual(got['static_param'], 'NEWER')

    def test_every_source_serving_the_rejected_revision_raises(self):
        with mock.patch.object(of_rules, 'RULES_SOURCES', ('a', 'b')), \
                mock.patch.object(of_rules, '_fetch', return_value=RULES):
            with self.assertRaises(of_rules.RulesError):
                of_rules.refresh(reject={of_rules.fingerprint(RULES)})

    def test_sources_are_named_by_position_not_by_repository(self):
        """A creator reading her console should not meet the account names of
        strangers on GitHub."""
        self.assertEqual(of_rules.label_of(of_rules.RULES_SOURCES[0]), 'Published set 1')
        self.assertEqual(of_rules.label_of('override'), 'Pasted in the console')
        self.assertEqual(of_rules.label_of('cached'), 'In use now')
        for url in of_rules.RULES_SOURCES:
            self.assertNotIn(url.split('/')[3], of_rules.label_of(url))

    def test_reads_the_format_out_of_a_signature(self):
        self.assertEqual(of_rules.format_of({'sign': '63708:abc:1f4:6a7f22a1'}),
                         RULES['format'])
        self.assertEqual(of_rules.format_of({'sign': 'nonsense'}), '')

    def test_solves_even_when_the_base_format_rotated(self):
        """The sample carries the format, so a base with a stale one still works."""
        sample = {'path': '/api2/v2/chats', 'user_id': '1', 'time': '1700000000',
                  'sign': expected('/api2/v2/chats', '1', 1700000000)}
        base = dict(RULES, static_param='OLD', format='999:{}:{:x}:zzzz')
        bundle = 'x="%s"' % RULES['static_param']
        got = of_rules.solve(bundle, sample, bases=[base])
        self.assertEqual(got['static_param'], RULES['static_param'])
        self.assertEqual(got['format'], RULES['format'])

    def test_a_fingerprint_tracks_what_the_signature_is_built_from(self):
        self.assertEqual(of_rules.fingerprint(RULES),
                         of_rules.fingerprint(dict(RULES, app_token='other')))
        self.assertNotEqual(of_rules.fingerprint(RULES),
                            of_rules.fingerprint(dict(RULES, static_param='x')))
        self.assertEqual(of_rules.fingerprint({'static_param': 'x'}), '')

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


class OracleTest(unittest.TestCase):
    """A signature OnlyFans' own page produced says which rule set is current."""

    def setUp(self):
        self.stamp = 1700000000
        self.sample = {'path': '/api2/v2/chats?limit=10', 'user_id': '99',
                       'time': str(self.stamp),
                       'sign': expected('/api2/v2/chats?limit=10', '99', self.stamp)}
        self.addCleanup(of_rules.sample_hooks, None, None)
        self.addCleanup(setattr, of_rules, '_rules', {})

    def test_verifies_the_matching_rules(self):
        self.assertIs(of_rules.verify(self.sample, RULES), True)

    def test_rejects_a_set_that_signs_differently(self):
        self.assertIs(of_rules.verify(self.sample, dict(RULES, static_param='x')), False)

    def test_says_nothing_without_a_sample(self):
        self.assertIsNone(of_rules.verify({}, RULES))
        self.assertIsNone(of_rules.verify(self.sample, {'static_param': 'x'}))

    def test_refresh_prefers_the_set_the_sample_proves(self):
        stale = dict(RULES, static_param='STALE', format='1:{}:{:x}:2')
        of_rules.sample_hooks(lambda: json.dumps(self.sample), lambda v: None)
        with mock.patch.object(of_rules, 'RULES_SOURCES', ('stale', 'current')), \
                mock.patch.object(of_rules, '_fetch',
                                  lambda u: stale if u == 'stale' else RULES):
            got = of_rules.refresh()
        self.assertEqual(got['static_param'], RULES['static_param'])

    def test_refresh_says_so_when_nothing_matches(self):
        stale = dict(RULES, static_param='STALE')
        of_rules.sample_hooks(lambda: json.dumps(self.sample), lambda v: None)
        with mock.patch.object(of_rules, 'RULES_SOURCES', ('a',)), \
                mock.patch.object(of_rules, '_fetch', return_value=stale):
            with self.assertRaises(of_rules.RulesError) as caught:
                of_rules.refresh()
        self.assertIn('captured signature', str(caught.exception))

    def test_an_override_beats_every_published_source(self):
        of_rules.override_hooks(lambda: json.dumps(RULES))
        self.addCleanup(of_rules.override_hooks, None)
        with mock.patch.object(of_rules, '_fetch',
                               return_value=dict(RULES, static_param='PUBLISHED')):
            self.assertEqual(of_rules.refresh()['static_param'], RULES['static_param'])

    def test_solves_the_static_param_out_of_a_bundle(self):
        bundle = 'var a="notitatall",b="%s",c=1;' % RULES['static_param']
        self.assertEqual(of_rules.solve(bundle, self.sample, bases=[RULES])['static_param'],
                         RULES['static_param'])

    def test_solving_gives_up_rather_than_guessing(self):
        self.assertEqual(of_rules.solve('var a="nothing useful here";',
                                        self.sample, bases=[RULES]), {})


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


class RefreshCooldownTest(unittest.TestCase):
    def test_a_failed_refresh_is_not_retried_on_every_request(self):
        of_rules._refresh_failed_at[0] = 0.0
        of_rules._rules = {'static_param': 's', 'format': '{}:{:x}',
                           'checksum_indexes': [0], 'checksum_constant': 1,
                           'app_token': 't'}
        of_rules._fetched_at = 0.0
        calls = []

        def boom(reject=()):
            calls.append(1)
            raise of_rules.RulesError('every source failed')

        real = of_rules.refresh
        of_rules.refresh = boom
        try:
            of_rules.rules()
            of_rules.rules()
            of_rules.rules()
        finally:
            of_rules.refresh = real
            of_rules._refresh_failed_at[0] = 0.0
        self.assertEqual(len(calls), 1)


class OracleForcedRefreshCooldownTest(unittest.TestCase):
    """The set the oracle disproves is the case that repeated per request, so
    it is the one the cooldown has to cover."""

    def test_a_disproved_set_does_not_refetch_on_every_call(self):
        of_rules._refresh_failed_at[0] = 0.0
        of_rules._rules = {'static_param': 's', 'format': '{}:{:x}',
                           'checksum_indexes': [0], 'checksum_constant': 1,
                           'app_token': 't'}
        of_rules._fetched_at = 0.0
        calls = []

        def boom(reject=()):
            calls.append(1)
            raise of_rules.RulesError('every source failed')

        real_refresh, real_verify = of_rules.refresh, of_rules.verify
        of_rules.refresh = boom
        of_rules.verify = lambda s, r: False
        try:
            for _ in range(3):
                of_rules.rules()
        finally:
            of_rules.refresh, of_rules.verify = real_refresh, real_verify
            of_rules._refresh_failed_at[0] = 0.0
        self.assertEqual(len(calls), 1)
