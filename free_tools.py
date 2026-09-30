"""Free public tools: the lead magnets at /<slug>.

TOOLS is the one list the routes, the sitemap, the "more tools" row and the
tracked sign-up links are built from. The two generators call Gemini from a
public endpoint, so their input is whitelisted here and their volume is capped
by an in-memory limiter — a cost guard per instance, not a security boundary.
"""
import json
import re
import threading
import time
from collections import deque

TOOLS = [
    {'slug': 'ai-chatter-earnings-calculator', 'track': 'tool-earnings',
     'name': 'AI chatter earnings calculator',
     'blurb': 'What replying to every fan could add to your month.',
     'title': 'AI Chatter Earnings Calculator for Creators',
     'description': 'Estimate how much more you could earn on OnlyFans or Fanvue '
                    'if every fan who messages you got a reply. Free, no sign-up.'},
    {'slug': 'chatter-cost-calculator', 'track': 'tool-chatter-cost',
     'name': 'Chatter cost calculator',
     'blurb': 'A chatting agency or hired chatter vs an AI chatter.',
     'title': 'Chatter Cost Calculator: Agency vs AI Chatter',
     'description': 'Compare what a chatting agency commission or a hired chatter '
                    'costs you per month and per year against an AI chatter.'},
    {'slug': 'ppv-caption-generator', 'track': 'tool-ppv-caption',
     'name': 'PPV caption generator',
     'blurb': 'Teasing captions and a starting price for your next PPV.',
     'title': 'Free PPV Caption Generator for OnlyFans and Fanvue',
     'description': 'Describe your content and get three teasing PPV captions '
                    'plus a suggested starting price. Free, no sign-up.'},
    {'slug': 'dm-opener-generator', 'track': 'tool-dm-opener',
     'name': 'DM opener generator',
     'blurb': 'First messages that get new subscribers talking.',
     'title': 'Free DM Opener Generator for New Subscribers',
     'description': 'Get five first messages for new subscribers, written in '
                    'your vibe, that start a real conversation.'},
    {'slug': 'free-link-in-bio', 'track': 'tool-link-in-bio',
     'name': 'Free link in bio',
     'blurb': 'Your own link-in-bio page with socials and buttons.',
     'title': 'Free Link in Bio Page for Creators (No Linktree Branding)',
     'description': 'Build a link-in-bio page with your photo, socials and link '
                    'buttons in a minute. Free, with a short link for your bio.'},
    {'slug': 'webhook-signature-simulator', 'track': '',
     'name': 'Webhook signature simulator',
     'blurb': 'Generate and verify HMAC-SHA256 webhook signatures.',
     'title': 'Webhook Signature Simulator',
     'description': 'Generate and verify HMAC-SHA256 webhook signatures in your '
                    'browser. See whether a receiver would accept or reject a '
                    'payload, and why.'},
]
BY_SLUG = {t['slug']: t for t in TOOLS}
TRACK_NOTES = {t['track']: 'Free tool: ' + t['name'] for t in TOOLS if t['track']}

PLATFORMS = ['OnlyFans', 'Fanvue', 'Fansly', 'Telegram']
VIBES = ['Deadpan / Dry', 'Bubbly / Sweet', 'Dominant / Edgy', 'Girl-Next-Door',
         'Mysterious / Dark', 'Playful / Teasing', 'Intellectual / Witty']
CONTENT_TYPES = ['Photo set', 'Short video (under 5 min)', 'Long video (5 min+)',
                 'Bundle', 'Custom request']
TEXT_MAX = 300

# Checked on the creator's free text before anything reaches the model: these
# generators write flirty copy, so a youth-leaning brief is refused outright.
_YOUTH = re.compile(r'\b(teen\w*|underage|under[- ]?18|schoolgirl\w*|school ?uniform|'
                    r'loli\w*|young girl|little girl|child\w*|kid|kids|barely legal|'
                    r'high ?school|jailbait|1[0-7] ?(yo|y/o|years? old))\b', re.I)

_SYSTEM = (
    'You write short copy for adult content creators (every person involved is '
    'an adult, 18+). Keep it suggestive and teasing, never explicit or graphic. '
    'Write like a real person texting: natural, specific, no hashtags, and at '
    'most one emoji per line unless the vibe is Bubbly / Sweet or Playful / '
    'Teasing. Never mention being an AI. Answer with JSON only.')


class InputError(ValueError):
    pass


def _pick(value, allowed, field):
    value = (value or '').strip()
    if value not in allowed:
        raise InputError(f'Choose a valid {field}.')
    return value


def _text(value, field, required=False):
    value = re.sub(r'\s+', ' ', (value or '')).strip()[:TEXT_MAX]
    if required and not value:
        raise InputError(f'Add a short {field}.')
    if _YOUTH.search(value):
        raise InputError('This tool only writes for adults. Please rephrase.')
    return value


def build_prompt(data):
    """(system, user prompt, result key) for a request, or InputError."""
    tool = (data or {}).get('tool')
    if tool == 'ppv-caption':
        platform = _pick(data.get('platform'), PLATFORMS, 'platform')
        kind = _pick(data.get('content_type'), CONTENT_TYPES, 'content type')
        vibe = _pick(data.get('vibe'), VIBES, 'vibe')
        about = _text(data.get('about'), 'description of the content', required=True)
        user = (f'Platform: {platform}. Content type: {kind}. Creator vibe: {vibe}.\n'
                f'What the content is: {about}\n\n'
                'Write 3 different PPV captions (each under 220 characters) that make '
                'a subscriber curious enough to unlock it without describing anything '
                'explicitly. Also suggest a starting price in USD for this content '
                'type as a low and high whole number, with one short sentence why. '
                'JSON shape: {"captions": [3 strings], "price": {"low": number, '
                '"high": number, "why": string}}')
        return _SYSTEM, user, 'captions'
    if tool == 'dm-opener':
        platform = _pick(data.get('platform'), PLATFORMS, 'platform')
        vibe = _pick(data.get('vibe'), VIBES, 'vibe')
        about = _text(data.get('about'), 'note about what you post')
        user = (f'Platform: {platform}. Creator vibe: {vibe}.\n'
                + (f'What the creator posts: {about}\n' if about else '') +
                '\nWrite 5 different first messages to send a brand-new subscriber '
                '(each under 200 characters). Warm, personal, and each ends with an '
                'easy question so the fan replies. No selling in the first message. '
                'JSON shape: {"openers": [5 strings]}')
        return _SYSTEM, user, 'openers'
    raise InputError('Unknown tool.')


def parse_result(text, key):
    """The model's JSON, or its lines as a fallback when it ignores the format."""
    raw = (text or '').strip()
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw)
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get(key), list):
        out = {key: [str(x).strip() for x in data[key] if str(x).strip()][:5]}
        price = data.get('price')
        if key == 'captions' and isinstance(price, dict):
            try:
                low, high = int(float(price.get('low'))), int(float(price.get('high')))
                if 0 < low <= high:
                    out['price'] = {'low': low, 'high': high,
                                    'why': str(price.get('why') or '')[:240]}
            except (TypeError, ValueError):
                pass
        return out
    lines = [re.sub(r'^\s*(?:[-*•]|\d+[.)])\s*', '', ln).strip().strip('"')
             for ln in raw.splitlines()]
    return {key: [ln for ln in lines if len(ln) > 3][:5]}


class Limiter:
    """Sliding windows: per_key calls per key per window, total per hour."""

    def __init__(self, per_key=10, window=600, total=300, total_window=3600):
        self.per_key, self.window = per_key, window
        self.total, self.total_window = total, total_window
        self._keys = {}
        self._all = deque()
        self._lock = threading.Lock()

    def allow(self, key, now=None):
        now = time.time() if now is None else now
        with self._lock:
            while self._all and now - self._all[0] >= self.total_window:
                self._all.popleft()
            hits = self._keys.setdefault(key, deque())
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.per_key or len(self._all) >= self.total:
                return False
            hits.append(now)
            self._all.append(now)
            if len(self._keys) > 5000:
                self._keys = {k: v for k, v in self._keys.items() if v}
            return True


limiter = Limiter()
