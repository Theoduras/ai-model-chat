"""Tests for the unattended X round: sub-round gating, daily caps and the
settings merge. Nothing here touches the network — `_x_call` is mocked the way
test_of_client.py mocks its transport.

Run with: python -m pytest test_x_auto.py
"""
import json
import os
import unittest
from unittest import mock

os.environ.setdefault('SECRET_KEY', 'test-secret-for-x-auto')

import app
import utils


PERSONA = 'testbot'


class XAutoBase(unittest.TestCase):
    def setUp(self):
        self.store = {}

        def get_setting(key, default=None):
            return self.store.get(key, default)

        def set_setting(key, value):
            self.store[key] = value

        self.patches = [
            mock.patch.object(app, '_get_setting', get_setting),
            mock.patch.object(app, '_set_setting', set_setting),
            mock.patch.object(app, '_log_x_event', lambda *a, **k: None),
            mock.patch.object(app, '_log_x_message', lambda *a, **k: None),
            mock.patch.object(app, '_x_call', mock.Mock(return_value={})),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()


class DefaultsTest(XAutoBase):
    def test_defaults_cover_the_whole_round(self):
        cfg = app._x_behavior(PERSONA)
        for key in ('auto', 'interval_min', 'dm_replies', 'new_chats', 'comments',
                    'respond_own', 'post_content', 'follow', 'feed_engage',
                    'feed_post_limit', 'feed_reply_limit', 'new_chat_limit',
                    'comment_limit', 'post_age_min', 'reply_age_min', 'daily_caps'):
            self.assertIn(key, cfg)
        self.assertFalse(cfg['auto'])
        self.assertEqual(cfg['daily_caps'], app.X_DAILY_CAP_DEFAULTS)

    def test_saved_keys_survive_a_partial_write(self):
        # The merge rule: a key the caller leaves out keeps its saved value.
        self.store[f'x_behavior_{PERSONA}'] = json.dumps(
            {'auto': True, 'interval_min': 7, 'post_topic': 'gym day'})
        cfg = app._x_behavior(PERSONA)
        self.assertTrue(cfg['auto'])
        self.assertEqual(cfg['interval_min'], 7)
        self.assertEqual(cfg['post_topic'], 'gym day')
        self.assertTrue(cfg['dm_replies'])

    def test_caps_fill_in_from_defaults(self):
        self.store[f'x_behavior_{PERSONA}'] = json.dumps({'daily_caps': {'dms': 3}})
        caps = app._x_behavior(PERSONA)['daily_caps']
        self.assertEqual(caps['dms'], 3)
        self.assertEqual(caps['likes'], app.X_DAILY_CAP_DEFAULTS['likes'])


class SettingsEndpointTest(XAutoBase):
    def setUp(self):
        super().setUp()
        app.app.config['TESTING'] = True
        self.client = app.app.test_client()
        # Sign-in, plan and ownership gating is not what these assert.
        for mod, name, fn in ((app, '_path_needs_plan', lambda *a, **k: False),
                              (utils, '_is_operator', lambda: True)):
            p = mock.patch.object(mod, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def _post(self, body):
        r = self.client.post('/api/x/settings', json={'persona': PERSONA, **body})
        return r.get_json()

    def test_partial_post_leaves_other_keys_alone(self):
        self._post({'auto': True, 'interval_min': 20, 'query': 'fitness'})
        d = self._post({'new_chat_limit': 3})
        s = d['settings']
        self.assertTrue(s['auto'])
        self.assertEqual(s['interval_min'], 20)
        self.assertEqual(s['query'], 'fitness')
        self.assertEqual(s['new_chat_limit'], 3)

    def test_limits_are_clamped(self):
        s = self._post({'new_chat_limit': 99, 'feed_reply_limit': 999})['settings']
        self.assertEqual(s['new_chat_limit'], 5)
        self.assertEqual(s['feed_reply_limit'], 25)

    def test_caps_merge_one_kind_at_a_time(self):
        self._post({'daily_caps': {'dms': 5}})
        s = self._post({'daily_caps': {'posts': 1}})['settings']
        self.assertEqual(s['daily_caps']['dms'], 5)
        self.assertEqual(s['daily_caps']['posts'], 1)


class TokenStoreTest(XAutoBase):
    """The connection must outlive the container. It used to be a file next to
    the code, which Cloud Run throws away on every deploy."""

    def test_tokens_round_trip_through_settings(self):
        app._save_x_tokens({PERSONA: {'access_token': 'tok', 'username': 'her'}})
        self.assertIn(app.X_TOKENS_KEY, self.store)
        self.assertEqual(app._load_x_tokens()[PERSONA]['username'], 'her')

    def test_no_disk_read_when_settings_have_them(self):
        self.store[app.X_TOKENS_KEY] = json.dumps({PERSONA: {'access_token': 'tok'}})
        with mock.patch.object(app.os.path, 'exists',
                               side_effect=AssertionError('read the disk')):
            self.assertTrue(app._load_x_tokens()[PERSONA]['access_token'])

    def test_oauth_state_round_trips(self):
        app._x_oauth_state_put({'state': 'abc', 'persona': PERSONA})
        self.assertEqual(app._x_oauth_state_get()['state'], 'abc')
        app._x_oauth_state_clear()
        with mock.patch.object(app.os.path, 'exists', return_value=False):
            self.assertEqual(app._x_oauth_state_get(), {})


class WorkerLogTest(XAutoBase):
    """A round the worker ran has to leave a trace. It used to leave none: the
    log call read the request's IP, raised off a request, and swallowed it."""

    def test_client_ip_is_empty_off_a_request(self):
        self.assertEqual(app._client_ip(), '')

    def test_trace_records_a_line_without_a_request(self):
        app._x_trace(PERSONA, 'chats', 'round: dm replies 1')
        rows = json.loads(self.store[f'x_trace_{PERSONA}'])
        self.assertEqual(rows[-1]['detail'], 'round: dm replies 1')
        self.assertEqual(rows[-1]['stage'], 'chats')

    def test_a_failure_is_filed_as_an_error(self):
        app._x_trace_line(PERSONA, 'Post failed: over capacity')
        app._x_trace_line(PERSONA, 'Daily DM cap reached — not replying this round.')
        rows = json.loads(self.store[f'x_trace_{PERSONA}'])
        self.assertEqual(rows[0]['stage'], 'error')
        self.assertEqual(rows[1]['stage'], 'skipped')


class DailyCountTest(XAutoBase):
    def test_bump_and_rollover(self):
        self.assertEqual(app._x_daily_count(PERSONA, 'dms'), 0)
        app._x_daily_bump(PERSONA, 'dms', 2)
        app._x_daily_bump(PERSONA, 'dms', 1)
        self.assertEqual(app._x_daily_count(PERSONA, 'dms'), 3)
        # A tally from another date is not today's.
        row = json.loads(self.store[app._x_state_key(PERSONA, 'daily')])
        row['date'] = '2000-01-01'
        self.store[app._x_state_key(PERSONA, 'daily')] = json.dumps(row)
        self.assertEqual(app._x_daily_count(PERSONA, 'dms'), 0)

    def test_left_honours_unlimited(self):
        app._x_daily_bump(PERSONA, 'likes', 5)
        self.assertEqual(app._x_daily_left({'likes': 8}, PERSONA, 'likes'), 3)
        self.assertEqual(app._x_daily_left({'likes': 2}, PERSONA, 'likes'), 0)
        self.assertIsNone(app._x_daily_left({'likes': -1}, PERSONA, 'likes'))


class AutoRoundTest(XAutoBase):
    """The round with every sub-round stubbed, so what is asserted is the
    gating and the trimming, not the X API."""

    def setUp(self):
        super().setUp()
        self.dm = mock.Mock(return_value=(1, ['replied to 1']))
        self.followup = mock.Mock(return_value=(0, []))
        self.feed = mock.Mock(return_value=({'post_comments': 1, 'reply_answers': 1,
                                             'likes': 2}, ['worked the feed']))
        self.candidates = mock.Mock(return_value=[])
        for name, obj in (('_x_dm_reply_round', self.dm),
                          ('_x_followup_round', self.followup),
                          ('_x_feed_engage_round', self.feed),
                          ('_x_audience_candidates', self.candidates),
                          ('_x_generate_post', mock.Mock(return_value='hello world')),
                          ('_content_register_add', mock.Mock()),
                          ('_x_known_user_ids', mock.Mock(return_value=set())),
                          ('_x_opener_ids', mock.Mock(return_value=set()))):
            p = mock.patch.object(app, name, obj)
            p.start()
            self.addCleanup(p.stop)

    def opts(self, **over):
        base = dict(app._x_behavior(PERSONA))
        base.update(over)
        return base

    def test_switched_off_sub_rounds_do_not_run(self):
        actions, _ = app._x_auto_round(
            PERSONA, self.opts(dm_replies=False, feed_engage=False,
                               new_chats=False, post_content=False))
        self.assertFalse(self.dm.called)
        self.assertFalse(self.feed.called)
        self.assertEqual(actions['posts'], 0)

    def test_enabled_sub_rounds_run_and_are_tallied(self):
        actions, _ = app._x_auto_round(
            PERSONA, self.opts(dm_replies=True, feed_engage=True, post_content=True))
        self.assertEqual(actions['dm_replies'], 1)
        self.assertEqual(actions['likes'], 2)
        self.assertEqual(actions['posts'], 1)
        self.assertEqual(app._x_daily_count(PERSONA, 'dms'), 1)
        self.assertEqual(app._x_daily_count(PERSONA, 'comments'), 2)
        self.assertEqual(app._x_daily_count(PERSONA, 'posts'), 1)

    def test_second_round_is_trimmed_by_the_dm_cap(self):
        opts = self.opts(dm_replies=True, feed_engage=False, post_content=False,
                         new_chats=False, daily_caps={**app.X_DAILY_CAP_DEFAULTS,
                                                      'dms': 1})
        app._x_auto_round(PERSONA, opts)
        self.assertEqual(app._x_daily_count(PERSONA, 'dms'), 1)
        actions, log = app._x_auto_round(PERSONA, opts)
        self.assertEqual(actions['dm_replies'], 0)
        self.assertEqual(self.dm.call_count, 1)
        self.assertTrue(any('Daily DM cap reached' in l for l in log), log)

    def test_new_chat_limit_is_trimmed_not_dropped(self):
        opts = self.opts(dm_replies=False, feed_engage=False, post_content=False,
                         new_chats=True, new_chat_limit=4,
                         daily_caps={**app.X_DAILY_CAP_DEFAULTS, 'dms': 2})
        _, log = app._x_auto_round(PERSONA, opts)
        self.assertEqual(self.candidates.call_args[0][1], 2)
        self.assertTrue(any('trimmed from 4 to 2' in l for l in log), log)

    def test_post_cap_blocks_the_post(self):
        opts = self.opts(dm_replies=False, feed_engage=False, post_content=True,
                         new_chats=False,
                         daily_caps={**app.X_DAILY_CAP_DEFAULTS, 'posts': 0})
        actions, log = app._x_auto_round(PERSONA, opts)
        self.assertEqual(actions['posts'], 0)
        self.assertTrue(any('daily post cap' in l for l in log), log)


if __name__ == '__main__':
    unittest.main()
