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


def cta_choice(cta, fan, beta=True, today=''):
    """Which link goes out with this reply.

    Returns {'url', 'label', 'kind', 'promo'}. Off the beta gate — or with no
    trial link configured — this is exactly the old single-link behaviour.
    """
    cta = cta or {}
    paid = (cta.get('cta_url') or '').strip()
    label = (cta.get('cta_label') or '').strip()
    plain = {'url': paid, 'label': label, 'kind': 'paid', 'promo': ''}
    if not beta:
        return plain
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
    'tiktok': {
        'label': 'TikTok',
        'cap': 2200,
        'brief': ('a curiosity caption of 300-400 characters that withholds the '
                  'payoff, reads as a diary entry, and never names a paid site'),
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
PUBLISHABLE = ('x', 'threads')

QUEUE_STATES = ('queued', 'sending', 'posted', 'failed', 'cancelled')


def post_cap(platform):
    return (POST_PLATFORMS.get(normalise_source(platform)) or {}).get('cap', 280)


def trim_post(platform, text):
    """Cut a draft to what the channel will take, on a word boundary where one
    is close enough to the limit to be worth keeping."""
    text = str(text or '').strip()
    cap = post_cap(platform)
    if len(text) <= cap:
        return text
    cut = text[:cap]
    space = cut.rfind(' ')
    return (cut[:space] if space >= cap - 30 else cut).rstrip()


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
