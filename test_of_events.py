"""Watcher tests: what the chat list turns into, and what it does not."""
import hashlib
import hmac
import json
import os
import unittest
from unittest import mock

os.environ.setdefault('SECRET_KEY', 'test-secret-for-of-session')

import of_client
import of_events
import of_session
from test_of_client import SESSION, use_memory_store


def chat(fan='7', mid='100', text='hey', sent_by_me=False, minutes=1,
         price=None, opened=None, typing=False):
    from datetime import datetime, timedelta, timezone
    when = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    return {'id': fan, 'withUser': {'id': fan, 'username': 'fan' + fan},
            'isTyping': typing,
            'lastMessage': {'id': mid, 'text': text, 'isSentByMe': sent_by_me,
                            'createdAt': when, 'price': price,
                            'isOpened': opened}}


class WatcherTest(unittest.TestCase):
    def setUp(self):
        use_memory_store()
        of_session.reset_key()
        of_session.put('acct1', SESSION)
        self.sent = []
        of_events.sink(lambda e, a, p, i: self.sent.append((e, a, p, i)) or True)
        self.addCleanup(of_events.sink, None)
        self.w = of_events.Watcher('acct1')

    def poll(self, chats):
        with mock.patch.object(of_client, 'chats', return_value=chats):
            return self.w.poll()

    def test_the_first_pass_records_the_backlog_without_replying_to_it(self):
        self.poll([chat(minutes=600), chat(fan='8', mid='200', minutes=900)])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.w.seen, {'7': '100', '8': '200'})

    def test_a_message_that_arrived_during_the_first_pass_is_not_lost(self):
        self.poll([chat(minutes=2)])
        self.assertEqual([e for e, *_ in self.sent], ['messages.received'])

    def test_a_new_fan_message_becomes_a_received_event(self):
        self.poll([chat(minutes=600)])
        self.sent.clear()
        self.poll([chat(mid='101', text='you there?')])
        self.assertEqual(len(self.sent), 1)
        event, account, payload, idem = self.sent[0]
        self.assertEqual(event, 'messages.received')
        self.assertEqual(account, 'acct1')
        self.assertEqual(payload['user_id'], '7')
        self.assertEqual(payload['text'], 'you there?')
        self.assertEqual(payload['fromUser']['username'], 'fan7')
        self.assertEqual(idem, '101:in')

    def test_an_unchanged_chat_produces_nothing(self):
        self.poll([chat(minutes=600)])
        self.sent.clear()
        self.poll([chat(minutes=600)])
        self.assertEqual(self.sent, [])

    def test_a_reply_typed_in_the_onlyfans_app_is_reported_as_ours(self):
        self.poll([chat(minutes=600)])
        self.sent.clear()
        self.poll([chat(mid='101', text='hey you', sent_by_me=True)])
        self.assertEqual([e for e, *_ in self.sent], ['messages.sent'])

    def test_an_unlocked_ppv_is_reported_as_a_purchase(self):
        self.poll([chat(minutes=600)])
        self.sent.clear()
        self.poll([chat(mid='101', price=22, opened=True, sent_by_me=True)])
        self.assertEqual([e for e, *_ in self.sent], ['messages.ppv.unlocked'])

    def test_a_typing_fan_holds_the_reply_back(self):
        self.poll([chat(minutes=600)])
        self.sent.clear()
        self.poll([chat(minutes=600, typing=True)])
        self.assertEqual([e for e, *_ in self.sent], ['users.typing'])

    def test_the_watcher_never_opens_a_chat(self):
        with mock.patch.object(of_client, 'messages') as messages, \
                mock.patch.object(of_client, 'chats', return_value=[chat()]):
            self.w.poll()
            self.w.poll()
        messages.assert_not_called()

    def test_a_dead_session_stops_the_watcher_rather_than_retrying(self):
        of_session.mark_expired('acct1', 'gone')
        with self.assertRaises(of_client.SessionExpired):
            self.w.poll()

    def test_a_quiet_account_is_polled_less_often(self):
        self.assertEqual(self.w.interval(), of_events.POLL_IDLE)
        self.poll([chat(minutes=600)])
        self.poll([chat(mid='101')])
        self.assertEqual(self.w.interval(), of_events.POLL_ACTIVE)


class EmitTest(unittest.TestCase):
    def tearDown(self):
        of_events.sink(None)

    def test_the_post_is_signed_the_way_the_endpoint_verifies_it(self):
        of_events.sink(None)
        posted = {}

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            posted['body'] = req.data
            posted['headers'] = dict(req.headers)
            return Resp()

        with mock.patch.dict(os.environ, {
                'ONLYFANS_INTERNAL_WEBHOOK_URL': 'https://app.example/webhooks/onlyfans',
                'ONLYFANS_WEBHOOK_SECRET': 'shh'}), \
                mock.patch.object(of_events.urllib.request, 'urlopen', urlopen):
            self.assertTrue(of_events.emit('messages.received', 'acct1',
                                           {'user_id': '7'}, 'idem-1'))
        want = hmac.new(b'shh', posted['body'], hashlib.sha256).hexdigest()
        self.assertEqual(posted['headers']['Signature'], want)
        self.assertEqual(posted['headers']['X-ofapi-idempotency-key'], 'idem-1')
        self.assertEqual(json.loads(posted['body'])['event'], 'messages.received')

    def test_an_event_with_nowhere_to_go_is_reported_not_raised(self):
        with mock.patch.dict(os.environ, {'ONLYFANS_INTERNAL_WEBHOOK_URL': ''}):
            self.assertFalse(of_events.emit('messages.received', 'acct1', {}))


class RegistryTest(unittest.TestCase):
    def setUp(self):
        of_events._watchers.clear()

    def tearDown(self):
        for w in list(of_events._watchers.values()):
            w.stop()
        of_events._watchers.clear()

    def test_reconcile_starts_and_drops_to_match(self):
        with mock.patch.object(of_events.Watcher, 'start', lambda self: self):
            of_events.reconcile(['a', 'b'])
            self.assertEqual(sorted(of_events._watchers), ['a', 'b'])
            of_events.reconcile(['b', 'c'])
            self.assertEqual(sorted(of_events._watchers), ['b', 'c'])

    def test_watching_an_account_twice_keeps_one_watcher(self):
        with mock.patch.object(of_events.Watcher, 'start', lambda self: self):
            first = of_events.watch('a')
            self.assertIs(of_events.watch('a'), first)


if __name__ == '__main__':
    unittest.main()
