"""Session vault and transport tests. Nothing here touches the network."""
import json
import os
import unittest
from unittest import mock

os.environ.setdefault('SECRET_KEY', 'test-secret-for-of-session')

import of_client
import of_rules
import of_session


SESSION = {'user_id': '99', 'username': 'lilith', 'cookie': 'sess=abc; auth_id=99',
           'x_bc': 'bctoken', 'user_agent': 'Mozilla/5.0 (Windows NT 10.0)',
           'proxy': 'http://user:pw@nl.proxy.example:8000'}


def use_memory_store():
    """Wire the vault to a dict and hand it back."""
    store = {}
    of_session.store_hooks(lambda a: store.get(a, ''),
                           lambda a, b: store.__setitem__(a, b),
                           lambda a: store.pop(a, None),
                           lambda: sorted(store))
    return store


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.store = use_memory_store()
        of_session.reset_key()

    def test_round_trips(self):
        of_session.put('acct1', SESSION)
        got = of_session.get('acct1')
        self.assertEqual(got['cookie'], SESSION['cookie'])
        self.assertEqual(got['x_bc'], 'bctoken')
        self.assertEqual(got['status'], of_session.STATUS_LIVE)

    def test_credentials_are_encrypted_at_rest(self):
        of_session.put('acct1', SESSION)
        blob = self.store['acct1']
        self.assertNotIn('sess=abc', blob)
        self.assertNotIn('bctoken', blob)

    def test_incomplete_session_is_refused(self):
        with self.assertRaises(of_session.SessionError):
            of_session.put('acct1', {'cookie': 'sess=abc'})

    def test_public_view_hides_the_credentials(self):
        of_session.put('acct1', SESSION)
        pub = of_session.describe('acct1')
        self.assertEqual(pub['username'], 'lilith')
        self.assertEqual(pub['proxy_label'], 'nl.proxy.example:8000')
        self.assertNotIn('cookie', pub)
        self.assertNotIn('x_bc', pub)

    def test_a_changed_key_is_reported_not_swallowed(self):
        of_session.put('acct1', SESSION)
        of_session.reset_key()
        with mock.patch.dict(os.environ, {'SECRET_KEY': 'a-different-secret'}):
            with self.assertRaises(of_session.SessionError):
                of_session.get('acct1')

    def test_marking_expired_keeps_the_credentials(self):
        of_session.put('acct1', SESSION)
        of_session.mark_expired('acct1', 'got a 401')
        self.assertEqual(of_session.describe('acct1')['status'],
                         of_session.STATUS_EXPIRED)
        self.assertEqual(of_session.get('acct1')['cookie'], SESSION['cookie'])
        self.assertFalse(of_session.live('acct1'))

    def test_cookie_string_keeps_only_what_authenticates(self):
        jar = [{'name': 'sess', 'value': 'abc'}, {'name': 'auth_id', 'value': '99'},
               {'name': '_ga', 'value': 'noise'}, {'name': 'fp', 'value': 'fpv'}]
        self.assertEqual(of_session.cookie_string(jar), 'sess=abc; auth_id=99; fp=fpv')


class CallTest(unittest.TestCase):
    def setUp(self):
        use_memory_store()
        of_session.reset_key()
        of_session.put('acct1', SESSION)
        for target, value in (('rules', {'static_param': 's', 'format': '{}:{:x}',
                                         'checksum_indexes': [0], 'checksum_constant': 1,
                                         'app_token': 't'}),):
            p = mock.patch.object(of_rules, target, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(of_client, '_wait_turn')
        p.start()
        self.addCleanup(p.stop)

    def test_a_rotation_refetches_the_rules_and_retries(self):
        calls = []

        def once(account, method, path, body, session):
            calls.append(path)
            if len(calls) == 1:
                raise of_client.OnlyFansError(400, 'Please refresh the page')
            return {'id': 99}

        with mock.patch.object(of_client, '_once', once), \
                mock.patch.object(of_rules, 'refresh',
                                  return_value={'static_param': 'b',
                                                'format': '{}:{:x}',
                                                'checksum_indexes': [0],
                                                'checksum_constant': 1,
                                                'app_token': 't'}) as refresh:
            self.assertEqual(of_client.call('acct1', 'GET', '/api2/v2/users/me'),
                             {'id': 99})
        refresh.assert_called_once()
        self.assertEqual(len(calls), 2)

    def test_a_refresh_that_returns_the_rejected_rules_gives_up(self):
        """The source is up but has not caught up, so retrying is pointless —
        and the account must not be blamed for it."""
        same = {'static_param': 's', 'format': '{}:{:x}'}
        with mock.patch.object(of_client, '_once',
                               side_effect=of_client.OnlyFansError(
                                   400, 'Please refresh the page')) as once, \
                mock.patch.object(of_rules, 'fingerprint',
                                  side_effect=lambda r=None: 'same'), \
                mock.patch.object(of_rules, 'refresh', return_value=same):
            with self.assertRaises(of_client.SigningStale):
                of_client.call('acct1', 'GET', '/api2/v2/users/me')
        self.assertEqual(once.call_count, 1)
        self.assertEqual(of_session.describe('acct1')['status'],
                         of_session.STATUS_LIVE)

    def test_no_fetchable_rules_is_not_an_expired_session(self):
        with mock.patch.object(of_client, '_once',
                               side_effect=of_client.OnlyFansError(
                                   400, 'Please refresh the page')), \
                mock.patch.object(of_rules, 'refresh',
                                  side_effect=of_rules.RulesError('all sources 404')):
            with self.assertRaises(of_client.SigningStale):
                of_client.call('acct1', 'GET', '/api2/v2/users/me')
        self.assertEqual(of_session.describe('acct1')['status'],
                         of_session.STATUS_LIVE)

    def test_a_401_expires_the_session_instead_of_retrying(self):
        with mock.patch.object(of_client, '_once',
                               side_effect=of_client.OnlyFansError(401, 'nope')) as once:
            with self.assertRaises(of_client.SessionExpired):
                of_client.call('acct1', 'GET', '/api2/v2/users/me')
        self.assertEqual(once.call_count, 1)
        self.assertEqual(of_session.describe('acct1')['status'],
                         of_session.STATUS_EXPIRED)

    def test_a_rate_limit_backs_off_and_retries(self):
        seq = [of_client.OnlyFansError(429, 'slow down'), {'ok': True}]

        def once(*a, **k):
            item = seq.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with mock.patch.object(of_client, '_once', once), \
                mock.patch.object(of_client.time, 'sleep'):
            self.assertEqual(of_client.call('acct1', 'GET', '/x'), {'ok': True})

    def test_a_real_error_is_raised_straight_away(self):
        with mock.patch.object(of_client, '_once',
                               side_effect=of_client.OnlyFansError(
                                   400, 'chat is blocked')) as once:
            with self.assertRaises(of_client.OnlyFansError):
                of_client.call('acct1', 'POST', '/api2/v2/chats/1/messages', {'x': 1})
        self.assertEqual(once.call_count, 1)

    def test_an_unknown_account_is_refused(self):
        with self.assertRaises(of_client.OnlyFansError):
            of_client.call('nope', 'GET', '/api2/v2/users/me')

    def test_check_reports_a_dead_session_rather_than_raising(self):
        with mock.patch.object(of_client, '_once',
                               side_effect=of_client.OnlyFansError(401, 'nope')):
            alive, detail = of_client.check('acct1')
        self.assertFalse(alive)
        self.assertIn('reconnect', detail)

    def test_check_revives_an_account_that_came_back(self):
        of_session.mark_expired('acct1', 'earlier')
        with mock.patch.object(of_client, '_once',
                               return_value={'id': 99, 'username': 'lilith'}):
            alive, who = of_client.check('acct1')
        self.assertTrue(alive)
        self.assertEqual(who, 'lilith')
        self.assertEqual(of_session.describe('acct1')['status'], of_session.STATUS_LIVE)


class PagingTest(unittest.TestCase):
    def setUp(self):
        use_memory_store()
        of_session.reset_key()
        of_session.put('acct1', SESSION)

    def test_follows_the_offset_and_stops_on_hasmore(self):
        pages = [{'list': [{'id': 1}, {'id': 2}], 'hasMore': True},
                 {'list': [{'id': 3}], 'hasMore': False}]
        with mock.patch.object(of_client, 'call',
                               side_effect=lambda *a, **k: pages.pop(0)):
            got = of_client.paged('acct1', '/api2/v2/chats?limit=50')
        self.assertEqual([r['id'] for r in got], [1, 2, 3])

    def test_stops_when_a_page_repeats_itself(self):
        with mock.patch.object(of_client, 'call',
                               return_value={'list': [{'id': 1}], 'hasMore': True}):
            got = of_client.paged('acct1', '/api2/v2/chats')
        self.assertEqual(len(got), 1)

    def test_honours_the_wanted_count(self):
        with mock.patch.object(
                of_client, 'call',
                side_effect=lambda *a, **k: {'list': [{'id': n} for n in
                                                      range(int(a[2].split('offset=')[1]),
                                                            int(a[2].split('offset=')[1]) + 50)],
                                             'hasMore': True}):
            self.assertEqual(len(of_client.paged('acct1', '/api2/v2/chats', want=70)), 70)


class SendTest(unittest.TestCase):
    def setUp(self):
        use_memory_store()
        of_session.reset_key()
        of_session.put('acct1', SESSION)

    def test_a_paid_message_needs_media(self):
        with self.assertRaises(of_client.OnlyFansError):
            of_client.send('acct1', '5', 'unlock me', price=20)

    def test_a_price_outside_onlyfans_limits_is_refused(self):
        with self.assertRaises(of_client.OnlyFansError) as e:
            of_client.send('acct1', '5', 'unlock me', price=2, media=['7'])
        self.assertIn('between', str(e.exception))

    def test_a_ppv_carries_price_and_media(self):
        with mock.patch.object(of_client, 'call', return_value={'id': 1}) as call:
            of_client.send('acct1', '5', 'unlock me', price=22, media=['7', '8'])
        body = call.call_args[1]['body']
        self.assertEqual(body['price'], 22.0)
        self.assertEqual(body['mediaFiles'], [7, 8])

    def test_a_free_message_carries_no_price(self):
        with mock.patch.object(of_client, 'call', return_value={'id': 1}) as call:
            of_client.send('acct1', '5', 'hey')
        self.assertNotIn('price', call.call_args[1]['body'])


class PacingTest(unittest.TestCase):
    def test_requests_for_one_account_are_spaced_out(self):
        of_client._buckets.clear()
        slept = []
        with mock.patch.object(of_client.time, 'sleep', slept.append):
            of_client._wait_turn('acct1')
            of_client._wait_turn('acct1')
        self.assertGreaterEqual(slept[-1], of_client.OF_MIN_INTERVAL)

    def test_accounts_do_not_wait_on_each_other(self):
        of_client._buckets.clear()
        slept = []
        with mock.patch.object(of_client.time, 'sleep', slept.append):
            of_client._wait_turn('acct1')
            of_client._wait_turn('acct2')
        self.assertFalse([s for s in slept if s > 0])


if __name__ == '__main__':
    unittest.main()


class ProvenRulesTest(unittest.TestCase):
    """A 400 cannot mean 'the rules rotated' when the rules reproduce the
    signature OnlyFans' own page produced."""

    def setUp(self):
        use_memory_store()
        of_session.reset_key()
        of_session.put('acct1', dict(SESSION, verified=False))
        p = mock.patch.object(of_client, '_wait_turn')
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(of_rules, 'proven', return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def test_the_rule_set_is_never_rejected(self):
        with mock.patch.object(of_client, '_once',
                               side_effect=of_client.OnlyFansError(
                                   400, 'Please refresh the page')), \
                mock.patch.object(of_rules, 'refresh') as refresh:
            with self.assertRaises(of_client.SignatureRefused):
                of_client.call('acct1', 'GET', '/api2/v2/chats')
        refresh.assert_not_called()

    def test_a_refusal_that_hits_every_request_does_not_blame_the_session(self):
        """Reconnecting costs her a working session, so it is only asked for
        when /users/me says the session itself is what OnlyFans refuses."""
        with mock.patch.object(of_client, '_once',
                               side_effect=of_client.OnlyFansError(
                                   400, 'Please refresh the page')):
            with self.assertRaises(of_client.SignatureRefused):
                of_client.call('acct1', 'GET', '/api2/v2/chats')
        self.assertEqual(of_session.describe('acct1')['status'],
                         of_session.STATUS_LIVE)

    def test_a_session_only_onlyfans_rejects_is_marked_for_reconnection(self):
        def once(account, method, path, body, session):
            if path.endswith('/users/me'):
                raise of_client.OnlyFansError(401, 'unauthorized')
            raise of_client.OnlyFansError(400, 'Please refresh the page')

        with mock.patch.object(of_client, '_once', once):
            with self.assertRaises(of_client.SignatureRefused):
                of_client.call('acct1', 'GET', '/api2/v2/chats')
        self.assertEqual(of_session.describe('acct1')['status'],
                         of_session.STATUS_EXPIRED)

    def test_a_wrong_stored_user_id_is_fixed_and_the_call_retried(self):
        calls = []

        def once(account, method, path, body, session):
            calls.append((path, session.get('user_id')))
            if path.endswith('/users/me'):
                return {'id': 777}
            if len(calls) < 4:
                raise of_client.OnlyFansError(400, 'Please refresh the page')
            return {'ok': True}

        with mock.patch.object(of_client, '_once', once):
            self.assertEqual(of_client.call('acct1', 'GET', '/api2/v2/chats'),
                             {'ok': True})
        row = of_session.describe('acct1')
        self.assertEqual(row['user_id'], '777')
        self.assertTrue(row['verified'])


class ProxyPoolOffTest(unittest.TestCase):
    """Turning the pool off has to reach the sessions already in the vault."""

    def setUp(self):
        self._t = os.environ.pop('ONLYFANS_PROXY_TEMPLATE', None)

    def tearDown(self):
        if self._t is not None:
            os.environ['ONLYFANS_PROXY_TEMPLATE'] = self._t
        else:
            os.environ.pop('ONLYFANS_PROXY_TEMPLATE', None)

    @staticmethod
    def _routes_through(opener, host):
        return any(host in str(v) for h in opener.handlers
                   for v in (getattr(h, 'proxies', None) or {}).values())

    def test_a_stored_proxy_is_ignored_without_a_pool(self):
        opener = of_client._opener('http://user:pw@gw.example.com:823')
        self.assertFalse(self._routes_through(opener, 'gw.example.com'))

    def test_a_stored_proxy_is_used_when_a_pool_is_configured(self):
        os.environ['ONLYFANS_PROXY_TEMPLATE'] = 'http://u-{country}-{session}:p@gw:823'
        opener = of_client._opener('http://user:pw@gw.example.com:823')
        self.assertTrue(self._routes_through(opener, 'gw.example.com'))
