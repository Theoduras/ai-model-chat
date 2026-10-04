"""Fan wishes: a fan describes a picture of her, she makes it and sends it back
as a paid unlock. This file is the pure part — spotting a wish, reading the
classifier's answer, refusing what must never be drawn, pricing it."""
import json
import re

from characters import YOUTH_TERMS

# Cheap gate in front of the classifier: most chat never mentions a picture,
# and a message that does not should cost no model call at all.
_CUE_RE = re.compile(
    r'\b(wish|pic|pics|picture|photo|photos|selfie|snap|show me|see you|'
    r'send me|wear|wearing|dressed|outfit|pose|posing|naked|nude|topless|'
    r'lingerie|bikini)\b', re.I)

# Beyond the youth list: people other than her, and anything without consent.
_BLOCK_TERMS = YOUTH_TERMS + (
    'rape', 'forced', 'non-consensual', 'nonconsensual', 'unconscious', 'asleep',
    'drugged', 'drunk girl', 'incest', 'sister', 'daughter', 'mom', 'mother',
    'animal', 'dog', 'horse', 'blood', 'gore', 'corpse', 'dead', 'my ex',
    'my wife', 'my girlfriend', 'celebrity', 'face of', 'looks like')

CLASSIFY_INSTRUCTION = (
    'A fan just messaged you: "{text}"\n\n'
    'Decide if they are asking to see a picture of YOU (an outfit, a pose, a '
    'place, anything they are picturing). It is a wish only if THEY describe '
    'what they want to see. Asking about wishes, how wishes work, whether you '
    'do them or what they cost is NOT a wish; neither is a bare "send a pic" '
    'with nothing described — never invent a scene they did not ask for. '
    'Answer with JSON only, no prose:\n'
    '{{"wish": true|false, "scene": "<what the photo shows: her outfit or lack '
    'of one, pose, setting — third person, under 40 words, no names>", '
    '"outfit": "<just what she wears, or \\"nothing\\" if nude>", '
    '"explicit": true|false, "caption": "<one short in-character line to send '
    'with the locked photo; tease it, never mention price or unlocking>"}}\n'
    'explicit is true only if the picture involves nudity or sex.')

PLAN_INSTRUCTION = (
    'You are about to post this: "{text}"\n\n'
    'Describe one photo of YOU to go with it. Answer with JSON only, no prose:\n'
    '{{"scene": "<what the photo shows: her outfit or lack of one, pose, setting '
    '— third person, under 40 words, no names>", '
    '"outfit": "<just what she wears, or \\"nothing\\" if nude>", '
    '"explicit": true|false}}\n'
    'explicit is true only if the picture involves nudity or sex.')


def looks_like_wish(text):
    return bool(_CUE_RE.search(text or ''))


def parse(raw, need_wish=True):
    """The classifier's JSON, or None when it is not a usable wish."""
    m = re.search(r'\{.*\}', raw or '', re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict) or (need_wish and d.get('wish') is not True):
        return None
    scene = re.sub(r'\s+', ' ', str(d.get('scene') or '')).strip()[:300]
    if not scene:
        return None
    return {'scene': scene, 'explicit': d.get('explicit') is True,
            'outfit': re.sub(r'\s+', ' ', str(d.get('outfit') or '')).strip()[:200],
            'caption': re.sub(r'\s+', ' ', str(d.get('caption') or '')).strip()[:300]}


def blocked(*texts):
    """The first refused term found in the fan's message or the scene, else ''."""
    low = ' '.join(t or '' for t in texts).lower()
    for term in _BLOCK_TERMS:
        if re.search(r'\b' + re.escape(term) + r'\b', low):
            return term
    return ''


def price(cfg, explicit, floor):
    key = 'price_nsfw' if explicit else 'price_sfw'
    return max(int(floor), int((cfg or {}).get(key) or 0))
