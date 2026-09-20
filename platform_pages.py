"""Public marketing pages, one per platform, rendered from templates/platform.html.

Separate from the signed-in consoles at /telegram and /fanvue: these are the SEO
landing pages a stranger arrives on, so the slugs carry the keyword rather than
the platform name on its own.
"""

import json


def _faq_schema(faq):
    return json.dumps({
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        'mainEntity': [{'@type': 'Question', 'name': f['q'],
                        'acceptedAnswer': {'@type': 'Answer', 'text': f['a']}}
                       for f in faq],
    })


# Every page carries the same set of sibling links, minus itself.
_ALL = [
    {'slug': 'telegram-ai-chatbot', 'name': 'Telegram', 'tag': 'Private fan DMs with the full funnel', 'status': 'Live'},
    {'slug': 'fanvue-ai-chatter', 'name': 'Fanvue', 'tag': 'The paid page your funnel lands on', 'status': 'Live'},
    {'slug': 'discord-ai-chatbot', 'name': 'Discord', 'tag': 'DM conversations, clean servers', 'status': 'Live'},
    {'slug': 'x-ai-bot', 'name': 'X (Twitter)', 'tag': 'Posts, replies and a DM funnel', 'status': 'Live'},
    {'slug': 'instagram-posting-automation', 'name': 'Instagram', 'tag': 'Posts, Stories and Reels on schedule', 'status': 'Live'},
    {'slug': 'threads-auto-reply', 'name': 'Threads', 'tag': 'Posting and comment auto-reply', 'status': 'Live'},
    {'slug': 'velvetchat-share-link', 'name': 'Velvetchat', 'tag': 'Your own chat page and embed', 'status': 'Live'},
    {'slug': 'reddit-posting-bot', 'name': 'Reddit', 'tag': 'Per-subreddit posting and DMs', 'status': 'Soon'},
]


def _related(slug, picks):
    by_slug = {p['slug']: p for p in _ALL}
    return [{'href': '/' + s, 'name': by_slug[s]['name'], 'tag': by_slug[s]['tag'],
             'status': by_slug[s]['status']} for s in picks if s != slug]


PAGES = {
    'telegram-ai-chatbot': {
        'slug': 'telegram-ai-chatbot',
        'live': True,
        'title': 'Telegram AI Chatbot for Creators | Velvetfunnel',
        'description': ('Give every persona her own Telegram bot. Automated fan DMs, memory, photo '
                        'teasing and a built-in conversion funnel that sells your paid page 24/7.'),
        'eyebrow': 'Live now · DM funnel',
        'h1_pre': 'Telegram AI chatbot for',
        'h1_accent': 'fan DMs.',
        'lede': [
            'Every persona gets her own Telegram bot, so fans message her directly in a private one '
            'to one chat. Telegram is the strongest converting channel in the stack because the '
            'conversation is private, uninterrupted and yours.',
            'The full funnel runs here. She warms fans up, remembers past chats, attaches a preview '
            'photo when a fan asks to see more, and sends your paid link once the fan is ready.',
        ],
        'cta_primary': 'Start free',
        'hero_note': 'Live in minutes. Create the bot with BotFather, paste the token, and she is running.',
        'route_title': 'The route, end to end',
        'route': [
            {'label': 'A fan finds you', 'detail': 'From your X, Instagram or Threads bio, through a tracked link.'},
            {'label': 'She opens the chat', 'detail': 'Your persona answers in her own voice, in a private Telegram DM.'},
            {'label': 'The funnel runs', 'detail': 'Warm, engage, intrigue, tease, offer, close, at the pace you set.'},
            {'label': 'They land on your paid page', 'detail': 'The CTA goes out in character, with a preview photo attached when it earns one.'},
        ],
        'route_foot': 'Destination: <b>your Fanvue page</b>. OnlyFans and Fansly are next.',
        'features_eyebrow': 'What she does',
        'features_h2_pre': 'A real inbox, worked',
        'features_h2_accent': 'around the clock',
        'features_sub': ('Everything below runs server side on every account you own. No open tab, no '
                         'browser, no chatter sitting in a shift.'),
        'features': [
            {'icon': '💬', 'title': 'Replies in your voice',
             'body': 'Sentence length, punctuation, emoji use and typing speed are all set by you in the persona builder. Telegram paces its own replies, so she never reads like a bot.'},
            {'icon': '🧠', 'title': 'Remembers every fan',
             'body': 'She brings back what a fan told her last week and picks the thread up where it stopped. No conversation ever restarts from zero.'},
            {'icon': '📸', 'title': 'Photo-backed offers',
             'body': 'Once a fan has asked to see more enough times to mean it, the CTA goes out with a preview photo from your library attached, not a bare link.'},
            {'icon': '🎯', 'title': 'Handles the objection',
             'body': 'A fan who was sent the paid link and never opened it is hesitating. She sends a free trial or a discount code next time instead of repeating the same ask.'},
            {'icon': '⏰', 'title': 'Wins quiet fans back',
             'body': 'When a fan stops replying she returns on day 1, 4, 12 and 30, then quarterly. The first messages are plain conversation, the link only comes later.'},
            {'icon': '📊', 'title': 'Logs and scores everything',
             'body': 'Every conversation is logged with its funnel stage, so you can see who is warming up and which channel actually sent them.'},
        ],
        'how_h2_pre': 'Connected in',
        'how_h2_accent': 'three steps',
        'how_sub': 'No servers, no code, no browser left open on a machine somewhere.',
        'steps': [
            {'title': 'Build the persona', 'body': 'Fill in her identity, voice and funnel pacing in the visual builder. Or describe her in a sentence and let the AI draft it for you to edit.'},
            {'title': 'Create the bot', 'body': 'Open BotFather in Telegram, send /newbot, and paste the token into Velvetfunnel. That is the whole connection.'},
            {'title': 'Send traffic', 'body': 'Put your tracked Telegram link in the bios she already posts to. Every fan who opens it is credited to the channel that found them.'},
        ],
        'rows_eyebrow': 'Where it fits',
        'rows_h2_pre': 'Telegram against',
        'rows_h2_accent': 'the other channels',
        'rows_sub': 'Each platform does a different job. Telegram is where the conversation gets private enough to close.',
        'rows': [
            {'k': 'Telegram', 'v': 'Private DM, no algorithm between you and the fan, no posting requirement. The strongest converting channel in the stack.'},
            {'k': 'Discord', 'v': 'Also a DM funnel, but public server channels stay clean: no funnel, no links, no CTA, ever.'},
            {'k': 'X (Twitter)', 'v': 'Public posts and replies that build reach, plus a DM funnel that converts it. The one channel that does both.'},
            {'k': 'Instagram and Threads', 'v': 'Reach only. They fill the funnel and hand fans to Telegram through a tracked bio link.'},
            {'k': 'Fanvue', 'v': 'The destination. Once a fan subscribes, the same persona keeps working inside that inbox and sells PPV from your vault.'},
        ],
        'faq': [
            {'q': 'Will fans know they are talking to an AI?',
             'a': 'That is your call and your setting. The persona is built to stay in character, and every chat Velvetfunnel hosts on your own page is labelled as an AI chat. On Telegram the bot is your bot, so you decide what its description says.'},
            {'q': 'Do I need to keep a browser or a tab open?',
             'a': 'No. The Telegram loop runs server side and stays on through deploys. Your machine can be off.'},
            {'q': 'Can I run more than one persona?',
             'a': 'Yes. Each persona gets her own bot and her own token, and they run at the same time without sharing memory or settings.'},
            {'q': 'What stops her sending the offer too early?',
             'a': 'The funnel is phased. You set how long each phase lasts and how often she sends photos, and the paid link only fires once a fan reaches the final phase.'},
            {'q': 'Does she repeat herself over a long conversation?',
             'a': 'No. Every message she sends is logged and fed back as context, and when a fan tells her to drop a subject she writes it down and never raises it again, with him or with anyone, on any platform.'},
            {'q': 'Where does the paid link point?',
             'a': 'Your Fanvue page today, with OnlyFans and Fansly landing next. You can also point a channel at your own Velvetchat page instead, and change the destination later without touching a single bio.'},
        ],
        'related': _related('telegram-ai-chatbot', ['fanvue-ai-chatter', 'discord-ai-chatbot', 'x-ai-bot', 'velvetchat-share-link']),
        'close_h2': 'Put her on Telegram tonight',
        'close_sub': 'Build the persona once and she works the inbox from then on, in your voice, on every account you own.',
    },

    'fanvue-ai-chatter': {
        'slug': 'fanvue-ai-chatter',
        'live': True,
        'title': 'Fanvue AI Chatter & PPV Automation | Velvetfunnel',
        'description': ('Fanvue is where your funnel lands. An AI chatter works your subscriber inbox, '
                        'sells PPV from your vault and keeps every paying fan engaged 24/7.'),
        'eyebrow': 'Live now · Paid destination',
        'h1_pre': 'Fanvue AI chatter and',
        'h1_accent': 'PPV automation.',
        'lede': [
            'Fanvue is the paid page every other channel points at. Your social platforms build the '
            'audience, the DM funnel warms them up, and Fanvue is where they subscribe.',
            'Velvetfunnel does not stop at the paywall. The same persona keeps working inside your '
            'Fanvue inbox, reading incoming messages and replying in character, so a fan who just '
            'paid gets the same voice they were talking to an hour ago.',
        ],
        'cta_primary': 'Start free',
        'hero_note': 'Runs server side, through deploys, with no open tab and no browser running.',
        'route_title': 'Where Fanvue sits',
        'route': [
            {'label': 'Traffic arrives', 'detail': 'Telegram, Discord, X, Instagram and Threads build the audience.'},
            {'label': 'The funnel warms them', 'detail': 'Your persona works the DM until the fan is ready to pay.'},
            {'label': 'They subscribe on Fanvue', 'detail': 'The CTA lands them on your page, credited to the channel that found them.'},
            {'label': 'PPV starts earning', 'detail': 'The same persona works the subscriber inbox and sells from your vault.'},
        ],
        'route_foot': 'Subscription is the floor. <b>PPV is where the margin is.</b>',
        'features_eyebrow': 'Inside the inbox',
        'features_h2_pre': 'The part a human chatter',
        'features_h2_accent': 'is too expensive to work',
        'features_sub': ('Subscription revenue arrives on its own. Everything after it depends on somebody '
                         'answering fast, in character, at three in the morning.'),
        'features': [
            {'icon': '🗄️', 'title': 'Sells from your vault',
             'body': 'Connect your Fanvue vault and she attaches real content to the offer. Browse folders, pick media, send PPV, all from the same dashboard.'},
            {'icon': '🔁', 'title': 'Never sends the same thing twice',
             'body': 'Every PPV drop is tracked per fan, so nothing goes out to somebody who already bought it.'},
            {'icon': '🎚️', 'title': 'Her own pacing',
             'body': 'Fanvue paces its replies independently of your other channels, so she reads right for people who already subscribe rather than for strangers.'},
            {'icon': '⭐', 'title': 'Fan scoring',
             'body': 'Every subscriber is scored by engagement, so you know who is close to the next buy before you spend a message on them.'},
            {'icon': '📈', 'title': 'Funnel and PPV analytics',
             'body': 'See which stage each fan sits in, which conversations are warming toward a drop, and what every drop earned.'},
            {'icon': '🛰️', 'title': 'One worker per account',
             'body': 'Busy accounts each get their own worker, so one full inbox never holds up another. It stays on through redeploys.'},
        ],
        'how_h2_pre': 'Connected in',
        'how_h2_accent': 'three steps',
        'how_sub': 'Approve once, and the inbox is worked from then on.',
        'steps': [
            {'title': 'Connect Fanvue', 'body': 'Approve the connection from the Fanvue console in your dashboard. She can then read her inbox and send as herself.'},
            {'title': 'Point your vault at it', 'body': 'Pick the folders she is allowed to sell from, and the price bands she can offer them at.'},
            {'title': 'Turn auto-reply on', 'body': 'Switch it on per persona. From that moment the inbox is answered around the clock, with every conversation logged.'},
        ],
        'rows_eyebrow': 'The destination',
        'rows_h2_pre': 'Where your funnel',
        'rows_h2_accent': 'can land',
        'rows_sub': 'The funnel is built to land anywhere. Fanvue is the paid page supported end to end today.',
        'rows': [
            {'k': 'Fanvue', 'v': 'Live. Subscriber inbox auto-reply, PPV from your vault, fan scoring and funnel analytics.'},
            {'k': 'OnlyFans', 'v': 'In production. Auto-reply and PPV delivery running the same funnel and the same persona. The console and reply loop are built and waiting on release.'},
            {'k': 'Fansly', 'v': 'In production. Next after OnlyFans, so creators who earn there point the same funnel at the same page instead of maintaining two setups.'},
            {'k': 'Velvetchat', 'v': 'Live. Your own chat page on property you control, for channels where a paysite link would get filtered.'},
            {'k': 'Per persona routing', 'v': 'You choose which paid page each persona sends fans to, and change it later without touching a single bio.'},
        ],
        'faq': [
            {'q': 'Does this replace my chatters?',
             'a': 'It covers the volume they cannot: instant replies, overnight, on every conversation at once. Plenty of creators run it alongside a human who steps in on the biggest spenders.'},
            {'q': 'Can it send PPV on its own?',
             'a': 'Yes, from the vault folders you allow, at the price bands you set. Every send is logged and nothing is sent twice to the same fan.'},
            {'q': 'What happens if Fanvue is slow or the connection drops?',
             'a': 'A network failure is treated as a blip and the connection is kept. Only a refusal Fanvue calls final clears the session, because losing it means approving again for nothing.'},
            {'q': 'Does the persona change after a fan pays?',
             'a': 'No, and that is the point. The voice a fan subscribed to is the voice waiting for them inside, with the same memory of what they already talked about.'},
            {'q': 'Do I need to keep a tab open?',
             'a': 'No. It runs server side and stays on through deploys.'},
            {'q': 'Will OnlyFans and Fansly work the same way?',
             'a': 'Yes. Same funnel, same persona, same vault logic, with the destination picked per persona. Both are built and finishing their access work.'},
        ],
        'related': _related('fanvue-ai-chatter', ['telegram-ai-chatbot', 'discord-ai-chatbot', 'x-ai-bot', 'velvetchat-share-link']),
        'close_h2': 'Stop losing PPV to a slow reply',
        'close_sub': 'Your subscribers are already inside. The difference is whether somebody answers them in ten seconds or ten hours.',
    },
}

for _slug, _page in PAGES.items():
    _page['faq_schema'] = _faq_schema(_page['faq'])
