"""Social-to-subscriber growth layer: where a fan came from, which link they get
next, and what has already been posted at them.

Pure logic — no Flask, no database, no network — so app.py stays the only place
that touches request state. Everything here is behind the operator beta gate
(see in_beta); nothing changes for a creator until their slug is switched on.
"""
import json
import re

from funnels import WINBACK_DAYS, WINBACK_MAX_TOUCHES, winback_due, winback_is_offer

# ── Beta gate ─────────────────────────────────────────────────────────────────
# One app_settings row, written only by an operator. While a slug is off this
# module's behaviour is inert: the CTA falls back to the single paid link, no
# source is recorded and the win-back ladder never fires.
BETA_KEY = 'growth_beta_personas'


def beta_slugs(raw):
    """The slugs switched on, from the stored setting or from what an operator
    typed. The settings table is schemaless, so this reads a JSON list as
    readily as a comma-separated string."""
    if isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        text = str(raw or '').strip()
        items = None
        if text.startswith('['):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    items = parsed
            except Exception:
                items = None
        if items is None:
            items = re.split(r'[,\s]+', text)
    return {s.strip().lower() for s in items if isinstance(s, str) and s.strip()}


def in_beta(raw, slug):
    slugs = beta_slugs(raw)
    return bool(slug) and ('*' in slugs or str(slug).lower() in slugs)


# ── CTA: paid link, free-trial link, promo code ───────────────────────────────
# A fan who was sent the link and never opened it is hesitating, and the guide's
# answer to hesitation is a lower barrier — the free trial, plus the promo code
# if one is running. The first touch stays the plain paid link.
_DATE_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')


def clean_cta(data, existing=None):
    """Validate a saved CTA block. Unknown keys are dropped; anything missing
    from `data` keeps the value already stored."""
    old = existing or {}

    def s(key, cap, default=''):
        val = data.get(key)
        if val is None:
            return str(old.get(key, default) or '')[:cap].strip()
        return str(val)[:cap].strip()

    out = {
        'cta_url': s('cta_url', 500),
        'cta_label': s('cta_label', 120),
        'trial_url': s('trial_url', 500),
        'trial_label': s('trial_label', 120),
        'promo_code': s('promo_code', 40),
        'promo_expires': s('promo_expires', 10),
    }
    if not _DATE_RE.match(out['promo_expires']):
        out['promo_expires'] = ''

    # An optional link per channel, for the persona whose X audience should land
    # somewhere other than her Instagram one. Absent keys keep what was stored,
    # so saving one channel does not silently clear the rest.
    per = data.get('platform_urls')
    urls = dict((old.get('platform_urls') or {}))
    if isinstance(per, dict):
        for source, url in per.items():
            src = normalise_source(source)
            url = str(url or '').strip()[:500]
            if not src:
                continue
            if url:
                urls[src] = url
            else:
                urls.pop(src, None)
    out['platform_urls'] = urls
    return out


def promo_live(cta, today=''):
    """The promo code, if one is set and has not run out. `today` is an ISO date
    string; an empty one means "no clock", which keeps the code live."""
    code = (cta or {}).get('promo_code') or ''
    if not code:
        return ''
    expires = (cta or {}).get('promo_expires') or ''
    if expires and today and today > expires:
        return ''
    return code


def hesitating(fan):
    """The link went out and was never opened."""
    fan = fan or {}
    return bool(fan.get('cta_sent')) and not fan.get('cta_clicked')


def cta_choice(cta, fan, beta=True, today='', source=''):
    """Which link goes out with this reply.

    Returns {'url', 'label', 'kind', 'promo'}. Off the beta gate — or with no
    trial link configured — this is exactly the old single-link behaviour.

    `source` is the channel the fan arrived through. When that channel has its
    own link it replaces the paid one; the trial link stays shared, because a
    trial has a use count and splitting it per channel spends it faster than
    anyone intends.
    """
    cta = cta or {}
    source = source or (fan or {}).get('source') or ''
    label = (cta.get('cta_label') or '').strip()
    base = (cta.get('cta_url') or '').strip()
    if not beta:
        return {'url': base, 'label': label, 'kind': 'paid', 'promo': ''}
    paid = platform_cta(cta, source) or base
    plain = {'url': paid, 'label': label, 'kind': 'paid', 'promo': ''}
    if not hesitating(fan):
        return plain
    trial = (cta.get('trial_url') or '').strip()
    promo = promo_live(cta, today)
    if not trial and not promo:
        return plain
    if not trial:
        return {'url': paid, 'label': label, 'kind': 'paid', 'promo': promo}
    return {'url': trial, 'kind': 'trial', 'promo': promo,
            'label': (cta.get('trial_label') or label).strip()}


# Where a click on each channel's link should land. Cold traffic goes to the
# chat to be warmed up; X is the one channel whose audience already knows who
# she is, so it goes straight at the paid page.
DEFAULT_ROUTES = {
    'instagram': 'chat', 'tiktok': 'chat', 'reddit': 'chat', 'youtube': 'chat',
    'linktree': 'chat', 'other': 'chat',
    'x': 'paid', 'threads': 'paid', 'telegram': 'paid',
}

ROUTE_KINDS = ('chat', 'paid', 'trial')


def clean_routes(data, existing=None):
    """Validate a saved routing table. Only known kinds survive, so a typo in
    the panel cannot send a channel somewhere that resolves to nothing."""
    out = dict(existing or {})
    for source, kind in (data or {}).items():
        src = normalise_source(source)
        if not src:
            continue
        kind = str(kind or '').strip().lower()
        if kind in ROUTE_KINDS:
            out[src] = kind
        elif not kind:
            out.pop(src, None)
    return out


def route_for(routes, source):
    """Where this channel's link goes. An unconfigured channel warms up in the
    chat rather than landing on a paywall — the wrong guess there costs a fan,
    and the other way costs nothing but a message."""
    src = normalise_source(source)
    kind = (routes or {}).get(src)
    if kind in ROUTE_KINDS:
        return kind
    return DEFAULT_ROUTES.get(src, 'chat')


def platform_cta(cta, source):
    """The channel's own link, when one is set. Most personas run a single link
    everywhere, so this is empty far more often than not."""
    per = (cta or {}).get('platform_urls') or {}
    return str(per.get(normalise_source(source)) or '').strip()


def cta_suffix(choice):
    """The line appended after her message. The link itself is added by the
    caller, which is the only place that knows the tracked redirect."""
    label = (choice.get('label') or '').strip()
    promo = (choice.get('promo') or '').strip()
    if promo:
        label = f'{label} (code {promo})' if label else f'code {promo}'
    return label


# ── Attribution ───────────────────────────────────────────────────────────────
_SOURCE_RE = re.compile(r'[^a-z0-9._-]+')

_REFERRER_HOSTS = {
    'instagram.com': 'instagram', 'l.instagram.com': 'instagram',
    'tiktok.com': 'tiktok', 'vm.tiktok.com': 'tiktok',
    'x.com': 'x', 'twitter.com': 'x', 't.co': 'x',
    'threads.net': 'threads', 'threads.com': 'threads',
    'reddit.com': 'reddit', 'out.reddit.com': 'reddit',
    'youtube.com': 'youtube', 'youtu.be': 'youtube',
    't.me': 'telegram', 'telegram.me': 'telegram',
    'fanvue.com': 'fanvue',
    'linktr.ee': 'linktree', 'beacons.ai': 'beacons',
}


def normalise_source(value, cap=40):
    """Lowercase, strip anything that is not link-safe, cap the length. Returns
    '' for junk, which the callers treat as "no source"."""
    out = _SOURCE_RE.sub('-', str(value or '').strip().lower()).strip('-')
    return out[:cap]


def source_from_referrer(referrer):
    """Map a referring URL to a channel name. Falls back to the bare host so an
    unknown referrer is still worth something."""
    m = re.match(r'^https?://([^/:?#]+)', str(referrer or '').strip(), re.I)
    if not m:
        return ''
    host = m.group(1).lower()
    host = host[4:] if host.startswith('www.') else host
    if host in _REFERRER_HOSTS:
        return _REFERRER_HOSTS[host]
    parts = host.split('.')
    if len(parts) >= 2:
        parent = '.'.join(parts[-2:])
        if parent in _REFERRER_HOSTS:
            return _REFERRER_HOSTS[parent]
    return normalise_source(host)


def source_from(params, referrer=''):
    """Read the channel off a request. Explicit tags beat the referrer, because
    a creator who tagged their own link knows better than the host header."""
    params = params or {}

    def first(*keys):
        for k in keys:
            v = normalise_source(params.get(k))
            if v:
                return v
        return ''

    source = first('utm_source', 'src', 'source', 'ref', 's')
    if not source:
        source = source_from_referrer(referrer)
    if not source:
        return {}
    return {'source': source,
            'medium': first('utm_medium', 'medium'),
            'campaign': first('utm_campaign', 'campaign', 'c')}


def pack_source(attr):
    """Flatten to one token that survives a cookie and a Telegram start payload
    (which allows only A-Za-z0-9_-, 64 chars)."""
    attr = attr or {}
    parts = [normalise_source(attr.get('source')),
             normalise_source(attr.get('medium')),
             normalise_source(attr.get('campaign'))]
    while parts and not parts[-1]:
        parts.pop()
    if not parts or not parts[0]:
        return ''
    return '_'.join(p or '-' for p in parts)[:60]


def unpack_source(packed):
    bits = [b for b in str(packed or '').split('_')]
    keys = ('source', 'medium', 'campaign')
    out = {}
    for key, bit in zip(keys, bits):
        val = normalise_source(bit)
        if val and val != '-':
            out[key] = val
    return out if out.get('source') else {}


# The persona code and the source share one Telegram start payload. '--' is a
# legal payload character and never appears in a generated persona code.
START_SEP = '--'


def split_start_payload(payload):
    """('code', 'packed source') from a t.me start payload."""
    raw = str(payload or '').strip()
    if START_SEP in raw:
        code, _, src = raw.partition(START_SEP)
        return code.strip(), src.strip()[:60]
    return raw, ''


def join_start_payload(code, packed):
    return f'{code}{START_SEP}{packed}' if packed else str(code or '')


# ── Content register ──────────────────────────────────────────────────────────
# What has already gone out, per platform, so the generator stops rewriting the
# same post. Kept small on purpose: it is prompt context, not an archive.
REGISTER_CAP = 60
REGISTER_SHOWN = 8

_WORD_RE = re.compile(r"[a-z0-9']+")
_STOP = {'the', 'a', 'an', 'and', 'or', 'but', 'to', 'of', 'in', 'on', 'at',
         'for', 'with', 'is', 'it', 'im', 'i', 'you', 'me', 'my', 'your',
         'this', 'that', 'so', 'just', 'be', 'are', 'was'}


def _words(text):
    return {w for w in _WORD_RE.findall(str(text or '').lower())
            if w not in _STOP and len(w) > 2}


def register_add(entries, platform, text, ts=0, cap=REGISTER_CAP):
    """Prepend one posted item. Newest first, oldest dropped past the cap."""
    text = str(text or '').strip()
    if not text:
        return list(entries or [])
    row = {'platform': normalise_source(platform) or 'other',
           'text': text[:400], 'ts': int(ts or 0)}
    return ([row] + [e for e in (entries or []) if isinstance(e, dict)])[:cap]


def register_recent(entries, platform='', limit=REGISTER_SHOWN):
    plat = normalise_source(platform)
    out = []
    for e in (entries or []):
        if not isinstance(e, dict):
            continue
        if plat and normalise_source(e.get('platform')) != plat:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def is_repeat(entries, platform, text, threshold=0.6):
    """True when this reads like something already posted on the same platform.
    Word overlap rather than exact match: the generator paraphrases itself."""
    new = _words(text)
    if len(new) < 3:
        return False
    for e in register_recent(entries, platform, limit=REGISTER_SHOWN * 2):
        old = _words(e.get('text'))
        if not old:
            continue
        overlap = len(new & old) / float(len(new | old))
        if overlap >= threshold:
            return True
    return False


def register_stats(entries, now=0):
    """Totals and a per-platform breakdown of what she has posted. The register
    is a rolling window, so this counts what it still holds rather than all time
    — the point is which channels are being fed and which have gone quiet."""
    now = int(now or 0)
    week = now - 7 * 86400
    per = {}
    totals = {'posts': 0, 'posts_7d': 0, 'platforms': 0, 'last': 0}
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        plat = normalise_source(e.get('platform')) or 'other'
        ts = int(e.get('ts') or 0)
        row = per.setdefault(plat, {'platform': plat, 'posts': 0, 'posts_7d': 0, 'last': 0})
        row['posts'] += 1
        totals['posts'] += 1
        if ts and ts >= week:
            row['posts_7d'] += 1
            totals['posts_7d'] += 1
        row['last'] = max(row['last'], ts)
        totals['last'] = max(totals['last'], ts)
    quiet = lambda last: int((now - last) // 86400) if (now and last) else None
    rows = sorted(per.values(), key=lambda r: (-r['posts'], r['platform']))
    for row in rows:
        row['quiet_days'] = quiet(row['last'])
        row['share'] = round(100.0 * row['posts'] / totals['posts']) if totals['posts'] else 0
    totals['platforms'] = len(rows)
    totals['quiet_days'] = quiet(totals['last'])
    return {'totals': totals, 'platforms': rows}


def register_block(entries, platform, limit=REGISTER_SHOWN):
    """The "do not repeat these" paragraph injected into a generation prompt."""
    recent = register_recent(entries, platform, limit)
    if not recent:
        return ''
    lines = '\n'.join(f'- {(e.get("text") or "")[:160]}' for e in recent)
    return ('\n\nYou have already posted these recently — say something new, do '
            'not rework any of them, and do not reuse their opening line:\n'
            f'{lines}')


# ── The post queue ────────────────────────────────────────────────────────────
# One idea becomes a different post on every channel. The brief is what tells
# the generator how each one differs; the cap is what the channel will take.
POST_PLATFORMS = {
    'x': {
        'label': 'X',
        'cap': 280,
        'brief': ('a single tweet: one hook line that stands on its own, an '
                  'invitation to reply, at most one hashtag and no @mentions'),
    },
    'threads': {
        'label': 'Threads',
        'cap': 500,
        'brief': ('one or two casual sentences that end on a real question, at '
                  'most one emoji, no hashtags'),
    },
    'instagram': {
        'label': 'Instagram',
        'cap': 2200,
        'brief': ('a Reel caption: a POV hook inside the first six words, then '
                  'one line of story, then "link in bio" on its own line'),
    },
    'instagram_carousel': {
        'label': 'Instagram carousel',
        'cap': 2200,
        'brief': ('a carousel micro-story across 3-5 slides, one short line per '
                  'slide numbered "1." to "5.", the first slide a hook and the '
                  'last hinting that the rest of it lives somewhere else'),
    },
    'tiktok': {
        'label': 'TikTok',
        'cap': 2200,
        'brief': ('a curiosity caption of 300-400 characters that withholds the '
                  'payoff, reads as a diary entry, and never names a paid site'),
    },
    'fanvue': {
        'label': 'Fanvue',
        'cap': 5000,
        'brief': ('a post for people who already subscribe: no hook written at '
                  'strangers, no "link in bio", no explaining who you are. Talk '
                  'about what is in front of them and what else is waiting'),
    },
    'reddit': {
        'label': 'Reddit',
        'cap': 300,
        'brief': ('a plain, flat title with no emoji, no hashtags, no sales '
                  'language and no link — the sort a real person types'),
    },
}

# What the platform can actually publish on its own. The rest are written here
# and posted by hand, which is why they are generated but never queued.
PUBLISHABLE = ('x', 'threads', 'fanvue')

# A variant is a second way to write for a channel that already exists, not a
# channel of its own, so anything keyed per channel — the content level, the
# tracked link, the no-repeat log — resolves through its base. Kept out of the
# default set so "write me everything" stays the five real channels.
VARIANT_BASE = {'instagram_carousel': 'instagram'}
OPTIONAL_VARIANTS = tuple(VARIANT_BASE)


def base_platform(platform):
    plat = normalise_source(platform)
    return VARIANT_BASE.get(plat, plat)


def default_platforms():
    return [p for p in POST_PLATFORMS if p not in VARIANT_BASE]


# On a video the words on screen and the words in the caption do different jobs:
# the overlay has to stop the scroll inside two seconds, the caption is read
# afterwards if at all. Both come out of one reply so they stay about one post.
# A carousel is excluded on purpose — its slides are already the words on screen.
OVERLAY_PLATFORMS = ('instagram', 'tiktok')
OVERLAY_CAP = 90

OVERLAY_BRIEF = (
    '\n\nReturn exactly two labelled sections and nothing else:\n'
    f'OVERLAY: the words on screen in the first two seconds, under {OVERLAY_CAP} '
    'characters, written to stop someone scrolling.\n'
    'CAPTION: the caption itself, exactly as described above.')


def wants_overlay(platform):
    return normalise_source(platform) in OVERLAY_PLATFORMS

# ── Media ─────────────────────────────────────────────────────────────────────
# What each channel will take, and how it takes it. `fetch` means the platform
# collects the file from a URL we serve, so the media has to be publicly
# reachable; `upload` means we hand it the bytes ourselves.
MEDIA_SUPPORT = {
    # X takes four stills or one clip, never a mix. Threads and Fanvue build a
    # carousel out of whatever they are given.
    'x':         {'kinds': ('image', 'video'), 'how': 'upload', 'max': 4},
    'threads':   {'kinds': ('image', 'video'), 'how': 'fetch',  'max': 20},
    'fanvue':    {'kinds': ('image', 'video'), 'how': 'upload', 'max': 20},
    # No posting API, so media here is something the creator downloads and
    # uploads by hand. Reddit takes a still; a video post there is a different
    # submission type we do not write.
    'instagram': {'kinds': ('image', 'video'), 'how': 'by-hand', 'max': 10},
    'tiktok':    {'kinds': ('video',),         'how': 'by-hand', 'max': 1},
    'reddit':    {'kinds': ('image',),         'how': 'by-hand', 'max': 20},
}

MEDIA_KINDS = ('image', 'video')

# Inline bytes live in a database text column, so the cap is about what a row
# and a request can carry rather than what the channel allows. Past it the
# creator hosts the file and gives us the URL.
MEDIA_INLINE_MAX_BYTES = 12 * 1024 * 1024


def media_kind(mime):
    """'image' or 'video' from a mime type, defaulting to image — the library
    held nothing else before video existed."""
    return 'video' if str(mime or '').lower().startswith('video/') else 'image'


def media_ok(platform, kind):
    """Whether this channel will carry this kind of media at all."""
    spec = MEDIA_SUPPORT.get(normalise_source(platform))
    return bool(spec) and str(kind or 'image') in spec['kinds']


def media_how(platform):
    """'upload', 'fetch' or 'by-hand' — how the media reaches the channel."""
    spec = MEDIA_SUPPORT.get(normalise_source(platform))
    return spec['how'] if spec else 'by-hand'


def media_max(platform):
    """How many files this channel will carry on one post."""
    spec = MEDIA_SUPPORT.get(normalise_source(platform))
    return int(spec['max']) if spec else 1


# A clip is never one of several: none of these channels builds a carousel with a
# video in it, and X refuses the mix outright.
def media_set_reject(platform, kinds):
    """Why this set of files cannot go on one post here, or '' when it can.
    Each file is still checked on its own by media_reject; this is about the set."""
    plat = normalise_source(platform)
    kinds = list(kinds or [])
    if len(kinds) <= 1:
        return ''
    label = POST_PLATFORMS.get(plat, {}).get('label', plat or 'that channel')
    cap = media_max(plat)
    if cap <= 1:
        return f'{label} takes one file per post, not {len(kinds)}.'
    if len(kinds) > cap:
        return f'{label} takes at most {cap} files per post, not {len(kinds)}.'
    if any(k == 'video' for k in kinds):
        return f'{label} will not carry a video alongside other files. Post the clip on its own.'
    return ''


def media_reject(platform, kind):
    """Why this pairing cannot be queued, or '' when it can. The wording is the
    error the operator sees, so it says what to do rather than what failed."""
    plat = normalise_source(platform)
    spec = MEDIA_SUPPORT.get(plat)
    label = POST_PLATFORMS.get(plat, {}).get('label', plat or 'that channel')
    if not spec:
        return f'{label} takes no media from here.'
    kind = str(kind or 'image')
    if kind not in MEDIA_KINDS:
        return f'{kind} is not a kind of media this understands.'
    if kind not in spec['kinds']:
        takes = ' or '.join(spec['kinds'])
        return f'{label} takes {takes}, not {kind}.'
    return ''

# 'manual' is a post on a channel with no posting API: it sits on the calendar
# as a reminder and the scheduler never sees it, because due_posts only ever
# claims a 'queued' row. Without it a by-hand post would either be invisible or
# would fail at its slot for a reason the creator can do nothing about.
QUEUE_STATES = ('queued', 'manual', 'sending', 'posted', 'failed', 'cancelled')

# What is still the creator's to edit, move or call off. A 'sending' row belongs
# to the worker and the send may already be away.
EDITABLE_STATES = ('queued', 'manual')


def queue_status_for(platform):
    """The status a freshly queued post starts in: waiting for the scheduler on
    a channel we can publish to, waiting for the creator everywhere else."""
    return 'queued' if normalise_source(platform) in PUBLISHABLE else 'manual'


def post_cap(platform):
    return (POST_PLATFORMS.get(normalise_source(platform)) or {}).get('cap', 280)


def trim_to(text, cap):
    """Cut text to a character budget, on a word boundary where one is close
    enough to the limit to be worth keeping."""
    text = str(text or '').strip()
    if len(text) <= cap:
        return text
    cut = text[:cap]
    space = cut.rfind(' ')
    return (cut[:space] if space >= cap - 30 else cut).rstrip()


def trim_post(platform, text):
    return trim_to(text, post_cap(platform))


def split_overlay(text):
    """(overlay, caption) from a two-section reply. A reply that ignored the
    format counts as all caption: the overlay is the half that can go missing
    without costing the post, so a stray format is never a lost draft."""
    overlay, caption, mode = [], [], ''
    for line in str(text or '').strip().splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith('overlay:'):
            mode, stripped = 'overlay', stripped[8:].strip()
        elif low.startswith('caption:'):
            mode, stripped = 'caption', stripped[8:].strip()
        elif mode:
            stripped = line
        else:
            continue
        (overlay if mode == 'overlay' else caption).append(stripped)
    head = ' '.join(x for x in overlay if x.strip()).strip().strip('"')
    body = '\n'.join(caption).strip()
    if not head or not body:
        return '', str(text or '').strip()
    return trim_to(head, OVERLAY_CAP), body


RATING_LEVELS = ('suggestive', 'moderate', 'explicit')

# TikTok's ToS bars directing users to adult content at all — this is a policy
# floor, not a formatting default, so an override cannot lift it the way a
# route or a bio can be overridden.
SFW_LOCKED = {'tiktok'}


def clean_ratings(data, existing=None):
    """Validate a saved content-level table. TikTok is coerced back off no
    matter what came in, the same way a typo'd route falls back to the default
    rather than resolving to nothing."""
    out = dict(existing or {})
    for source, val in (data or {}).items():
        src = normalise_source(source)
        if not src:
            continue
        if not val:
            out.pop(src, None)
            continue
        enabled = bool((val or {}).get('nsfw_enabled')) and src not in SFW_LOCKED
        level = str((val or {}).get('nsfw_level') or '').strip().lower()
        if level not in RATING_LEVELS:
            level = 'suggestive'
        out[src] = {'nsfw_enabled': enabled, 'nsfw_level': level}
    return out


def content_level(overrides, platform, global_enabled, global_level):
    """(enabled, level) for one platform's captions: an explicit override
    wins, then the SFW floor, then the persona's global chat setting — so a
    caption never assumes a looser level than the creator actually set. A
    variant reads its base channel's level: a carousel is still Instagram."""
    src = base_platform(platform)
    row = (overrides or {}).get(src)
    if row:
        return bool(row.get('nsfw_enabled')), row.get('nsfw_level', 'suggestive')
    if src in SFW_LOCKED:
        return False, 'suggestive'
    return bool(global_enabled), (global_level if global_level in RATING_LEVELS
                                  else 'suggestive')


# Kept in the same words the chat prompt uses, so a caption never reads
# looser or tighter than a message the persona would actually send.
_LEVEL_CLAUSE = {
    'suggestive': 'Be suggestive and flirtatious. Hint at things without going explicit.',
    'moderate': 'Be openly sexual in language. Moderately explicit — describe feelings, sensations, intentions vividly.',
    'explicit': 'Go fully explicit when the moment calls for it. Direct, specific, graphic language.',
}


def content_level_clause(enabled, level):
    return _LEVEL_CLAUSE.get(level, _LEVEL_CLAUSE['suggestive']) if enabled else ''


BIO_PLATFORMS = {
    'instagram': {'label': 'Instagram', 'cap': 150, 'lines': 3},
    # 80 characters is not three lines of anything. TikTok gets who she is and
    # where to go, and the middle line — the promise — is the one that goes.
    'tiktok':    {'label': 'TikTok',    'cap': 80,  'lines': 2},
    'x':         {'label': 'X',         'cap': 160, 'lines': 3},
    'threads':   {'label': 'Threads',   'cap': 150, 'lines': 3},
    'reddit':    {'label': 'Reddit',    'cap': 200, 'lines': 3},
}

# The three-line bio: who she is, what a follower actually gets, where to go
# next. Written as one instruction rather than three calls because the lines
# have to sound like one person wrote them in one go.
BIO_BRIEF = ('one per line, no bullets and no numbering: who you are and the '
             'one thing you are about, then what someone following you '
             'actually gets, then a short instruction to follow the link. No '
             'hashtags, no @mentions, no URL — the link is added underneath.')

BIO_BRIEF_SHORT = ('one per line, no bullets and no numbering: who you are and '
                   'the one thing you are about, then a short instruction to '
                   'follow the link. No hashtags, no @mentions, no URL — the '
                   'link is added underneath.')


def bio_brief(platform):
    """The tight caps cannot hold the promise line, so they are never asked for
    it — a bio generated to be cut is a bio that reads as if it was."""
    return BIO_BRIEF if bio_line_count(platform) >= 3 else BIO_BRIEF_SHORT


def bio_line_count(platform):
    return (BIO_PLATFORMS.get(normalise_source(platform)) or {}).get('lines', 3)

# How long a pinned post has to stand before it stops being the first thing
# worth showing a new visitor. The guide's cadence is weekly.
PIN_REFRESH_DAYS = 7


def bio_cap(platform):
    return (BIO_PLATFORMS.get(normalise_source(platform)) or {}).get('cap', 150)


def bio_lines(text, platform=''):
    """The bio's lines, fitted to the platform's cap. The last line is the one
    that sends people to the link, so when the budget runs short the middle
    lines go first — a bio that ends on half a word asks for nothing at all."""
    raw = [ln.strip(' -*\u2022\t') for ln in str(text or '').splitlines()]
    lines = [ln for ln in raw if ln]
    if not lines:
        return []
    want = bio_line_count(platform) if platform else 3
    if len(lines) > want:
        # Keep the opening lines and the last one. The last is the CTA whatever
        # the model called it, and merging it into the line above buries it.
        lines = lines[:want - 1] + lines[-1:]
    cap = bio_cap(platform) if platform else 0
    if not cap:
        return lines

    def fits(ls):
        return len('\n'.join(ls)) <= cap

    while len(lines) > 1 and not fits(lines):
        # Drop from the middle outwards, keeping the first line and the CTA.
        lines.pop(len(lines) // 2 if len(lines) > 2 else 0)
    if not fits(lines):
        cut = lines[0][:cap]
        space = cut.rfind(' ')
        lines = [(cut[:space] if space >= cap - 20 else cut).rstrip()]
    return [ln for ln in lines if ln]


def pin_status(pinned_at=0, now=0):
    """How the pinned post is doing: its age in days, and whether it is past
    the refresh cadence. Never pinned counts as stale — that is the state the
    panel most needs to shout about."""
    now = int(now or 0)
    pinned_at = int(pinned_at or 0)
    if not pinned_at or pinned_at > now:
        return {'pinned_at': pinned_at, 'days': None, 'stale': True, 'ever': False}
    days = int((now - pinned_at) // 86400)
    return {'pinned_at': pinned_at, 'days': days,
            'stale': days >= PIN_REFRESH_DAYS, 'ever': True}


def queue_stats(rows, now=0):
    """Totals and the next slot for the queue panel. `rows` are dicts of
    {platform, status, run_at} — run_at as an epoch, so the caller does the
    timezone work once rather than here."""
    now = int(now or 0)
    totals = {k: 0 for k in QUEUE_STATES}
    totals['next_at'] = 0
    totals['overdue'] = 0
    per = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get('status') or '')
        if status not in totals:
            continue
        plat = normalise_source(row.get('platform')) or 'other'
        run_at = int(row.get('run_at') or 0)
        totals[status] += 1
        bucket = per.setdefault(plat, {'platform': plat,
                                       **{k: 0 for k in QUEUE_STATES}})
        bucket[status] += 1
        if status == 'queued':
            if run_at and (not totals['next_at'] or run_at < totals['next_at']):
                totals['next_at'] = run_at
            # Due and still sitting there: the worker is off, or the persona is
            # not on the beta. Either way it is the one thing worth saying.
            if run_at and now and run_at <= now:
                totals['overdue'] += 1
    rows_out = sorted(per.values(), key=lambda r: (-r['queued'], -r['posted'], r['platform']))
    return {'totals': totals, 'platforms': rows_out}


# ── Win-back ladder ───────────────────────────────────────────────────────────
# funnels.winback_due has been in the tree unused since the funnel engine
# landed. This is the thin wrapper the follow-up rounds call.
WEEKLY_CADENCE = {
    'x':         {'per_day': 2,  'hours': (9, 20)},
    'threads':   {'per_day': 1,  'hours': (12,)},
    'fanvue':    {'per_day': 1,  'hours': (11,)},
    'instagram': {'per_day': 1,  'hours': (18,)},
    'tiktok':    {'per_day': 1,  'hours': (19,)},
    'reddit':    {'per_week': 3, 'hours': (21,)},
}

# 70/20/10. Most of what she posts has to be worth following on its own, or a
# tease has nothing to interrupt and an ask has nothing behind it.
MIX_TEASE, MIX_CTA = 0.2, 0.1


def mix_for(count):
    """The job each of `count` posts is doing, holding the ratio at any length.
    Spread rather than dealt in order: a week that saves every ask for Sunday
    is a week of asks nobody sees, and a run of teases with nothing between
    them is just selling."""
    if count <= 0:
        return []
    n_cta = max(1, int(round(count * MIX_CTA))) if count >= 5 else 0
    n_tease = max(1, int(round(count * MIX_TEASE))) if count >= 3 else 0
    n_tease = max(0, min(n_tease, count - n_cta))
    kinds = ['value'] * count

    def place(n, label, offset):
        step = count / float(n) if n else 0
        for k in range(n):
            p = int(k * step + step * offset) % count
            for _ in range(count):
                if kinds[p] == 'value':
                    kinds[p] = label
                    break
                p = (p + 1) % count

    place(n_cta, 'cta', 0.5)
    place(n_tease, 'tease', 0.15)
    return kinds

MIX_BRIEF = {
    'value': ('something worth reading on its own — a scene from your day, an '
              'opinion, a small story. Sell nothing.'),
    'tease': ('hint that there is more of this somewhere else without naming '
              'the place or pasting a link.'),
    'cta': ('invite them to come find you, warmly and once. Still no link — '
            'the link lives in your bio.'),
}

# One request cannot sit there making sixty model calls, and a week generated
# in one go is a week of drafts nobody reads before they go out.
PLAN_QUEUE_CAP = 20

# Two posts telling one story a few days apart: part one withholds the payoff
# and says so, part two delivers it. Only where the guide uses the device — the
# curiosity gap belongs to short video; on X it is a cliffhanger nobody asked for.
SERIES_PLATFORMS = ('tiktok', 'instagram')
SERIES_PARTS = 2

SERIES_BRIEF = {
    1: ('This is part one of two. Set it up and stop before the payoff, and say '
        'in your own words that part two is coming.'),
    2: ('This is part two of two and they have already seen part one. Pay off '
        'what it set up — no recap and no second hook.'),
}


def series_plan(slots):
    """Pair a channel's slots into two-part series, in the order they run. A
    trailing odd slot stays on its own rather than becoming a part one whose
    part two never comes. Both halves share one idea, so the caller assigns an
    idea per group: two halves of one story cannot be about two things."""
    group = 0
    for plat in SERIES_PLATFORMS:
        mine = [s for s in slots if s.get('platform') == plat]
        for i in range(0, len(mine) - 1, SERIES_PARTS):
            group += 1
            for part, slot in enumerate(mine[i:i + SERIES_PARTS], start=1):
                slot['series'] = {'group': group, 'part': part, 'of': SERIES_PARTS}
    return slots


def series_part(series):
    """The validated part number in a series payload, or 0. Comes back off the
    wire from the planner, so a bad shape means no series rather than a 500."""
    try:
        part = int((series or {}).get('part') or 0)
    except (TypeError, ValueError, AttributeError):
        return 0
    return part if part in SERIES_BRIEF else 0


def plan_week(start_at, days=7, cadence=None):
    """The week's slots: what goes where, when, and which of the three jobs each
    post is doing. Reddit never draws the ask — its own brief rules out sales
    language, and a subreddit is the fastest place to lose an account over it."""
    cadence = cadence or WEEKLY_CADENCE
    # The default lives in the signature; asking for none here means one day,
    # not a silent week.
    days = max(1, min(int(days or 0), 28))
    start_at = int(start_at or 0)
    slots = []
    for plat, spec in cadence.items():
        hours = spec.get('hours') or (12,)
        per_day = int(spec.get('per_day', 0) or 0)
        per_week = int(spec.get('per_week', 0) or 0)
        if per_day:
            picks = [(d, hours[i % len(hours)])
                     for d in range(days) for i in range(per_day)]
        elif per_week:
            total = max(1, int(round(per_week * days / 7.0)))
            step = days / float(total)
            picks = [(min(days - 1, int(i * step)), hours[i % len(hours)])
                     for i in range(total)]
        else:
            picks = []
        kinds = mix_for(len(picks))
        for n, (d, h) in enumerate(picks):
            kind = 'value' if plat == 'reddit' else kinds[n]
            slots.append({'platform': plat, 'label': POST_PLATFORMS.get(plat, {}).get('label', plat),
                          'day': d, 'hour': h, 'kind': kind,
                          'at': start_at + d * 86400 + h * 3600,
                          'publishable': plat in PUBLISHABLE})
    slots.sort(key=lambda s: (s['at'], s['platform']))
    return slots


def plan_summary(slots, now=0):
    """Counts for the panel, plus how much of the week the queue can take on its
    own — the rest is copy she has to paste somewhere herself."""
    now = int(now or 0)
    by_plat, by_kind = {}, {}
    posts = auto = upcoming = 0
    for s in slots:
        posts += 1
        p = by_plat.setdefault(s['platform'], {'platform': s['platform'],
                                               'label': s.get('label', s['platform']),
                                               'posts': 0, 'publishable': s['publishable']})
        p['posts'] += 1
        by_kind[s['kind']] = by_kind.get(s['kind'], 0) + 1
        if s['publishable']:
            auto += 1
            if not now or s['at'] > now:
                upcoming += 1
    rows = sorted(by_plat.values(), key=lambda r: (-r['posts'], r['platform']))
    return {'posts': posts, 'auto': auto, 'manual': posts - auto,
            'queueable': min(upcoming, PLAN_QUEUE_CAP), 'upcoming': upcoming,
            'platforms': rows,
            'kinds': [{'kind': k, 'posts': v} for k, v in sorted(by_kind.items())]}


def plan_ideas(text, wanted):
    """Pull the week's angles out of one model reply. Numbering, bullets and a
    stray preamble all turn up; what matters is getting `wanted` distinct lines
    and never handing a slot an empty idea."""
    out, seen = [], set()
    for raw in str(text or '').splitlines():
        line = raw.strip().strip('-*\u2022').strip()
        line = re.sub(r'^\s*\d+[\.\)]\s*', '', line).strip()
        # Short is fine — "my cat" is an angle. What gets dropped is a
        # fragment left over from stripping, and the heading above the list.
        if len(line) < 5 or line.endswith(':'):
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line[:200])
        if len(out) >= wanted:
            break
    return out


def winback_step(days_since_lapse, touches):
    """{'touch', 'offer'} when a win-back touch is due, else None."""
    if days_since_lapse < 1 or not winback_due(days_since_lapse, touches):
        return None
    return {'touch': int(touches) + 1, 'offer': winback_is_offer(touches)}


def winback_next_day(touches):
    """Which day of silence the next rung fires on, or None once the ladder is
    spent. The panel counts down to it; winback_step decides."""
    touches = int(touches)
    if touches >= WINBACK_MAX_TOUCHES:
        return None
    if touches < len(WINBACK_DAYS):
        return WINBACK_DAYS[touches]
    return 30 + 90 * (touches - len(WINBACK_DAYS) + 1)


def winback_rungs():
    """Every rung of the ladder, for the panel to lay out."""
    return [{'touch': t + 1, 'day': winback_next_day(t), 'offer': winback_is_offer(t)}
            for t in range(WINBACK_MAX_TOUCHES)]


def winback_instruction(offer, label=''):
    if not offer:
        return (
            'This fan has gone quiet for a while. Write ONE short, in-character '
            'message that reopens the conversation without mentioning the gap, '
            'without apologising for it and without any offer or link. Reference '
            'something they told you before if you can. One or two sentences.')
    invite = f' Phrase the invite as "{label}".' if label else ''
    return (
        'This fan has been gone a while and a plain hello has not brought them '
        'back. Write ONE short, in-character message that misses them and points '
        'them to where the rest of it lives — warm, no pressure, no guilt.' +
        invite + ' Do not paste a URL yourself; a link is appended after your '
        'message. One or two sentences.')
