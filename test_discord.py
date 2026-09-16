"""Regression tests for the Discord connection.

Everything here runs against the stub transport: no token, no socket, no
network. What is checked is the part that cannot be checked in production
without a real server full of real people — which messages she is allowed to
answer, how often she may speak unprompted, and that a paid link never leaves a
DM.

Run with: python test_discord.py
"""
import json
import os
import time

os.environ.setdefault('GEMINI_API_KEY', 'test')
os.environ.setdefault('DISCORD_WORKER', '0')
os.environ.setdefault('DISCORD_AUTOSTART', '0')
os.environ.setdefault('SECRET_KEY', 'test-secret-for-discord')

import app
import discord_gateway as DG
import discord_rest as DR
import discord_stub as DS

FAILURES = []
_PRISTINE = {k: v for k, v in vars(app).items() if callable(v)}


def restore():
    for k, v in _PRISTINE.items():
        setattr(app, k, v)
    DS.uninstall('lilly')


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (('  ' + str(detail)) if not ok else ''))
    if not ok:
        FAILURES.append(name)


def test_adapter_shape():
    """The engine calls these by name. A missing one is not a type error until a
    fan is already waiting on a reply."""
    plat = app.PLAT_DISCORD
    needed = ('connected', 'scope', 'me', 'chats', 'read_chat', 'online',
              'messages', 'import_history', 'text_of', 'msg_id', 'msg_time',
              'msg_age', 'direction', 'send_text', 'send_ppv', 'typing',
              'webhook_state', 'reachable')
    missing = [n for n in needed if not callable(getattr(plat, n, None))]
    check('the adapter answers everything the round asks of it', not missing, missing)
    check('state is namespaced away from the other platforms',
          plat.k('auto', 'lilly') == 'discord_auto_lilly')
    check('fan keys are namespaced too', plat.fan_key('7') == 'dc:7')
    check('discord is registered', app.PLATFORMS.get('discord') is plat)
    check('the unified inbox knows both of its shapes',
          app.INBOX_PLATFORMS['discord']['prefixes'] == ('dc:', 'dcg:'))


def test_a_dm_is_taken():
    woken = []
    runner = DS.install('lilly', on_dm=lambda p, f: woken.append(f))
    DS.dm(runner, 'hey you', fan='77', handle='dave')
    check('a direct message wakes a round', woken == ['77'], woken)
    check('the fan is now a known chat',
          [c['fan_id'] for c in DG.chats('lilly')] == ['77'])
    check('and what he said is readable',
          DG.text_of(DG.messages('lilly', '77')[-1]) == 'hey you')
    check('it is read as inbound',
          DG.direction_of(DG.messages('lilly', '77')[-1]) == 'in')

    woken.clear()
    DS.dm(runner, 'beep', fan='88', bot=True)
    check('a bot is ignored', woken == [], woken)
    DS.dm(runner, '   ', fan='99')
    check('an empty message is ignored', woken == [], woken)
    first = DS.dm(runner, 'again', fan='77')
    woken.clear()
    runner._on_message(first)
    check('the same message twice only counts once', woken == [], woken)


def test_the_mention_gate():
    """The difference between a persona in a server and a bot in a server. Every
    line in a busy channel that is not addressed to her has to cost nothing."""
    woken = []
    runner = DS.install('lilly', on_channel=lambda p, g, c, a: woken.append(a))
    runner.configure({'allow': [{'guild': '9', 'channel': '90'}], 'chime': {}})

    DS.channel(runner, 'anyone around?')
    check('ordinary chatter is left alone', woken == [], woken)
    DS.channel(runner, 'hey lilly', mention=True)
    check('being @mentioned is answered', woken == [True], woken)
    woken.clear()
    DS.channel(runner, 'yeah exactly', reply_to_me=True)
    check('a reply with the ping suppressed still counts', woken == [True], woken)
    woken.clear()
    DS.channel(runner, 'hi lilly', mention=True, chan='91')
    check('a channel not on the allowlist is silent', woken == [], woken)
    runner.configure({'allow': []})
    DS.channel(runner, 'hi lilly', mention=True)
    check('an empty allowlist means she says nothing in public', woken == [], woken)


def test_chiming_in_is_capped():
    woken = []
    runner = DS.install('lilly', on_channel=lambda p, g, c, a: woken.append(a))
    runner.configure({'allow': [{'guild': '9', 'channel': '90'}],
                      'chime': {'enabled': True, 'cooldown_min': 45,
                                'keywords': ['gaming']}})
    DS.channel(runner, 'the weather is nice')
    check('an unprompted line off-topic is skipped', woken == [], woken)
    DS.channel(runner, 'anyone playing gaming tonight')
    check('a keyword she cares about lets her join in', woken == [False], woken)
    woken.clear()
    DS.channel(runner, 'more gaming talk')
    check('but not twice inside the cooldown', woken == [], woken)
    DS.channel(runner, 'lilly what do you think', mention=True)
    check('being asked directly is never held back by the cooldown',
          woken == [True], woken)


def test_the_daily_cap_survives_a_restart():
    """The gateway's own cooldown lives in memory, and Cloud Run wipes memory.
    The count that actually limits her has to be the one in the database."""
    saved = {}
    app._get_setting = lambda k, d=None: saved.get(k, d)
    app._set_setting = lambda k, v: saved.__setitem__(k, v)
    ok = [app._dc_chime_spend('lilly', '90', 2) for _ in range(3)]
    check('the budget is spent and then refused', ok == [True, True, False], ok)
    check('the count is written down, not remembered',
          json.loads(saved['discord_chime_state_lilly'])['90']['n'] == 2)
    check('a cap of zero never lets her chime in at all',
          app._dc_chime_spend('lilly', '91', 0) is False)


def test_sending_and_pacing():
    runner = DS.install('lilly')
    DS.dm(runner, 'hey', fan='77', channel='500')
    runner.send('77', 'hey back')
    check('the reply goes to the fan channel',
          runner.rest.sent[-1]['channel_id'] == '500')
    check('and is in her own transcript straight away, not on the echo',
          DG.messages('lilly', '77')[-1]['out'] is True)
    runner._last_out['500'] = time.time()
    started = time.time()
    runner.send('77', 'and again')
    check('two messages into one channel are spaced out',
          time.time() - started >= DG.OUT_FLOOR_SECONDS - 0.5)
    runner._sent_today = DG.DAILY_OUT_CAP
    try:
        runner.send('77', 'once more')
        check('the daily cap stands her down', False, 'no error raised')
    except DR.DiscordApiError:
        check('the daily cap stands her down', True)


def test_a_link_tier_is_allowed_where_media_is_not():
    """Discord has no paywall, so a drop is a link. Every other platform still
    has to be held to a real price and real media."""
    link = app._fv_clean_tier({'link': True, 'price': 0, 'caption': 'come see'})
    check('a link tier with no media is kept', link and link['link'] is True, link)
    check('an ordinary tier with no media is still refused',
          app._fv_clean_tier({'price': 1200, 'caption': 'x'}) is None)
    check('and one priced under the floor still is',
          app._fv_clean_tier({'media': ['a'], 'price': 100}) is None)
    check('a normal tier is unchanged',
          (app._fv_clean_tier({'media': ['a'], 'price': 1200}) or {}).get('price') == 1200)


def test_the_offer_only_ever_goes_to_a_dm():
    runner = DS.install('lilly')
    DS.dm(runner, 'hey', fan='77', channel='500')
    app._phases_cta = lambda slug: {'cta_url': 'https://paid.example/lilly'}
    app._get_setting = lambda k, d=None: '' if k.startswith('discord_cta') else d
    mid = app.PLAT_DISCORD.send_ppv('lilly', '', '77', 'this one is just for you',
                                    [], 1200)
    body = runner.rest.sent[-1]['content']
    check('the drop carries the caption and the link',
          'just for you' in body and 'paid.example' in body, body)
    check('it comes back with a real message id to record',
          mid.startswith('dc:500:'), mid)
    check('no drop was sent into a channel',
          all(r['channel_id'] == '500' for r in runner.rest.sent))
    app._phases_cta = lambda slug: {'cta_url': ''}
    try:
        app.PLAT_DISCORD.send_ppv('lilly', '', '77', 'hi', [], 1200)
        check('with nowhere to send them, it refuses rather than sending a bare caption',
              False, 'no error raised')
    except DR.DiscordApiError:
        check('with nowhere to send them, it refuses rather than sending a bare caption',
              True)


def test_the_cache_is_bounded_and_comes_back():
    runner = DS.install('lilly')
    for n in range(DG.CHAT_CACHE + 25):
        DS.dm(runner, 'hi', fan=str(1000 + n), channel=str(2000 + n))
    check('the chat cache does not grow without limit',
          len(DG.chats('lilly')) == DG.CHAT_CACHE, len(DG.chats('lilly')))
    for n in range(DG.MSG_CACHE + 10):
        DS.dm(runner, f'line {n}', fan='77', channel='500')
    check('nor does one conversation', len(DG.messages('lilly', '77', 999)) == DG.MSG_CACHE)

    DS.uninstall('lilly')
    fresh = DS.install('lilly')
    check('a restart comes back with nothing', DG.chats('lilly') == [])
    DS.ready(fresh, [{'fan': '77', 'handle': 'dave', 'channel': '500', 'last': '9'}])
    check('and READY refills it without a single request',
          [c['fan_id'] for c in DG.chats('lilly')] == ['77'])
    check('with the channel it needs to reply on',
          fresh.channel_for('77') == '500')


def test_a_guild_channel_is_never_imported():
    """A public backlog is not her history with anyone, and reading 200 lines of
    it would be the most expensive thing on the platform."""
    check('no history is imported for a channel', DG.history('lilly', '9:90') == [])


def test_the_fingerprint_agrees_with_itself():
    """The socket and the REST calls have to describe the same client. Two
    stories is the loudest thing an automated account can tell."""
    props = DR.properties()
    rest = DR.Rest('token', props)
    head = rest._headers()
    check('the token goes out bare, never as a bot', head['Authorization'] == 'token')
    check('super-properties are sent', bool(head.get('X-Super-Properties')))
    import base64
    decoded = json.loads(base64.b64decode(head['X-Super-Properties']))
    check('and say exactly what IDENTIFY says', decoded == props)
    runner = DG.Runner('lilly', 'token', props)
    ident = runner._identify()['d']
    check('IDENTIFY sends no intents field', 'intents' not in ident)
    check('IDENTIFY carries the same properties', ident['properties'] == props)
    check('and the token bare there too', ident['token'] == 'token')


def test_a_whole_dm_round():
    """A fan writes, she answers, both sides land in the transcript and the
    cursor stops her answering the same line twice."""
    runner = DS.install('lilly')
    DS.dm(runner, 'hey you', fan='77', handle='dave', channel='500')

    store = {'discord_auto_lilly': json.dumps({'enabled': True, 'humanize': False,
                                               'reply_limit': 5})}
    logged, traced = [], []
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    app._log_x_message = lambda p, k, h, d, t: logged.append((k, d, t))
    app._fanvue_msg_count = lambda p, k: 3
    app._fanvue_saved_history = lambda p, k, limit=40: []
    app._fan_memory = lambda p, k: ''
    app._fan_memory_update = lambda *a, **k: None
    app._fan_memory_block = lambda m, p: ''
    app._funnel_read = lambda *a, **k: ('', {}, None)
    app._persona_text = lambda p, instr, **k: 'hey, how was your day?'
    app._fv_trace = lambda p, st, d='', fan='': traced.append((st, d))
    app._dc_token = lambda p: 'stub-token'

    actions, log = app._plat_auto_round(app.PLAT_DISCORD, 'lilly')
    check('she replied once', actions.get('replies') == 1, (actions, log))
    check('the reply went out on his DM channel',
          runner.rest.sent and runner.rest.sent[-1]['channel_id'] == '500',
          runner.rest.sent)
    check('it is what the model wrote',
          'how was your day' in (runner.rest.sent[-1]['content'] if runner.rest.sent else ''))
    check('his message is stored under the Discord key',
          ('dc:77', 'in', 'hey you') in logged, logged)
    check('so is her reply', any(k == 'dc:77' and d == 'out' for k, d, t in logged), logged)
    check('the cursor is written so it is not answered twice',
          json.loads(store.get('discord_cursor_lilly') or '{}').get('77'), store)
    check('and none of it touched another platform',
          not any(k.startswith(('fanvue_', 'onlyfans_')) for k in store), sorted(store))

    before = len(runner.rest.sent)
    app._plat_auto_round(app.PLAT_DISCORD, 'lilly')
    check('the same message is not answered again', len(runner.rest.sent) == before)


def test_a_click_is_recorded_as_opened_not_paid():
    """Nothing in a redirect can see a purchase. Writing one would make every
    revenue figure downstream a lie."""
    import db
    marked = []
    db_session = type('S', (), {'commit': lambda self: None, 'close': lambda self: None,
                                'query': lambda self, m: self})()
    db_session.filter = lambda *a: db_session
    db_session.order_by = lambda *a: db_session
    db_session.first = lambda: type('R', (), {'id': 'drop1'})()
    app_db = __import__('db')
    real_session, real_mark = app_db.SessionLocal, app_db.mark_ppv_read
    app_db.SessionLocal = lambda: db_session
    app_db.mark_ppv_read = lambda s, drop_id, when=None: marked.append(drop_id)
    try:
        app._dc_mark_opened('lilly', '77')
    finally:
        app_db.SessionLocal, app_db.mark_ppv_read = real_session, real_mark
    check('the newest drop is marked opened', marked == ['drop1'], marked)
    import inspect
    check('and the redirect path cannot mark one paid',
          'paid_at' not in inspect.getsource(app._dc_mark_opened)
          and 'paid_at' not in inspect.getsource(app.discord_cta_click))


def test_a_channel_reply():
    """The public round. It has to answer, log the thread, and never reach for
    the funnel or the offer."""
    runner = DS.install('lilly')
    runner.configure({'allow': [{'guild': '9', 'channel': '90', 'nsfw': False}],
                      'chime': {}})
    DS.channel(runner, 'lilly what are you playing', mention=True)

    store, logged, prompts = {}, [], []
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    app._log_x_message = lambda p, k, h, d, t: logged.append((k, d, t))
    app._fv_trace = lambda p, st, d='', fan='': None
    app._funnel_read = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError('the funnel must not run in a channel'))
    app._persona_text = lambda p, instr, **k: prompts.append(instr) or 'nothing much tbh'
    DG.OUT_FLOOR_SECONDS = 0

    app._dc_channel_round('lilly', '9', '90', True)
    check('she answered in the channel',
          runner.rest.sent and runner.rest.sent[-1]['channel_id'] == '90',
          runner.rest.sent)
    check('the thread is logged under the channel key',
          any(k == 'dcg:9:90' for k, d, t in logged), logged)
    check('a clean channel is told to stay clean',
          prompts and 'stays clean' in prompts[0])
    check('and she is told she is in a group, not a DM',
          prompts and 'several people' in prompts[0])

    prompts.clear()
    runner.configure({'allow': [{'guild': '9', 'channel': '90', 'nsfw': True}]})
    DS.channel(runner, 'lilly again', mention=True)
    app._dc_channel_round('lilly', '9', '90', True)
    check('an adult channel is not', prompts and 'stays clean' not in prompts[0])

    prompts.clear()
    runner.configure({'allow': []})
    app._dc_channel_round('lilly', '9', '90', True)
    check('a channel taken off the allowlist gets nothing', not prompts, prompts)


def test_signing_in_through_the_browser():
    """The sign-in relay, driven as Discord rather than as OnlyFans. Nothing
    here opens a browser — what is checked is that the site is carried through
    and that the two never tread on each other."""
    import of_connect

    check('discord is a site the relay knows', 'discord' in of_connect.SITES)
    check('and it opens Discord, not OnlyFans',
          of_connect.SITES['discord']['url'].startswith('https://discord.com'))
    check('an attempt built without a site still answers as one',
          of_connect.Attempt.site == 'onlyfans')

    made = of_connect.Attempt.__new__(of_connect.Attempt)
    made.site = 'discord'
    made._site = of_connect.SITES['discord']
    made._dc_seen = {'token': 'tok-abc',
                     'super_properties': __import__('base64').b64encode(
                         json.dumps({'client_build_number': 4242}).encode()).decode(),
                     'user_agent': 'Mozilla/5.0 test'}
    made._dc_caps = 16381
    made.proxy = ''
    made.account = 'dc_lilly'
    made.capture_note = ''
    made.result = {}
    made.probes = 0
    made.state = 'signin'
    made._done = __import__('threading').Event()
    made._pending_session = None

    class Page:
        def __init__(self, me):
            self.me = me

        def evaluate(self, js):
            return self.me

    made._capture_discord(Page({}), None)
    check('a page that is not signed in yet finishes nothing',
          made.state == 'signin' and made.capture_note == 'awaiting_login')

    made._capture_discord(Page({'id': '4242', 'username': 'lilly'}), None)
    check('once it is, the sign-in is done', made.state == 'connected')
    held = made._pending_session
    check('the token comes back', (held or {}).get('token') == 'tok-abc')
    check('with the build the real client used, not a guess',
          (held or {}).get('build') == 4242)
    check('and the capabilities it really identified with',
          (held or {}).get('capabilities') == 16381)
    check('claim hands it over exactly once',
          of_connect.claim(made) == held and of_connect.claim(made) is None)

    check('the app keys Discord accounts apart from OnlyFans',
          app._dc_account_id('lilly') == 'dc_lilly')


def test_the_winback_ladder():
    """What happens after the two short nudges are spent. Days 1 and 4 are a
    plain hello; the offer rungs come later and carry the link."""
    check('only Discord opted in',
          (app.PLAT_DISCORD.has_winback, app.PLAT_FANVUE.has_winback,
           app.PLAT_ONLYFANS.has_winback) == (True, False, False))
    check('and the other platforms have nowhere for an offer to point',
          app.PLAT_FANVUE.winback_link('lilly', '1') == '')

    step = growth_step = __import__('growth').winback_step
    check('nothing is due on the first quiet day with no nudges spent',
          step(0, 0) is None)
    check('day 1 is the first rung, and it is a message',
          step(1, 0) == {'touch': 1, 'offer': False})
    check('day 4 is the second, still a message',
          step(4, 1) == {'touch': 2, 'offer': False})
    check('the third rung is the first that offers anything',
          (step(12, 2) or {}).get('offer') is True)
    import funnels
    check('and the ladder runs out rather than going forever',
          step(9999, funnels.WINBACK_MAX_TOUCHES) is None)

    saved = {}
    app._get_setting = lambda k, d=None: saved.get(k, d)
    app._set_setting = lambda k, v: saved.__setitem__(k, v)
    saved['discord_winback_lilly'] = json.dumps({'77': {'n': 2}})
    check('where a fan has got to is read back per platform',
          app._plat_winback(app.PLAT_DISCORD, 'lilly').get('77', {}).get('n') == 2)
    check('and that key is namespaced like every other bit of state',
          app.PLAT_DISCORD.k('winback', 'lilly') == 'discord_winback_lilly')

    runner = DS.install('lilly')
    app._phases_cta = lambda slug: {'cta_url': 'https://paid.example/lilly'}
    app._get_setting = lambda k, d=None: (
        '' if k.startswith('discord_cta') else
        'https://app.example' if k == 'public_base_url' else saved.get(k, d))
    check('an offer rung points at her tracked link, not the raw page',
          app.PLAT_DISCORD.winback_link('lilly', '77')
          == 'https://app.example/go/dc/lilly/77')


def test_posting_on_a_schedule():
    """The clock-driven post. It must respect the allowlist, the interval and
    the same daily budget as joining in, and must never carry a link."""
    runner = DS.install('lilly')
    allow = [{'guild': '9', 'channel': '90', 'nsfw': False, 'post': True},
             {'guild': '9', 'channel': '91', 'nsfw': False, 'post': False}]
    runner.configure({'allow': allow, 'chime': {}})

    store = {'discord_guilds_lilly': json.dumps({'allow': allow}),
             'discord_chime_lilly': json.dumps({'enabled': False, 'daily_cap': 1}),
             'discord_post_lilly': json.dumps({'enabled': True, 'interval_min': 30,
                                               'brief': 'her day at the gym'})}
    prompts = []
    app._get_setting = lambda k, d=None: store.get(k, d)
    app._set_setting = lambda k, v: store.__setitem__(k, v)
    app._log_x_message = lambda *a: None
    app._fv_trace = lambda p, st, d='', fan='': None
    app._persona_text = lambda p, instr, **k: prompts.append(instr) or 'gym was brutal today'
    DG.OUT_FLOOR_SECONDS = 0

    check('it posts once', app._dc_post_round('lilly') == 1)
    check('into the channel marked scheduled, and only that one',
          [r['channel_id'] for r in runner.rest.sent] == ['90'], runner.rest.sent)
    check('the brief reaches the model', prompts and 'gym' in prompts[0])
    check('and it is told never to post a link', prompts and 'never a link' in prompts[0])

    check('it does not post again inside the interval',
          app._dc_post_round('lilly') == 0)

    store['discord_post_state_lilly'] = json.dumps({})
    check('and not once the daily budget is spent either',
          app._dc_post_round('lilly') == 0)

    store['discord_post_state_lilly'] = json.dumps({})
    store['discord_chime_lilly'] = json.dumps({'enabled': False, 'daily_cap': 0})
    store['discord_chime_state_lilly'] = json.dumps({})
    check('a cap of zero silences the schedule too, not just chiming in',
          app._dc_post_round('lilly') == 0)

    store['discord_post_lilly'] = json.dumps({'enabled': False})
    store['discord_post_state_lilly'] = json.dumps({})
    check('and it does nothing at all when switched off',
          app._dc_post_round('lilly') == 0)


if __name__ == '__main__':
    for fn in (test_adapter_shape, test_a_dm_is_taken, test_the_mention_gate,
               test_chiming_in_is_capped, test_the_daily_cap_survives_a_restart,
               test_sending_and_pacing, test_a_link_tier_is_allowed_where_media_is_not,
               test_the_offer_only_ever_goes_to_a_dm,
               test_the_cache_is_bounded_and_comes_back,
               test_a_guild_channel_is_never_imported,
               test_the_fingerprint_agrees_with_itself,
               test_a_whole_dm_round,
               test_a_click_is_recorded_as_opened_not_paid,
               test_a_channel_reply,
               test_posting_on_a_schedule,
               test_the_winback_ladder,
               test_signing_in_through_the_browser):
        restore()
        fn()
    restore()
    print()
    if FAILURES:
        print('FAILED: ' + ', '.join(FAILURES))
        raise SystemExit(1)
    print('All checks passed.')
