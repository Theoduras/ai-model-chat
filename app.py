from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for, render_template_string, Response, after_this_request
import os
import sys
import copy
import json
import re
import logging
import hashlib
import hmac
import secrets
import time
import random
import threading
import urllib.request
import urllib.parse
import urllib.error as url_error
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from utils import (platform_scoped, operator_only, _is_operator,
                   owned_slugs, request_persona)
from google import genai
from google.genai import types

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PERSONAS_DIR = os.path.join(BASE_DIR, 'personas')
os.makedirs(PERSONAS_DIR, exist_ok=True)

# --- Logging setup (serverless-safe) ---
IS_VERCEL = bool(os.getenv('VERCEL') or os.getenv('AWS_LAMBDA_FUNCTION_NAME'))
TMP_PERSONAS_DIR = '/tmp/personas'
LOG_DIR = '/tmp/logs' if IS_VERCEL else os.path.join(BASE_DIR, 'logs')
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except Exception:
    LOG_DIR = None

def _worker_enabled(var):
    """Whether a long-lived background poll loop should start.

    These loops only make sense on an always-on host. On Vercel every
    invocation is frozen once the response is sent, so a worker thread stalls
    mid-round, restarts on each cold start, and duplicates outbound messages
    across concurrent instances. Setting the variable explicitly still wins.
    """
    val = (os.getenv(var) or '').strip()
    if val:
        return val != '0'
    return not IS_VERCEL


logger = logging.getLogger('app')
logger.setLevel(logging.INFO)
# Needs its own stdout handler: with no handler anywhere, logging falls back to
# lastResort (WARNING) and every info() line is dropped before Cloud Run sees it.
if not logger.handlers:
    _ah = logging.StreamHandler(sys.stdout)
    _ah.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(_ah)
error_logger = logging.getLogger('error_logger')
error_logger.setLevel(logging.ERROR)
if not any(isinstance(h, logging.StreamHandler) for h in error_logger.handlers):
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    error_logger.addHandler(sh)
if LOG_DIR:
    try:
        eh = logging.FileHandler(os.path.join(LOG_DIR, 'errors.log'))
        eh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        error_logger.addHandler(eh)
    except Exception:
        pass


def get_chat_logger(user):
    safe_user = re.sub(r'[^\w]', '_', str(user))[:50] or 'unknown'
    logger = logging.getLogger(f'chat_{safe_user}')
    logger.setLevel(logging.INFO)
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        logger.addHandler(sh)
    if LOG_DIR:
        chat_file = os.path.join(LOG_DIR, f'chat_{safe_user}.log')
        if not any(getattr(h, 'baseFilename', '') == chat_file for h in logger.handlers):
            try:
                fh = logging.FileHandler(chat_file)
                fh.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
                logger.addHandler(fh)
            except Exception:
                pass
    return logger, safe_user


# She mirrors the fan's language and a lowercase persona drops the question
# mark, so an English-only list meant her Dutch questions were never counted \u2014
# the frequency limit then had nothing to limit. Same languages as _cta_asked.
QUESTION_STARTERS = (
    # English
    'what', 'why', 'how', 'when', 'where', 'who', 'which',
    'do you', 'did you', 'are you', 'have you', 'can you',
    'would you', 'could you', 'tell me',
    # Dutch
    'wat', 'waarom', 'hoe', 'wanneer', 'waar', 'wie', 'welke', 'welk',
    'ben je', 'ben jij', 'heb je', 'heb jij', 'kun je', 'kun jij',
    'wil je', 'wil jij', 'vind je', 'denk je', 'hou je', 'zou je',
    'vertel me', 'vertel eens',
    # German
    'was', 'warum', 'wieso', 'wie', 'wann', 'wo', 'wer',
    'welche', 'welcher', 'welches', 'bist du', 'hast du', 'kannst du',
    'willst du', 'magst du', 'w\u00fcrdest du', 'erz\u00e4hl mir',
    # French
    'pourquoi', 'comment', 'quand', 'o\u00f9', 'qui', 'quel', 'quelle',
    'est-ce que', 'es-tu', 'as-tu', 'peux-tu', 'veux-tu', 'dis-moi',
    # Spanish / Portuguese
    'qu\u00e9', 'por qu\u00e9', 'c\u00f3mo', 'cu\u00e1ndo', 'd\u00f3nde',
    'qui\u00e9n', 'cu\u00e1l', 'eres', 'tienes', 'puedes', 'quieres', 'dime',
    'o que', 'por que', 'quando', 'onde', 'quem', 'qual', 'me diz',
    # Italian
    'cosa', 'perch\u00e9', 'quale', 'sei tu', 'hai', 'puoi', 'vuoi', 'dimmi',
)

# A question is often tacked on after a comma or an ellipsis ("en jij dan,
# kooke...wat voor situaties"), and _sentences deliberately glues ellipsis
# fragments back together, so the starter has to be looked for per clause.
_CLAUSE_SPLIT_RE = re.compile(r'(?:\.{3}|\u2026|,)\s*')

# A clause frequently opens on a conjunction ("en wat voor soort seks...").
_LEAD_CONJ_RE = re.compile(
    r'^(?:en|and|of|or|maar|but|dus|so|und|oder|aber|et|ou|mais|y|pero|ma)\s+',
    re.I)


def sentence_asks_question(sentence):
    s = (sentence or '').strip().lower()
    if not s:
        return False
    if '?' in s:
        return True
    for clause in _CLAUSE_SPLIT_RE.split(s):
        clause = _LEAD_CONJ_RE.sub('', clause.strip())
        if clause.startswith(QUESTION_STARTERS):
            return True
    return False


def response_asks_question(text):
    """True when the reply actually puts a question to the fan. Matches on the
    sentence level so a word like "somewhat" no longer counts as a question."""
    return any(sentence_asks_question(s) for s in _sentences(text))


QUESTION_FREQ_RULES = {
    'rarely': ('Ask the fan a question only rarely — around one reply in five. '
               'Most of your replies are statements: react, share something about '
               'yourself, let them carry it.'),
    'sometimes': ('Ask the fan a question sometimes — around one reply in three. '
                  'The rest of the time just react and share something of your own '
                  'instead of putting another question to them.'),
    'often': ('Ask the fan a question often, but not every time — around two '
              'replies in three.'),
    'very often': 'Ask the fan a question in nearly every reply.',
}

QUESTION_LIMIT_RULE = (
    'Never put more than ONE question in a reply. If your last message already '
    'asked something the fan has not answered yet, do not ask anything new — '
    'respond to what they did say, or wait.')


def question_freq_rule(config):
    freq = (config or {}).get('question_freq') or 'often'
    desc = QUESTION_FREQ_RULES.get(freq, QUESTION_FREQ_RULES['often'])
    return f'Question frequency: {desc} {QUESTION_LIMIT_RULE}'


def trim_extra_questions(reply, allow_question=True):
    """Drop question sentences past the budget. Keeps the first question when
    one is allowed, so a reply never lands as a burst of questions.

    Works line by line and keeps the original breaks: they are her paragraphing,
    and joining everything with spaces turned a two-line reply into a run-on.
    """
    budget = 1 if allow_question else 0
    pieces = re.split(r'(\n+)', (reply or '').strip())
    out, dropped = [], False
    for i in range(0, len(pieces), 2):
        kept = []
        for s in _sentences(pieces[i]):
            if sentence_asks_question(s):
                if budget <= 0:
                    dropped = True
                    continue
                budget -= 1
            kept.append(s)
        if kept:
            out.append((pieces[i - 1] if i and out else '') + ' '.join(kept))
    if not dropped or not out:
        return reply
    return ''.join(out)


def _read_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read().strip()


def _persona_path(slug, ext):
    """Return path to persona file, preferring /tmp/personas on Vercel."""
    tmp = os.path.join(TMP_PERSONAS_DIR, f'{slug}{ext}')
    if os.path.exists(tmp):
        return tmp
    return os.path.join(PERSONAS_DIR, f'{slug}{ext}')


def load_persona_prompt(slug):
    saved = db_get_persona(slug)
    if saved and saved.get('prompt'):
        return saved['prompt']
    path = _persona_path(slug, '.txt')
    if os.path.exists(path):
        return _read_file(path)
    # fallback to legacy location
    legacy = os.path.join(BASE_DIR, f'grok-{slug}-prompt.txt')
    if os.path.exists(legacy):
        return _read_file(legacy)
    return None


def load_persona_config(slug):
    """A persona's builder config, DB first then the repo file. Empty dict when
    the persona has none, so callers can rely on .get() defaults."""
    saved = db_get_persona(slug)
    if saved and isinstance(saved.get('config'), dict):
        return saved['config']
    path = _persona_path(slug, '.config.json')
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
        except Exception:
            return {}
    return {}


def load_persona_profile(slug):
    path = _persona_path(slug, '.json')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    # fallback to shared profile_data.json for legacy
    legacy = os.path.join(BASE_DIR, 'profile_data.json')
    if os.path.exists(legacy):
        with open(legacy, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def _is_premade(slug):
    """A premade (original) persona ships as a repo file and must stay unedited."""
    return os.path.exists(os.path.join(PERSONAS_DIR, f'{slug}.config.json')) \
        or os.path.exists(os.path.join(PERSONAS_DIR, f'{slug}.txt'))


def db_get_persona(slug):
    """Return a saved persona from the DB as a dict, or None. A DB entry for a
    premade slug is treated as an in-place OVERRIDE that shadows the repo file,
    so dashboard edits to originals persist (durable in Postgres)."""
    try:
        from db import SessionLocal, get_saved_persona
    except Exception:
        return None
    s = SessionLocal()
    try:
        sp = get_saved_persona(s, slug)
        if not sp:
            return None
        try:
            config = json.loads(sp.config_json)
        except Exception:
            config = {}
        return {'slug': sp.slug, 'name': sp.name, 'config': config, 'prompt': sp.prompt}
    finally:
        s.close()


def db_list_personas(owner_id=None):
    """Saved personas as dicts. With owner_id, only that customer's."""
    try:
        from db import SessionLocal, list_saved_personas, list_saved_personas_for_owner
    except Exception:
        return []
    try:
        s = SessionLocal()
    except Exception:
        return []
    try:
        out = []
        rows = (list_saved_personas_for_owner(s, owner_id) if owner_id
                else list_saved_personas(s))
        for sp in rows:
            try:
                config = json.loads(sp.config_json)
            except Exception:
                config = {}
            out.append({
                'slug': sp.slug, 'name': sp.name, 'config': config,
                # Surfaced so the dashboard can sort by recency.
                'created_at': sp.created_at.isoformat() if sp.created_at else None,
                'updated_at': sp.updated_at.isoformat() if sp.updated_at else None,
            })
        return out
    except Exception:
        # DB unreachable (e.g. misconfigured Cloud SQL) — never let saved-copy
        # loading hide the premade/file personas.
        return []
    finally:
        try:
            s.close()
        except Exception:
            pass


def db_save_persona(slug, name, config, prompt, owner_id=None):
    from db import SessionLocal, upsert_saved_persona
    s = SessionLocal()
    try:
        upsert_saved_persona(s, slug, name, json.dumps(config, ensure_ascii=False),
                             prompt, owner_id=owner_id)
        s.commit()
    finally:
        s.close()
    _prompt_cache.pop(slug, None)


def _persona_owner(slug):
    """owner_id of a saved persona, or None if unowned/nonexistent."""
    try:
        from db import SessionLocal, SavedPersona
        s = SessionLocal()
        try:
            sp = s.get(SavedPersona, slug)
            return sp.owner_id if sp else None
        finally:
            s.close()
    except Exception:
        return None


def _can_edit_persona(slug, user):
    """Admins edit anything. Everyone else only their own, plus slugs that do
    not exist yet (creating). Premade repo personas are house-owned."""
    if not user:
        return False
    if user.get('is_admin'):
        return True
    owner = _persona_owner(slug)
    if owner:
        # Strictly the workspace in view: a creator's own personas must not
        # follow them into a workspace they merely have a seat in.
        return owner == (user.get('workspace_id') or user['id'])
    # No DB row: free to create unless a premade repo persona holds the slug.
    return not _is_premade(slug)


_PERSONA_WRITE_RE = re.compile(r'^/api/personas/([a-z0-9_-]+)')


PERSONA_PHOTOS_DIR = os.path.join(PERSONAS_DIR, 'photos')


def _photo_files(slug):
    d = os.path.join(PERSONA_PHOTOS_DIR, slug)
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d))
            if f.lower().endswith(('.jpg', '.jpeg', '.png'))]


def _file_to_data_url(path):
    import base64
    mime = 'image/png' if path.lower().endswith('.png') else 'image/jpeg'
    with open(path, 'rb') as f:
        return f'data:{mime};base64,' + base64.b64encode(f.read()).decode()


def db_get_images(slug):
    """Image data URLs for a persona: DB first (editable, durable with Postgres),
    falling back to baked-in repo files in personas/photos/<slug>/."""
    imgs = []
    try:
        from db import SessionLocal, get_persona_images_row
        s = SessionLocal()
        try:
            row = get_persona_images_row(s, slug)
            if row:
                parsed = json.loads(row.images_json)
                if isinstance(parsed, list):
                    imgs = parsed
        finally:
            s.close()
    except Exception:
        imgs = []
    if imgs:
        return imgs
    return [_file_to_data_url(p) for p in _photo_files(slug)][:5]


def db_set_images(slug, images):
    """Persist a persona's images (list of data URLs, capped at 5) to the DB."""
    from db import SessionLocal, set_persona_images_row
    clean = [i for i in (images or []) if isinstance(i, str) and i.startswith('data:')][:5]
    s = SessionLocal()
    try:
        set_persona_images_row(s, slug, json.dumps(clean))
        s.commit()
    finally:
        s.close()
    return clean


def unique_copy_slug(name):
    """Make a URL-safe slug from a name, unique across premade + saved personas."""
    base = re.sub(r'[^a-z0-9]+', '-', (name or 'persona').lower()).strip('-') or 'persona'
    slug = base
    i = 2
    existing_saved = {p['slug'] for p in db_list_personas()}
    while _is_premade(slug) or slug in existing_saved:
        slug = f'{base}-{i}'
        i += 1
    return slug


# Keyword → (language, country) for inferring a persona's native language from
# its location. Order matters: more specific entries first.
_LANGUAGE_BY_PLACE = [
    (['netherlands', 'dutch', 'amsterdam', 'rotterdam', 'utrecht', 'holland'], ('Dutch', 'the Netherlands')),
    (['czech', 'prague', 'czechia'], ('Czech', 'the Czech Republic')),
    (['portugal', 'lisbon', 'porto'], ('Portuguese', 'Portugal')),
    (['brazil', 'brasil', 'rio', 'sao paulo', 'são paulo'], ('Portuguese', 'Brazil')),
    (['spain', 'madrid', 'barcelona', 'valencia', 'sevilla', 'seville'], ('Spanish', 'Spain')),
    (['mexico', 'méxico'], ('Spanish', 'Mexico')),
    (['france', 'paris', 'lyon', 'marseille'], ('French', 'France')),
    (['germany', 'berlin', 'munich', 'münchen', 'hamburg', 'cologne'], ('German', 'Germany')),
    (['italy', 'rome', 'roma', 'milan', 'milano', 'naples'], ('Italian', 'Italy')),
    (['poland', 'warsaw', 'krakow', 'kraków'], ('Polish', 'Poland')),
    (['sweden', 'stockholm'], ('Swedish', 'Sweden')),
    (['norway', 'oslo'], ('Norwegian', 'Norway')),
    (['denmark', 'copenhagen'], ('Danish', 'Denmark')),
    (['russia', 'moscow'], ('Russian', 'Russia')),
    (['japan', 'tokyo', 'osaka'], ('Japanese', 'Japan')),
    (['china', 'beijing', 'shanghai'], ('Mandarin Chinese', 'China')),
    (['greece', 'athens'], ('Greek', 'Greece')),
    (['turkey', 'istanbul', 'ankara'], ('Turkish', 'Turkey')),
]


def _home_language(location):
    """Infer (language, country) from a location string. Defaults to English."""
    loc = (location or '').lower()
    for keys, lang in _LANGUAGE_BY_PLACE:
        if any(k in loc for k in keys):
            return lang
    return ('English', None)


def _language_block(location, mirror_location):
    """Language rules: start in English; only switch to the persona's home
    language when the fan signals they'd rather chat in it."""
    if mirror_location:
        return (
            "\n\nLanguage rules:\n"
            "- Always start and conduct the conversation in English.\n"
            "- If the fan clearly prefers another language — they write to you in it or ask to switch — "
            "and it is the native language of where they (and therefore you) are from, you may switch to it.\n"
            "- Never reply in a language other than English unless the fan has made that preference clear. "
            "If they use a language you wouldn't plausibly speak, keep replying in English."
        )
    language, country = _home_language(location)
    if language == 'English':
        return (
            "\n\nLanguage rules:\n"
            "- You only ever respond in English. Never reply in any other language, "
            "even if the fan writes to you in one."
        )
    return (
        "\n\nLanguage rules:\n"
        f"- You are fluent in English and {language} ({country}). You must NEVER respond in any other language.\n"
        "- Always start and conduct the conversation in English by default.\n"
        f"- Only switch to {language} if the fan makes it clear they'd rather chat in {language} — "
        f"for example they write to you in {language} or ask you to. Once they do, reply in {language}.\n"
        f"- If the fan is also from {country}, it's natural to speak {language} with them.\n"
        f"- Never reply in a language other than English or {language}. If the fan writes in some other "
        "language, answer in English."
    )


# How fast she feels. Reply speed is the pause before she starts typing (she is
# "reading"); typing speed is how long the bubble runs, scaled by message length.
# The wait and the typing time are paid one after the other, on top of the
# model round trip, so the old numbers put "slow" at 13-17s before a single
# word appeared and ~20s for a two-bubble reply — long enough to read as
# broken rather than unhurried. Retuned to land slow around 7s to first word.
# The whole ladder moves together: these are relative, and dropping only slow
# would have made it faster than natural.
REPLY_SPEED = {
    'instant': {'base': 200,  'jitter': 300,  'per_char': 0},
    'fast':    {'base': 600,  'jitter': 700,  'per_char': 8},
    'natural': {'base': 1100, 'jitter': 1600, 'per_char': 18},
    'slow':    {'base': 2000, 'jitter': 2600, 'per_char': 32},
}

TYPING_SPEED = {
    'fast':    {'base': 400,  'per_char': 18, 'max': 3000},
    'natural': {'base': 700,  'per_char': 30, 'max': 5000},
    'slow':    {'base': 1100, 'per_char': 45, 'max': 7000},
}


def _pacing(config):
    """Delivery timings for one persona, as plain numbers the chat client can
    apply directly. Kept out of the system prompt: how long she waits is not
    something the model should be asked to simulate."""
    reply = REPLY_SPEED.get(config.get('reply_speed'), REPLY_SPEED['natural'])
    typing = TYPING_SPEED.get(config.get('typing_speed'), TYPING_SPEED['natural'])
    return {'reply': dict(reply), 'typing': dict(typing)}


_SPICY_ASK_RE = re.compile(
    r'\b('
    r'nudes?|naked|nsfw|spicy|sexy\s+(pic|photo|video)s?|'
    r'(show|send|got|have|see)\s+(me\s+)?(some\s+|any\s+|more\s+|your\s+)?'
    r'(pic|pics|photo|photos|vid|vids|video|videos|tits|ass|body|content)|'
    r'more\s+(spicy|naughty|explicit|of\s+you)|'
    r'(let\s+me\s+see|wanna\s+see|want\s+to\s+see)|'
    # naakt / laat zien / stuur een foto — Dutch
    r'naakt\w*|(laat|mag ik)\s+(me\s+|het\s+)?zien|'
    r'(stuur|heb je|mag ik)\s+(me\s+|mij\s+|een\s+|je\s+)*(foto|fotos|foto\'s|filmpje|video)\w*|'
    # nackt / zeig mir — German
    r'nackt\w*|zeig\s+mir|(schick|hast du)\s+(mir\s+)?(ein\s+|deine\s+)?(bild|bilder|foto)\w*|'
    # nue / montre-moi — French
    r'\bnue?s?\b|montre[-\s]moi|(envoie|as-tu)\s+(moi\s+)?(une\s+|des\s+|tes\s+)?photos?|'
    # desnuda / enséñame — Spanish / Portuguese
    r'desnuda\w*|ens\u00e9\u00f1ame|mu\u00e9strame|(m\u00e1ndame|manda)\s+(una\s+|unas\s+|tus\s+)?fotos?|'
    r'pelada\w*|me\s+mostra|'
    # nuda / mostrami — Italian
    r'nuda\w*|mostrami|(mandami|hai)\s+(una\s+|delle\s+|le tue\s+)?foto'
    r')\b', re.I)


def _spicy_asked(text):
    """True when the fan is asking to see explicit content. Distinct from
    _cta_asked, which is them asking where else to find her."""
    return bool(_SPICY_ASK_RE.search(text or ''))


# Legacy stored prompts still carry "[PPV]" examples, and PPV is a Fanvue-only
# concept — anywhere else the marker just leaks into the bubble.
_PPV_MARKER_RE = re.compile(r'\s*\[\s*ppv\s*\]\s*', re.I)


def strip_ppv_marker(text):
    return _PPV_MARKER_RE.sub(' ', text or '').strip()


def build_system_prompt(config):
    """Generate a system prompt from persona builder config dict."""
    name = config.get('name', 'Aria')
    age = config.get('age', '22')
    gender = config.get('gender', 'Female')
    location = config.get('location', '')
    backstory = config.get('backstory', '')
    archetype = config.get('archetype', 'Friendly')
    speech_style = config.get('speech_style', 'Short, natural sentences.')
    warmth = int(config.get('warmth', 3))
    flirt_pace = config.get('flirt_pace', 'moderate')
    nsfw_enabled = config.get('nsfw_enabled', False)
    nsfw_level = config.get('nsfw_level', 'suggestive')
    interests = config.get('interests', '')
    conversion_triggers = config.get('conversion_triggers', '')
    question_rule = question_freq_rule(config)
    mirror_location = config.get('mirror_location', False)
    emoji_use = config.get('emoji_use', 'none')
    reply_length = config.get('reply_length', 'short')
    lowercase = config.get('lowercase', True)

    # No location set → automatically match the fan's location.
    if not (location or '').strip():
        mirror_location = True
        location = ''

    warmth_map = {1: 'cold and distant', 2: 'reserved', 3: 'friendly', 4: 'warm', 5: 'affectionate'}
    warmth_desc = warmth_map.get(warmth, 'friendly')

    # A cold or reserved persona was still told to build genuine warmth, which
    # pulls straight against her own warmth setting. She stays attentive at the
    # low end — she just does not perform affection she is not set to have.
    if warmth <= 2:
        rapport_note = ('Earn her interest slowly — pay attention and remember what the fan says, '
                        'but stay measured. Do not gush, and do not act closer than you feel.')
    else:
        rapport_note = 'Build genuine warmth — the fan must like you as a person before anything else happens.'

    # These three were hardcoded, which silently contradicted any creator who
    # wrote "uses lots of emojis" or "long rambling messages" in Speech Style.
    # The defaults reproduce the old behaviour exactly.
    emoji_map = {
        'none': 'No emojis. Zero. Not even one.',
        'rare': 'Emojis almost never — at most one every several messages, and only when it genuinely lands.',
        'some': 'The odd emoji is fine — roughly one every few messages, never more than one per reply.',
        'lots': 'Use emojis freely, the way she texts — one or two per reply wherever they fit.',
    }
    emoji_note = emoji_map.get(emoji_use, emoji_map['none'])

    length_map = {
        'short': 'Keep each reply to 1-2 sentences only. Short, like a real text message. Never write a paragraph.',
        'medium': 'Keep each reply to 2-4 sentences. Conversational, but never a wall of text.',
        'long': 'Longer replies are fine — up to a short paragraph when she has something to say. Still never lecture.',
    }
    length_note = length_map.get(reply_length, length_map['short'])

    case_note = ('Write mostly in lowercase — only capitalise proper names (people, cities) '
                 'and the word "I". Everything else stays lowercase.') if lowercase else (
                 'Use ordinary sentence capitalisation, the way most people type.')

    nsfw_level_map = {
        'suggestive': 'Be suggestive and flirtatious. Hint at things without going explicit.',
        'moderate': 'Be openly sexual in language. Moderately explicit — describe feelings, sensations, intentions vividly.',
        'explicit': 'Go fully explicit when the moment calls for it. Direct, specific, graphic language. Match the fan\'s energy completely. Never go vanilla or pull back.'
    }
    nsfw_desc = nsfw_level_map.get(nsfw_level, nsfw_level_map['suggestive']) if nsfw_enabled else ''

    flirt_pace_note = {
        'slow': 'Take your time warming up. Stay friendly and curious before any flirtation creeps in.',
        'moderate': 'Let flirtation develop naturally across the conversation.',
        'fast': 'Be openly flirtatious once you have basic rapport — don\'t wait long.',
        'instant': 'Be playful and flirtatious from the very first reply.'
    }.get(flirt_pace, 'Let flirtation develop naturally.')

    interests_block = (
        f'\nYour personal interests: {interests}. Talk about these like you actually live them — specific opinions, real experience, small details — not just naming the topic.'
        if interests else ''
    )
    triggers_block = f'\nExtra conversion triggers: {conversion_triggers}' if conversion_triggers else ''
    language_block = _language_block(location, mirror_location)

    location_clause = '' if mirror_location else (f', from {location}' if location else '')
    location_block = ''
    if mirror_location:
        location_block = (
            "\n- You claim to be from the same place as the fan. If you don't know yet, ask casually early on, "
            "then treat their city as your own. Drop in local references naturally. Never reveal you're doing this."
        )

    spicy_mode = config.get('spicy_cta', 'normal')

    ppv_block = ''
    if nsfw_enabled:
        ppv_block = f"""

Sales funnel (follow this arc across the conversation):
- STEP 1 — WELCOME: Open warmly. Introduce yourself as {name}, {age}{location_clause}. Learn their name and a little about them.
- STEP 2 — CONNECT: When they reply, pick up on their name, age, or location. Make a playful comment that creates a real-world connection — e.g. if they're from California, say you've always wanted to visit and you've just found your tour guide. Make them feel like they could actually meet you.
- STEP 3 — BOND OVER INTERESTS: Get onto what they enjoy doing for fun. When they tell you, share one of your own interests and pick something from their list you'd genuinely want to try with them. Make it feel like a real plan, not just small talk.
- STEP 4 — FIND OUT WHAT THEY'RE LOOKING FOR: Work out what they're here for on the platform. Have a real conversation about it. Don't rush past this.
- STEP 5 — HEAT UP (SEXTING BEGINS): Steer onto what they're into. When they share, tell them yours. Match their energy and go slightly further each time.
- STEP 6 — IGNITE: Get them picturing what they'd do to you if you were together right now. Be vivid in your reply. Create scenes. Describe what YOU would do to them. Build heat, don't resolve it.
- STEP 7 — CHALLENGE: Tell them you're getting really naughty and challenge them — say something like "I don't think you could handle me when I really let go". Men hate being challenged. Keep raising the stakes each time they respond.
- STEP 8 — FIRST DROP: After the tension peaks, send something. Write a caption so intriguing they have to know what's inside — tease a specific body part, action, or scenario without revealing it. Example: "I recorded something for you earlier… I wasn't going to send it but here"
- STEP 9 — KEEP THE THREAD HOT: After they react, keep sexting for 2-3 more messages. Stay in the scene.
- STEP 10 — SECOND DROP: Send again with a caption that promises more of what they just saw — or goes one level further. Example: "That was just the warm-up… wait til you see what comes next"
- STEP 11 — LADDER UP: Repeat — sext 2-3 messages, then drop again, escalating each time. Keep returning to conversation between drops so it never feels like a hard sell.
- After the final drop, bring the conversation back down. Be warm, funny, turn it back to them. The goal is they come back tomorrow.
- The Voice rules above outrank every step here — emoji use, sentence length, lowercase, all of it. A step never licenses breaking them.
- A step is a direction, not a question to fire off. The question frequency rule above overrides every step here: when it says not to ask, move the step forward by sharing something of your own and let them come to you. A step can take several messages.
- NEVER write a marker, a tag or a bracketed word like "[PPV]" in your message. What you send is decided outside the text — whether that is a photo here or a paid unlock on a paid platform. Write only the caption, in your own voice.{nsfw_desc and chr(10) + '- Content level: ' + nsfw_desc}"""

    if spicy_mode in ('fast', 'instant') and nsfw_enabled:
        ppv_block += (
            '\n- WHEN THEY ASK FOR SPICY CONTENT: treat it as a buying signal, not an '
            'interruption. Skip straight ahead in the arc — tease once, then make the '
            'offer' + (' in that very same reply.' if spicy_mode == 'instant'
                       else ' within the next reply or two.'))

    prompt = f"""You are {name}, {age}, {gender}{location_clause}. {backstory}

Personality: {archetype}. {speech_style}

Voice rules:
- You are {warmth_desc} in your interactions.
- {flirt_pace_note}
- {length_note}
- {question_rule}
- {emoji_note}
- Always complete every sentence. Never cut off mid-thought.
- {case_note}
- Always write numbers as digits, never as words. Write "3" not "three", "19" not "nineteen".
- Your opening message to a new fan must always feel fresh and slightly different — vary the phrasing and opener every time, but keep the same casual warm vibe.{location_block}{interests_block}

Memory rules (CRITICAL):
- Read the full conversation history before every reply.
- NEVER repeat yourself. If you said something already, do not say it again — not the same sentence, not the same question, not the same phrasing.
- NEVER ask for information already provided. If the fan gave their name, use it. If they gave their location, don't ask again.
- NEVER ask a question you already asked. If you're waiting for an answer, reference that wait instead of asking again.
- Reference specific things the fan said — their exact words, their name, their location — to show you were listening.

Character rules:
- Stay 100% in character as {name} at all times. Never mention being an AI.
- Sound like a real person texting — casual, natural, not robotic or formal.
- React to what was just said first. Don't jump ahead to a new topic before acknowledging the current one.
- {rapport_note}
- ANSWER ORDINARY QUESTIONS PROPERLY. If the fan asks something normal — what you like, what you do, how your day was, what you are up to, where you are from — give a real, specific answer about yourself, then ask them something back. Deflecting a normal question with "that's for another time", "you'll have to find out", "some things are better discovered" or anything like it is WRONG: it reads as evasive and kills the conversation. Only genuinely private things (your address, real name, phone number) are off limits, and you say that plainly rather than being coy.
- Teasing is ONLY for sexual or explicit requests. When the fan pushes there: engage — don't shut it down or go cold — but make clear that YOU set the pace, not them. Be playful about it: tease, hint, slow things down deliberately. A response like "easy... I go at my own speed" keeps them hooked without giving everything at once.
- Never deflect the same thing twice. If the fan asks again, or says you did not answer, ANSWER IT — properly and directly. Repeated dodging is the fastest way to lose them.{ppv_block}{triggers_block}

You are {name} in a text conversation on a fan platform. Respond only as {name}. One short text at a time.{language_block}"""

    return prompt.strip()


# --- Gemini client init ---
client = None
auth_mode = None

PROJECT_ID = os.getenv('GOOGLE_CLOUD_PROJECT', '793708886252')
LOCATION = os.getenv('GOOGLE_CLOUD_LOCATION', 'us-central1')

ENV_PATH = os.path.join(BASE_DIR, '.env')


def update_env_var(key, value):
    """Insert or replace KEY=value in the project .env, preserving other lines."""
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    out = []
    found = False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '=' in stripped:
            if stripped.split('=', 1)[0].strip() == key:
                out.append(f'{key}={value}\n')
                found = True
                continue
        out.append(line)
    if not found:
        if out and not out[-1].endswith('\n'):
            out[-1] += '\n'
        out.append(f'{key}={value}\n')
    with open(ENV_PATH, 'w', encoding='utf-8') as f:
        f.writelines(out)


def init_gemini_client():
    """(Re)initialize the global Gemini client from current environment.
    Returns an error string on failure, or None on success."""
    global client, auth_mode
    google_creds = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    api_key = os.getenv('GEMINI_API_KEY')
    try:
        if google_creds and os.path.exists(google_creds):
            from google.oauth2 import service_account
            credentials = service_account.Credentials.from_service_account_file(
                google_creds,
                scopes=['https://www.googleapis.com/auth/cloud-platform']
            )
            client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION, credentials=credentials)
            auth_mode = 'service-account'
            print(f'Gemini: service account (project={PROJECT_ID})')
        elif api_key:
            client = genai.Client(api_key=api_key)
            auth_mode = 'api-key'
            print('Gemini: API key')
        else:
            client = None
            auth_mode = None
            print('WARNING: No Gemini credentials. Using local fallback.')
        return None
    except Exception as e:
        client = None
        auth_mode = None
        print(f'WARNING: Gemini init failed: {e}')
        return str(e)


init_gemini_client()

MODEL_NAME = 'gemini-2.5-flash'

# Default persona slug (used when no persona param given)
DEFAULT_PERSONA = 'lilly'

# Cache of loaded system prompts {slug: str}
_prompt_cache = {}


# Personas saved before this rule existed still carry the old "tease everything"
# wording, which had her dodging ordinary questions. Append it at load time so
# every persona gets it without being re-saved in the builder.
ANSWER_RULE = """

Answering rules (CRITICAL):
- ANSWER ORDINARY QUESTIONS PROPERLY. If the fan asks something normal — what you like, what you do, how your day was, what you are up to, where you are from — give a real, specific answer about yourself, then ask them something back. Deflecting a normal question with "that's for another time", "you'll have to find out", "some things are better discovered" or anything like it is WRONG: it reads as evasive and kills the conversation.
- Only genuinely private things (your address, real name, phone number) are off limits, and you say that plainly rather than being coy.
- Teasing and holding back are ONLY for sexual or explicit requests, never for getting-to-know-you questions.
- Never deflect the same thing twice. If the fan asks again, or says you did not answer, ANSWER IT — properly and directly. Repeated dodging is the fastest way to lose them."""


def get_system_prompt(slug):
    if slug not in _prompt_cache:
        prompt = load_persona_prompt(slug)
        if not prompt:
            prompt = load_persona_prompt(DEFAULT_PERSONA) or 'You are a friendly assistant.'
        if 'ANSWER ORDINARY QUESTIONS' not in prompt:
            prompt = prompt.rstrip() + ANSWER_RULE
        if 'Question frequency:' not in prompt:
            prompt = prompt.rstrip() + '\n\n' + question_freq_rule(load_persona_config(slug))
        _prompt_cache[slug] = prompt
    return _prompt_cache[slug]


def local_fallback_reply(msg):
    lower = msg.lower().strip()
    if any(x in lower for x in ['hi', 'hello', 'hey']):
        return "Hey. What's your story?"
    if 'how are you' in lower or 'you doing' in lower:
        return "Good. You?"
    if 'name' in lower or 'who are you' in lower:
        return "You already know. What's yours?"
    return "Yeah. What do you think?"


app = Flask(__name__, static_folder=BASE_DIR, static_url_path='',
            template_folder=os.path.join(BASE_DIR, 'templates'))
def _session_secret():
    """A signing key that survives restarts. Prefers SECRET_KEY, else reuses one
    kept in the database, so sessions on Cloud Run outlive a new revision
    instead of silently logging everyone out."""
    env = (os.getenv('SECRET_KEY') or '').strip()
    if env:
        return env
    try:
        from db import SessionLocal, get_app_setting, set_app_setting
        s = SessionLocal()
        try:
            stored = get_app_setting(s, 'flask_secret_key')
            if not stored:
                stored = secrets.token_hex(32)
                set_app_setting(s, 'flask_secret_key', stored)
                s.commit()
            return stored
        finally:
            s.close()
    except Exception:
        # No database yet — fall back to a per-process key.
        return secrets.token_hex(32)


try:
    from db import init_db
    init_db()
except Exception as _db_err:
    print(f'DB init skipped: {_db_err}')

# After init_db, so the settings table exists to read the stored key from.
app.secret_key = _session_secret()
app.permanent_session_lifetime = timedelta(days=30)   # "keep me signed in"


def _persistence_warnings():
    """Config that silently loses customer accounts, surfaced at startup.

    Both are fatal to sign-in on serverless: an ephemeral SQLite file is wiped
    between cold starts, and a per-instance random secret key means a session
    cookie signed by one instance is rejected by the next.
    """
    warns = []
    try:
        from db import DATABASE_URL
    except Exception:
        return warns
    if DATABASE_URL.startswith('sqlite'):
        where = 'an ephemeral /tmp file' if IS_VERCEL else 'a local file'
        warns.append(
            f'Database is SQLite ({where}). Accounts and payments will not '
            'survive a redeploy or cold start — set DATABASE_URL to Postgres.')
    if not (os.getenv('SECRET_KEY') or '').strip() and DATABASE_URL.startswith('sqlite'):
        warns.append(
            'SECRET_KEY is unset and there is no durable database to keep a '
            'generated one in, so sign-in will not survive a restart.')
    return warns


for _w in _persistence_warnings():
    print(f'CONFIG WARNING: {_w}')


# The app serves static assets straight from the project root (static_folder=
# BASE_DIR), which otherwise publishes the source, the database and .env at "/".
# Everything the browser legitimately needs is html/css/js/images, so block the
# rest before Flask's static handler ever sees it.
_BLOCKED_SUFFIXES = ('.py', '.pyc', '.pyo', '.db', '.sqlite', '.sqlite3', '.db-journal',
                     '.log', '.env', '.pem', '.key', '.cfg', '.ini', '.toml', '.lock',
                     '.txt', '.md', '.yml', '.yaml')
_BLOCKED_DIRS = ('personas/', 'logs/', '__pycache__/', 'templates/', '.git/')
_ALLOWED_FILES = {'/robots.txt', '/sitemap.xml'}


@app.before_request
def _block_source_files():
    path = (request.path or '/').lower()
    if path in _ALLOWED_FILES:
        return None
    stripped = path.lstrip('/')
    if any(seg.startswith('.') for seg in stripped.split('/') if seg):
        return ('Not found', 404)
    if stripped.startswith(_BLOCKED_DIRS):
        return ('Not found', 404)
    # Only guard bare files, never the app's own routes (which carry no suffix).
    if path.endswith(_BLOCKED_SUFFIXES):
        return ('Not found', 404)
    return None


# ── SEO: robots, sitemap, Search Console verification ───────────────────────
# Everything routes through Flask (vercel.json rewrites "/(.*)"), so these are
# routes rather than files on disk.

# Public, crawlable pages. Everything else is app surface behind the paywall.
_PUBLIC_PAGES = [('/', '1.0', 'weekly'),
                 ('/pricing', '0.9', 'weekly'),
                 ('/register', '0.6', 'monthly'),
                 ('/login', '0.3', 'monthly')]

# Crawling these wastes budget and can leak a creator's funnel into search.
# The fan pages (/landing, /profile, /chat.html) are deliberately absent: they
# carry <meta name="robots" content="noindex">, and a crawler blocked here would
# never fetch the page to read that tag, leaving anything already indexed stuck.
_CRAWL_DISALLOW = ['/dashboard', '/admin', '/account', '/billing', '/api/',
                   '/xbot', '/fanvue', '/threads', '/telegram', '/auth/',
                   '/logout', '/go/']


def _request_origin():
    """The origin this request actually arrived on. Cloud Run and Vercel
    terminate TLS at their proxy, so request.url_root reports http even on an
    https request; anything handed to an external service (an OAuth redirect
    URI, a payment callback) has to carry the real scheme."""
    proto = (request.headers.get('X-Forwarded-Proto') or '').split(',')[0].strip()
    origin = request.url_root.rstrip('/')
    if proto == 'https' and origin.startswith('http://'):
        origin = 'https://' + origin[len('http://'):]
    return origin


def _callback_origin():
    """Origin for URLs a third party calls back on. PUBLIC_BASE_URL wins so a
    custom domain stays stable even when the app answers on *.run.app."""
    explicit = (os.getenv('PUBLIC_BASE_URL') or '').strip().rstrip('/')
    return explicit or _request_origin()


def _site_origin():
    """Canonical origin for absolute URLs. SITE_URL wins so the canonical stays
    the marketing domain even when the app answers on a *.run.app host."""
    explicit = (os.getenv('SITE_URL') or '').strip().rstrip('/')
    return explicit or _request_origin()


def _seo_noindex_all():
    """Whole-environment opt-out. The dev service is a public copy of the live
    site, and an indexed copy competes with the real one in search."""
    return (os.getenv('SEO_NOINDEX_ALL') or '').strip() == '1'


@app.route('/robots.txt')
def robots_txt():
    if _seo_noindex_all():
        return Response('User-agent: *\nDisallow: /\n', mimetype='text/plain')
    lines = ['User-agent: *']
    lines += ['Disallow: ' + p for p in _CRAWL_DISALLOW]
    lines += ['', 'Sitemap: ' + _site_origin() + '/sitemap.xml', '']
    return Response('\n'.join(lines), mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap_xml():
    if _seo_noindex_all():
        return ('Not found', 404)
    origin = _site_origin()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    urls = ''.join(
        f'<url><loc>{origin}{path}</loc><lastmod>{today}</lastmod>'
        f'<changefreq>{freq}</changefreq><priority>{prio}</priority></url>'
        for path, prio, freq in _PUBLIC_PAGES)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + urls + '</urlset>')
    return Response(xml, mimetype='application/xml')


@app.route('/google<token>.html')
def google_site_verification(token):
    """Search Console's HTML-file verification. Set GOOGLE_SITE_VERIFICATION to
    the googleXXXX part of the filename Google hands out."""
    want = (os.getenv('GOOGLE_SITE_VERIFICATION') or '').strip()
    want = want[:-5] if want.endswith('.html') else want
    want = want[len('google'):] if want.startswith('google') else want
    if not want or token != want:
        return ('Not found', 404)
    return Response('google-site-verification: google%s.html' % want,
                    mimetype='text/html')


# ── SEA: Google Analytics 4 + Google Ads tag ────────────────────────────────
# Served as JS rather than inlined in the static pages so the measurement IDs
# stay in env vars and the tag stays absent until they are set.
@app.route('/js/analytics.js')
def analytics_js():
    ga = (os.getenv('GA_MEASUREMENT_ID') or '').strip()
    ads = (os.getenv('GOOGLE_ADS_ID') or '').strip()
    label = (os.getenv('GOOGLE_ADS_SIGNUP_LABEL') or '').strip()
    resp = Response(mimetype='application/javascript')
    if not (ga or ads):
        resp.set_data('/* analytics off: GA_MEASUREMENT_ID / GOOGLE_ADS_ID unset */')
        return resp
    primary = ga or ads
    configs = ''.join("gtag('config', %s);" % json.dumps(i)
                      for i in (ga, ads) if i)
    conversion = ''
    if ads and label:
        # /billing?signup=1 is where register() lands a brand-new account, so it
        # is the one place a signup is known to have just happened.
        conversion = ("""
if (location.pathname === '/billing' &&
    new URLSearchParams(location.search).get('signup') === '1') {
  gtag('event', 'conversion', {send_to: %s});
}""" % json.dumps(ads + '/' + label))
    resp.set_data("""(function(){
var s=document.createElement('script');
s.async=true;s.src='https://www.googletagmanager.com/gtag/js?id=%s';
document.head.appendChild(s);
window.dataLayer=window.dataLayer||[];
window.gtag=function(){dataLayer.push(arguments);};
gtag('js', new Date());
%s%s
})();""" % (primary, configs, conversion))
    return resp


# Fan-facing funnel pages. The meta tag covers the HTML; this header covers the
# same pages served as static files and any non-HTML response under them.
_NOINDEX_PATHS = ('/landing', '/landingpage.html', '/profile', '/chat')


@app.after_request
def _noindex_fan_pages(resp):
    if _seo_noindex_all() or (request.path or '/').lower().startswith(_NOINDEX_PATHS):
        resp.headers['X-Robots-Tag'] = 'noindex, nofollow'
    return resp


@app.route('/healthz')
def healthz():
    """Reports whether account storage is durable. Names no credentials —
    only the backend kind and whether required config is present."""
    try:
        from db import DATABASE_URL
        backend = DATABASE_URL.split(':', 1)[0].split('+')[0]
    except Exception:
        backend = 'unknown'
    warns = _persistence_warnings()
    return jsonify({
        'status': 'ok',
        'db': backend,
        'secret_key_set': bool((os.getenv('SECRET_KEY') or '').strip()),
        'google_login': bool(_google_oauth_config()[0]),
        'payments_oxapay': bool(_oxapay_key()),
        'payments_stripe': bool(_stripe_key()),
        'accounts_persist': not warns,
        'warnings': warns,
    }), 200


X_TOKENS_FILE = '/tmp/x_tokens.json' if IS_VERCEL else os.path.join(BASE_DIR, 'x_tokens.json')
X_OAUTH_STATE_FILE = '/tmp/x_oauth_state.json' if IS_VERCEL else os.path.join(BASE_DIR, '.x_oauth_state.json')


def _load_x_tokens():
    if os.path.exists(X_TOKENS_FILE):
        with open(X_TOKENS_FILE, 'r') as f:
            return json.load(f)
    return {}


def _save_x_tokens(data):
    with open(X_TOKENS_FILE, 'w') as f:
        json.dump(data, f)


# ── Customer accounts, tiers and the paywall ─────────────────────────────────

CURRENCY = 'EUR'
CURRENCY_SYMBOL = '€'
# 3 months free when billed annually (~25% off): a year costs 9x the month.
ANNUAL_SUFFIX = '_annual'
ANNUAL_MONTHS_CHARGED = 9
ANNUAL_SAVE_PCT = round((12 - ANNUAL_MONTHS_CHARGED) / 12 * 100)

# No free tier — every account picks a paid plan. `days` is how long one
# payment keeps the account active; the *_annual twin below is generated from
# the same features so the two never drift apart.
# `capabilities` is the machine-readable half and the one enforcement reads;
# `features` is the marketing copy shown on the cards. They describe the same
# plan, so a change to one wants a matching change to the other.
_BASE_TIERS = {
    'starter': {'name': 'Starter', 'price': 49,
                'blurb': 'One persona on Fanvue, fully monetised.',
                'features': ['1 AI persona', 'Fanvue chat',
                             'Full PPV engine — ladders, per-fan pricing, '
                             'timed re-offers',
                             'Up to 3 funnel phases + CTA',
                             '15 AI image generations a month',
                             'Unlimited photo uploads', 'Email support'],
                'capabilities': {
                    'personas': 1,
                    'seats': 1,
                    'platforms': ['fanvue'],
                    'phases_max': 3,
                    'outfit_lock': False,
                    'scheduled_followups': False,
                    'analytics': False,
                    'ppv_reconcile': False,
                    'image_generations_month': 15,
                }},
    'pro': {'name': 'Pro', 'price': 149,
            'blurb': 'Five personas, every platform.',
            'features': ['5 AI personas', '2 team seats',
                         'Telegram, X, Fanvue and Threads',
                         'Outfit locking + media tagging',
                         'Up to 10 funnel phases with photo rates',
                         'Scheduled follow-ups',
                         '75 AI image generations a month',
                         'Priority support'],
            'capabilities': {
                'personas': 5,
                'seats': 2,
                'platforms': None,
                'phases_max': 10,
                'outfit_lock': True,
                'scheduled_followups': True,
                'analytics': False,
                'ppv_reconcile': False,
                'image_generations_month': 75,
            }},
    'agency': {'name': 'Agency', 'price': 349,
               'blurb': 'Fifteen personas and a team to run them.',
               'features': ['15 AI personas', '6 team seats with roles',
                            'Every platform',
                            'Outfit locking + media tagging',
                            'Conversation and revenue analytics',
                            'PPV reconciliation against Fanvue earnings',
                            '225 AI image generations a month',
                            'Dedicated support'],
               'capabilities': {
                   'personas': 15,
                   'seats': 6,
                   'platforms': None,
                   'phases_max': 10,
                   'outfit_lock': True,
                   'scheduled_followups': True,
                   'analytics': True,
                   'ppv_reconcile': True,
                   'image_generations_month': 225,
               }},
}
DEFAULT_TIER_ORDER = ['starter', 'pro', 'agency']

# Quote-only plan: never sold self-serve, so it stays out of TIERS (nothing can
# activate or charge for it) and is rendered as a fourth, not-yet-open card.
CUSTOM_TIER = {
    'name': 'Custom',
    'blurb': 'More than 15 personas, or something built to fit.',
    'price_label': "Let's talk",
    'features': ['Unlimited AI personas', 'Every platform',
                 'Photo sending + outfit locking',
                 'PPV selling tuned to your catalogue',
                 'Custom funnel phases + integrations',
                 'Onboarding and roster migration',
                 'Named contact on our team'],
    'cta': 'Coming soon',
    'note': 'Not open yet.',
    'coming_soon': True,
}

TIERS = {}
for _key, _base in _BASE_TIERS.items():
    TIERS[_key] = {**_base, 'days': 30, 'period': 'month'}
    _annual_price = _base['price'] * ANNUAL_MONTHS_CHARGED
    TIERS[_key + ANNUAL_SUFFIX] = {**_base, 'price': _annual_price, 'days': 365,
                                   'period': 'year', 'monthly_of': _key,
                                   'monthly_equiv': round(_annual_price / 12, 2)}
del _key, _base, _annual_price

OXAPAY_API = 'https://api.oxapay.com/v1/payment/invoice'
STRIPE_API = 'https://api.stripe.com/v1'
# Software as a service (SaaS) — business use. Required by Managed Payments.
STRIPE_TAX_CODE = 'txcd_10103001'


def _oxapay_key():
    return (os.getenv('OXAPAY_MERCHANT_KEY') or '').strip()


def _stripe_key():
    return (os.getenv('STRIPE_SECRET_KEY') or '').strip()


def _stripe_webhook_secret():
    return (os.getenv('STRIPE_WEBHOOK_SECRET') or '').strip()


def _dev_payments_enabled():
    """Dev-only: lets a plan be activated without paying. Off unless
    DEV_FAKE_PAYMENTS=1 is set explicitly, so production can never hit it."""
    return (os.getenv('DEV_FAKE_PAYMENTS') or '').strip() == '1'


def _db_session():
    from db import SessionLocal
    return SessionLocal()


# The owner's account, promoted automatically so admin works with no config.
# ADMIN_EMAILS overrides this entirely when set (comma-separated).
DEFAULT_ADMIN_EMAILS = 'jeffrey.kluijtmans@gmail.com'


def _bootstrap_admins():
    """Emails here are promoted to admin on sign-in, so the first admin exists
    without a console. Everyone else must be promoted by an admin."""
    raw = (os.getenv('ADMIN_EMAILS') or DEFAULT_ADMIN_EMAILS).strip()
    return {e.strip().lower() for e in raw.split(',') if e.strip()}


def _current_user():
    """The logged-in customer, or None. Refreshes an expired subscription."""
    uid = session.get('user_id')
    if not uid:
        return None
    from db import User
    s = _db_session()
    try:
        u = s.get(User, uid)
        if u is None:
            return None
        if (u.status == 'active' and u.expires_at
                and u.expires_at < datetime.now(timezone.utc).replace(tzinfo=None)):
            u.status = 'expired'
            s.commit()
        if u.role != 'admin' and u.email in _bootstrap_admins():
            u.role = 'admin'
            s.commit()
            logger.info('ADMIN BOOTSTRAPPED from ADMIN_EMAILS: %s', u.email)
        role = u.role or 'user'
        ws, seat_role = _active_workspace(s, u)
        # A seat draws its plan from the workspace owner: only the owner is
        # billed, so a member's access has to follow the owner's subscription.
        owner = u if (ws is None or ws.owner_id == u.id) else s.get(User, ws.owner_id)
        owner = owner or u
        tier, status = owner.tier, owner.status
        expires_at, grandfathered = owner.expires_at, owner.grandfathered_until
        if (owner.id != u.id and status == 'active' and expires_at
                and expires_at < datetime.now(timezone.utc).replace(tzinfo=None)):
            status = 'expired'
        return {'id': u.id, 'email': u.email, 'name': u.name, 'tier': tier,
                'status': status, 'role': role,
                'workspace_id': ws.id if ws is not None else u.id,
                'workspace_name': (ws.name if ws is not None else '') or owner.email,
                'workspace_owner_id': owner.id,
                'seat_role': seat_role,
                'is_admin': role == 'admin',
                'stripe_customer_id': u.stripe_customer_id or '',
                'stripe_subscription_id': u.stripe_subscription_id or '',
                'grandfathered_until': (grandfathered.isoformat()
                                        if grandfathered else None),
                'expires_at': expires_at.isoformat() if expires_at else None}
    finally:
        s.close()


def _active_workspace(s, u):
    """(workspace, seat_role) for this request. The chosen workspace is held in
    the session so a switch survives navigation; an invalid or stale choice
    falls back to one the user is actually a member of rather than erroring."""
    from db import User, Workspace, Membership, list_memberships, create_workspace
    rows = list_memberships(s, u.id)
    if not any(m.role == 'owner' for m, _ in rows):
        # Sign-up, Google sign-in and the admin console all create users, so the
        # own-workspace guarantee lives here rather than in each of them. A user
        # who only holds seats still needs somewhere of their own to come back to.
        ws = s.get(Workspace, u.id)
        if ws is None:
            ws = create_workspace(s, u.id, u.brand or u.name or u.email, u.id)
        elif s.query(Membership).filter(Membership.workspace_id == ws.id,
                                        Membership.user_id == u.id).first() is None:
            s.add(Membership(workspace_id=ws.id, user_id=u.id, role='owner'))
        s.commit()
        rows = list_memberships(s, u.id)
    chosen = session.get('workspace_id') or ''
    for m, ws in rows:
        if ws.id == chosen:
            return ws, (m.role or 'chatter')
    # No choice made yet. Prefer a workspace that can actually be used: someone
    # invited purely as a chatter has an empty, unpaid workspace of their own,
    # and landing them there would greet them with the paywall.
    for m, ws in rows:
        owner = s.get(User, ws.owner_id)
        if owner is not None and owner.status == 'active':
            return ws, (m.role or 'chatter')
    m, ws = rows[0]
    return ws, (m.role or 'chatter')


def _user_is_active(user):
    """Admins are never paywalled — they get the whole app regardless of plan."""
    return bool(user) and (user.get('is_admin') or user.get('status') == 'active')


def _activate_plan(session_db, user_row, tier_key):
    """Put a user on a plan. Renewals extend unexpired time rather than
    truncating it. Returns the new expiry."""
    tier = TIERS.get(tier_key) or {}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start = (user_row.expires_at
             if (user_row.expires_at and user_row.expires_at > now) else now)
    user_row.tier = tier_key
    user_row.status = 'active'
    user_row.expires_at = start + timedelta(days=int(tier.get('days', 30)))
    return user_row.expires_at


DENIED_CAPS = {'personas': 0, 'seats': 0, 'platforms': [], 'phases_max': 0,
               'outfit_lock': False, 'scheduled_followups': False,
               'analytics': False, 'ppv_reconcile': False,
               'image_generations_month': 0}
# None means "no limit" throughout, for both counts and the platform allow-list.
UNLIMITED_CAPS = {k: (None if not isinstance(v, bool) else True)
                  for k, v in DENIED_CAPS.items()}


def tier_capabilities(tier_key):
    """What a plan may do. The annual twins are generated from the same base,
    so they must resolve to the same capabilities as their monthly sibling."""
    key = (tier_key or '').strip()
    base = _BASE_TIERS.get(key) or _BASE_TIERS.get(
        key[:-len(ANNUAL_SUFFIX)] if key.endswith(ANNUAL_SUFFIX) else key)
    if not base:
        return dict(DENIED_CAPS)
    return {**DENIED_CAPS, **base.get('capabilities', {})}


def _is_grandfathered(user):
    """Accounts that were already paying when per-tier limits arrived keep the
    old unlimited entitlements until the date they have paid through, so
    enforcement is not a retroactive downgrade mid-subscription."""
    raw = (user or {}).get('grandfathered_until')
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return False
    if until.tzinfo is not None:
        until = until.astimezone(timezone.utc).replace(tzinfo=None)
    return until > datetime.now(timezone.utc).replace(tzinfo=None)


def user_capabilities(user):
    if not user:
        return dict(DENIED_CAPS)
    if user.get('is_admin'):
        return dict(UNLIMITED_CAPS)
    if user.get('status') == 'active' and _is_grandfathered(user):
        return dict(UNLIMITED_CAPS)
    if user.get('status') != 'active':
        return dict(DENIED_CAPS)
    return tier_capabilities(user.get('tier'))


def _workspace_id(user):
    return (user or {}).get('workspace_id') or (user or {}).get('id') or ''


def _cap_denied(name, user, extra=None):
    """The 402 body for a capability the plan does not include. Names the
    capability and the cheapest tier that has it, so the UI can offer the
    right upgrade instead of a generic paywall bounce."""
    have = user_capabilities(user).get(name)
    upgrade = ''
    for key in DEFAULT_TIER_ORDER:
        val = tier_capabilities(key).get(name)
        # For a graded cap the next plan up is the first one that raises the
        # number, not the first one that has it at all.
        if val is None or (isinstance(have, int) and not isinstance(have, bool)
                           and isinstance(val, int) and val > have) or (
                val is True and not have):
            upgrade = key
            break
    body = {'error': 'Not included in your plan', 'capability': name,
            'current_tier': (user or {}).get('tier') or '',
            'upgrade_to': upgrade}
    body.update(extra or {})
    return jsonify(body), 402


def _persona_count(user):
    return len(db_list_personas(owner_id=_workspace_id(user)))


def _persona_cap_blocked(user):
    """The 402 response when this plan is already at its persona limit, or
    None when there is room. Called only when a *new* slug is being created."""
    limit = user_capabilities(user).get('personas')
    if limit is None:
        return None
    used = _persona_count(user)
    if used < int(limit):
        return None
    return _cap_denied('personas', user, {'used': used, 'limit': int(limit)})


def _usage_period():
    return datetime.now(timezone.utc).strftime('%Y-%m')


def _image_quota(user):
    """(used, limit) for this month. limit None means unlimited."""
    limit = user_capabilities(user).get('image_generations_month')
    if limit is None:
        return 0, None
    from db import get_usage
    s = _db_session()
    try:
        return get_usage(s, _workspace_id(user), 'image_generations',
                         _usage_period()), int(limit)
    finally:
        s.close()


# Creator-facing surface: needs a logged-in customer on an active plan.
_PAID_PAGES =('/dashboard', '/xbot', '/fanvue', '/threads', '/telegram', '/admin')
_PAID_API = ('/api/telegram', '/api/tguser', '/api/x', '/api/xlog', '/api/threads',
             '/api/fanvue', '/api/platforms', '/api/visitors', '/api/generate',
             '/api/backstory', '/api/config', '/api/whatsapp')
# Fan-facing and auth/billing routes stay open. So are inbound webhooks: they
# arrive from the platform, not a signed-in creator, and carry their own signed
# proof of origin — a sign-in redirect would just look like a failure to Fanvue.
_OPEN_PATHS = ('/login', '/register', '/logout', '/pricing', '/billing',
               '/auth/google', '/join/', '/api/workspaces',
               '/account', '/api/billing', '/healthz', '/go/', '/webhooks/',
               '/dashboard/logout', '/admin/logout',
               # OAuth landing pages: the platform's auth server redirects the
               # browser here directly, in a popup that may not even be on the
               # app's own domain (e.g. a raw Cloud Run URL configured as the
               # redirect_uri). They carry only a code/state to relay back to
               # the opener via postMessage — no session to require, same as
               # a webhook.
               '/api/fanvue/oauth-redirect', '/api/x/oauth-redirect',
               '/api/threads/oauth-redirect')


# Longest prefix wins in every map below, so a specific path can carry a
# stricter rule than the platform prefix it sits under.
_CAP_PATHS = {
    '/api/fanvue/ppv-stats': 'analytics',
    '/api/fanvue/reconcile': 'ppv_reconcile',
    '/api/telegram/stats': 'analytics',
    '/api/visitors': 'analytics',
    '/api/xlog': 'analytics',
}
# Auto-reply is the product itself — every tier's plan says the persona chats —
# so /api/{fanvue,x,threads}/auto is not gated here. Only the proactive
# follow-up interval inside it is paid; those handlers check the capability.

_PLATFORM_PATHS = {
    '/api/telegram': 'telegram', '/api/tguser': 'telegram',
    '/api/x': 'x', '/api/xlog': 'x',
    '/api/fanvue': 'fanvue', '/api/threads': 'threads',
}
_PLATFORM_PAGES = {'/telegram': 'telegram', '/xbot': 'x',
                   '/fanvue': 'fanvue', '/threads': 'threads'}
# Seat roles that may not reach a path at all. Owners and admins never appear
# here; they are filtered out before the map is consulted.
_ROLE_DENY = {
    '/api/personas': ('chatter',),
    '/api/platforms': ('chatter',),
    '/api/generate': ('chatter',),
    '/api/config': ('chatter', 'manager'),
}


def _longest_prefix(mapping, path):
    best = None
    for prefix, val in mapping.items():
        if path.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, val)
    return best[1] if best else None


def _path_needs_plan(path, method):
    if path.startswith(_OPEN_PATHS):
        return False
    if path.startswith(_PAID_PAGES) or path.startswith(_PAID_API):
        return True
    # chat.html reads personas to render the fan chat, so only writes are gated.
    if path.startswith('/api/personas') and method not in ('GET', 'HEAD', 'OPTIONS'):
        return True
    return False


@app.before_request
def _require_paid_account():
    path = request.path or '/'
    if not _path_needs_plan(path, request.method):
        return None
    user = _current_user()
    wants_json = path.startswith('/api/')
    if not user:
        if wants_json:
            return jsonify({'error': 'Sign in required'}), 401
        return redirect('/login?next=' + urllib.parse.quote(path))
    if not _user_is_active(user):
        if wants_json:
            return jsonify({'error': 'Subscription required',
                            'status': user['status']}), 402
        return redirect('/billing')
    return _require_entitlement(path, request.method, user, wants_json)


def _require_entitlement(path, method, user, wants_json):
    """Tier gates the capability, seat role gates the action, and both must
    pass. Admins skip both, matching _user_is_active."""
    if user.get('is_admin'):
        return None

    seat = user.get('seat_role') or 'owner'
    if seat not in ('owner', 'admin', 'support'):
        denied = _longest_prefix(_ROLE_DENY, path) or ()
        if seat in denied:
            logger.warning('ROLE DENIED user=%s role=%s path=%s',
                           user['email'], seat, path)
            return (jsonify({'error': 'Your role cannot do this',
                             'role': seat}), 403) if wants_json else ('Not found', 404)
    if seat == 'support' and method not in ('GET', 'HEAD', 'OPTIONS'):
        return (jsonify({'error': 'Read-only role'}), 403) if wants_json else ('Not found', 404)

    caps = user_capabilities(user)

    # Reads on a locked platform stay open so the dashboard can render the tile
    # greyed out with an upgrade prompt; only writes are refused.
    allowed = caps.get('platforms')
    if allowed is not None:
        platform = (_longest_prefix(_PLATFORM_PATHS, path)
                    if method not in ('GET', 'HEAD', 'OPTIONS') else None)
        if platform is None:
            platform = _PLATFORM_PAGES.get(path)
        if platform and platform not in allowed:
            if not wants_json:
                return redirect('/pricing')
            return _cap_denied('platform', user, {'platform': platform})

    cap = _longest_prefix(_CAP_PATHS, path)
    if cap and not caps.get(cap):
        if not wants_json:
            return redirect('/pricing')
        return _cap_denied(cap, user)
    return None


@app.before_request
def _guard_persona_writes():
    """Stop one customer editing another's persona, covering the sub-resources
    (media, images, outfits, phases) as well as the persona itself."""
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return None
    m = _PERSONA_WRITE_RE.match(request.path or '')
    if not m:
        return None
    slug = m.group(1)
    if slug == 'copy':          # creates a new slug; checked in the handler
        return None
    if not _can_edit_persona(slug, _current_user()):
        logger.warning('PERSONA WRITE DENIED slug=%s path=%s', slug, request.path)
        return jsonify({'error': 'Not found'}), 404
    return None


def _admin_password():
    return os.getenv('ADMIN_PASSWORD', '')


def _check_admin():
    """Return True if the request has a valid admin session."""
    if not _admin_password():
        return True  # no password set → open access
    return session.get('admin_authed') is True


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" href="/favicon.png">
<title>Admin Login</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0e0e0e;color:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#111113;border:1px solid #2e2e2e;border-radius:16px;padding:40px 36px;width:100%;max-width:380px}
h1{font-size:1.3rem;font-weight:700;margin-bottom:8px}
p{color:#6b6b6b;font-size:.9rem;margin-bottom:28px}
label{display:block;font-size:.8rem;color:#a7a7a7;margin-bottom:6px}
input{width:100%;background:#1c1c1c;border:1px solid #2e2e2e;border-radius:10px;padding:11px 14px;color:#fff;font-size:.95rem;outline:none;margin-bottom:20px}
input:focus{border-color:#ff5c38}
button{width:100%;background:#ff5c38;color:#fff;border:none;border-radius:10px;padding:13px;font-size:.95rem;font-weight:600;cursor:pointer}
button:hover{background:#ff2d78}
.err{background:#3f1515;border:1px solid #7f1d1d;border-radius:8px;padding:10px 14px;font-size:.85rem;color:#fca5a5;margin-bottom:16px}
</style>
</head>
<body>
<div class="card">
  <h1>Admin Login</h1>
  <p>Enter your admin password to continue.</p>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="post" autocomplete="on">
    <label>Username</label>
    <input type="text" name="username" value="admin" autocomplete="username" readonly>
    <label>Password</label>
    <input type="password" name="password" autofocus autocomplete="current-password" placeholder="••••••••">
    <button type="submit">Sign in</button>
  </form>
</div>
</body>
</html>"""


ACCOUNT_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@600;700;800&family=Inter:wght@400;500;600&display=swap');
:root{--bg:#0e0e0e;--panel:#111113;--surface:#1c1c1c;--border:#2e2e2e;
--text:#f0ece9;--text-2:#bdb8b4;--text-3:#97918d;--text-muted:#6b6663;
--accent:#ff5c38;--accent-2:#ff2d78;--accent-3:#7c3aed;
--grad:linear-gradient(120deg,#ff5c38,#ff2d78 30%,#7c3aed 60%,#ff2d78 80%,#ff5c38);
--font:'Inter',system-ui,sans-serif;--display:'Sora','Inter',system-ui,sans-serif}
/* Light theme — same token names and values as css/style.css, so these pages
   follow the theme js/theme.js stored from the dashboard. */
:root[data-theme="light"]{--bg:#faf9f8;--panel:#ffffff;--surface:#f3f1ef;--border:#d9d5d0;
--text:#1a1614;--text-2:#3b3532;--text-3:#57504b;--text-muted:#6e6762;--accent:#c53c20}
:root[data-theme="light"] .err{background:#fdecec;border-color:#f0b4b4;color:#9b1c1c}
:root[data-theme="light"] .ok{background:#e8f6ed;border-color:#a8ddbd;color:#0f7038}
:root[data-theme="light"] .tier:hover{border-color:#bdb7b1}
@keyframes sweep{0%{background-position:0% 50%}100%{background-position:150% 50%}}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--font);display:flex;align-items:center;justify-content:center;min-height:100vh;padding:32px 16px}
.wrap{width:100%;max-width:420px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:16px;padding:36px 32px}
h1,h2{font-family:var(--display);letter-spacing:-.015em}
h1{font-size:1.35rem;font-weight:800;margin-bottom:8px}
p.sub{color:var(--text-muted);font-size:.9rem;margin-bottom:24px}
label{display:block;font-size:.8rem;color:var(--text-3);margin-bottom:6px}
input{width:100%;background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 14px;color:var(--text);font-size:.95rem;outline:none;margin-bottom:16px;font-family:var(--font);transition:border-color .2s}
input:focus{border-color:var(--accent)}
button{width:100%;background:var(--grad);background-size:300% 100%;color:#fff;border:none;border-radius:12px;padding:14px;font-size:.95rem;font-weight:600;cursor:pointer;font-family:var(--font);transition:transform .1s,box-shadow .2s}
button:hover{transform:translateY(-2px);box-shadow:0 8px 26px #ff2d7855;animation:sweep .55s linear infinite}
button:disabled{opacity:.6;cursor:not-allowed;transform:none;animation:none}
.err{background:#3f1515;border:1px solid #7f1d1d;border-radius:10px;padding:10px 14px;font-size:.85rem;color:#fca5a5;margin-bottom:16px}
.ok{background:#14321f;border:1px solid #166534;border-radius:10px;padding:10px 14px;font-size:.85rem;color:#86efac;margin-bottom:16px}
.ok a,.err a{color:inherit;font-weight:600;text-decoration:underline}
.alt{text-align:center;margin-top:18px;font-size:.85rem;color:var(--text-muted)}
.alt a{color:var(--accent);text-decoration:none}
.gbtn{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;background:#fff;color:#1f1f1f;border:1px solid #dadce0;border-radius:12px;padding:13px;font-size:.95rem;font-weight:600;font-family:var(--font);cursor:pointer;text-decoration:none;transition:box-shadow .2s}
.gbtn:hover{box-shadow:0 2px 10px #0003}
.gbtn svg{width:18px;height:18px}
.orsep{display:flex;align-items:center;gap:12px;color:var(--text-muted);font-size:.78rem;margin:18px 0}
.orsep:before,.orsep:after{content:'';flex:1;height:1px;background:var(--border)}
.tiers{display:grid;gap:16px;margin-top:8px}
.tier{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:22px;transition:border-color .2s}
.tier:hover{border-color:#3d3d3d}
.tier.featured{border-color:var(--accent)}
.tier h2{font-size:1.05rem;font-weight:700;margin-bottom:4px}
.price{font-family:var(--display);font-size:1.95rem;font-weight:800;margin:10px 0 2px;letter-spacing:-.02em}
.price span{font-family:var(--font);font-size:.85rem;font-weight:400;color:var(--text-muted)}
.blurb{color:var(--text-3);font-size:.85rem;margin-bottom:12px}
.tier ul{list-style:none;margin-bottom:16px}
.tier li{font-size:.85rem;color:var(--text-2);padding:4px 0}
.tier li:before{content:'✓';color:var(--accent);margin-right:8px}
.bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;font-size:.85rem;color:var(--text-3)}
.bar a{color:var(--accent);text-decoration:none}
.ptoggle{display:flex;justify-content:center;gap:4px;background:var(--surface);border:1px solid var(--border);border-radius:999px;padding:4px;width:fit-content;margin:0 auto 22px}
.ptoggle button{width:auto;background:none;color:var(--text-2);border-radius:999px;padding:8px 18px;font-size:.85rem;box-shadow:none;animation:none}
.ptoggle button:hover{transform:none;box-shadow:none;animation:none}
.ptoggle button.active{background:var(--grad);background-size:300% 100%;color:#fff}
.ptoggle .save{font-size:.7rem;opacity:.85;margin-left:4px}
.permo{font-size:.75rem;color:var(--text-muted);margin-bottom:10px}
.tier.soon{opacity:.85}
@media(min-width:700px){.wrap.wide{max-width:760px}.tiers{grid-template-columns:repeat(2,1fr)}}
@media(min-width:1180px){.wrap.wide{max-width:1240px}.tiers{grid-template-columns:repeat(4,1fr)}}
"""

REGISTER_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><script src="/js/theme.js"></script>
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" href="/favicon.png"><title>Create account</title>
<script src="/js/analytics.js" defer></script>
<script src="/js/page-editor.js" defer></script>
<style>""" + ACCOUNT_CSS + """</style></head><body data-page="register"><div class="wrap"><div class="card">
<h1 data-edit-id="h1">Create your account</h1><p class="sub" data-edit-id="sub">Start building your AI persona.</p>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
{% if google_enabled %}<a class="gbtn" href="/auth/google{{ google_next }}"><svg viewBox="0 0 48 48"><path fill="#4285F4" d="M45.1 24.5c0-1.6-.1-3.1-.4-4.5H24v8.5h11.8c-.5 2.8-2 5.1-4.4 6.7v5.5h7.1c4.2-3.8 6.6-9.5 6.6-16.2z"/><path fill="#34A853" d="M24 46c6 0 11-2 14.5-5.3l-7.1-5.5c-2 1.3-4.5 2.1-7.4 2.1-5.7 0-10.6-3.9-12.3-9.1H4.3v5.7C7.8 41 15.3 46 24 46z"/><path fill="#FBBC05" d="M11.7 28.2c-.4-1.3-.7-2.7-.7-4.2s.2-2.9.7-4.2v-5.7H4.3C2.8 17.1 2 20.4 2 24s.8 6.9 2.3 9.9l7.4-5.7z"/><path fill="#EA4335" d="M24 10.7c3.2 0 6.1 1.1 8.4 3.3l6.3-6.3C35 4.1 30 2 24 2 15.3 2 7.8 7 4.3 14.1l7.4 5.7c1.7-5.2 6.6-9.1 12.3-9.1z"/></svg>Continue with Google</a>
<div class="orsep">or</div>{% endif %}
<form method="post">
<label>Name</label><input type="text" name="name" autocomplete="name" value="{{ name or '' }}">
<label>Email</label><input type="email" name="email" required autocomplete="email" value="{{ email or '' }}">
<label>Password</label><input type="password" name="password" required autocomplete="new-password" placeholder="At least 8 characters">
<button type="submit"><span data-edit-id="submit-text">Create account</span></button></form>
<div class="alt"><span data-edit-id="alt-text">Already have an account?</span> <a href="/login">Sign in</a></div>
</div></div></body></html>"""

SIGNIN_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><script src="/js/theme.js"></script>
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" href="/favicon.png"><title>Sign in</title>
<script src="/js/analytics.js" defer></script>
<script src="/js/page-editor.js" defer></script>
<style>""" + ACCOUNT_CSS + """</style></head><body data-page="login"><div class="wrap"><div class="card">
<h1 data-edit-id="h1">Sign in</h1><p class="sub" data-edit-id="sub">Welcome back.</p>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
{% if google_enabled %}<a class="gbtn" href="/auth/google{{ google_next }}"><svg viewBox="0 0 48 48"><path fill="#4285F4" d="M45.1 24.5c0-1.6-.1-3.1-.4-4.5H24v8.5h11.8c-.5 2.8-2 5.1-4.4 6.7v5.5h7.1c4.2-3.8 6.6-9.5 6.6-16.2z"/><path fill="#34A853" d="M24 46c6 0 11-2 14.5-5.3l-7.1-5.5c-2 1.3-4.5 2.1-7.4 2.1-5.7 0-10.6-3.9-12.3-9.1H4.3v5.7C7.8 41 15.3 46 24 46z"/><path fill="#FBBC05" d="M11.7 28.2c-.4-1.3-.7-2.7-.7-4.2s.2-2.9.7-4.2v-5.7H4.3C2.8 17.1 2 20.4 2 24s.8 6.9 2.3 9.9l7.4-5.7z"/><path fill="#EA4335" d="M24 10.7c3.2 0 6.1 1.1 8.4 3.3l6.3-6.3C35 4.1 30 2 24 2 15.3 2 7.8 7 4.3 14.1l7.4 5.7c1.7-5.2 6.6-9.1 12.3-9.1z"/></svg>Continue with Google</a>
<div class="orsep">or</div>{% endif %}
<form method="post">
<label>Email</label><input type="email" name="email" required autocomplete="email" value="{{ email or '' }}">
<label>Password</label><input type="password" name="password" required autocomplete="current-password">
<label style="display:flex;align-items:center;gap:8px;margin:-4px 0 18px;color:#a1a1aa;cursor:pointer">
<input type="checkbox" name="remember" value="1" checked
 style="width:auto;margin:0;accent-color:#7c3aed;cursor:pointer">Keep me signed in for 30 days</label>
<button type="submit"><span data-edit-id="submit-text">Sign in</span></button></form>
<div class="alt"><span data-edit-id="alt-text">No account yet?</span> <a href="/register">Create one</a></div>
</div></div></body></html>"""

BILLING_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><script src="/js/theme.js"></script>
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" href="/favicon.png"><title>Choose a plan</title>
<script src="/js/analytics.js" defer></script>
<script src="/js/page-editor.js" defer></script>
<style>""" + ACCOUNT_CSS + """</style></head><body data-page="pricing"><div class="wrap wide">
<div class="bar"><span>Signed in as {{ user.email }}</span><a href="/logout">Sign out</a></div>
{% if user.status == 'active' %}
<div class="ok">Your <strong>{{ tiers[user.tier].name if user.tier in tiers else user.tier }}</strong>
plan is active{% if user.expires_at %} until {{ user.expires_at[:10] }}{% endif %}.
<a href="/dashboard">Go to dashboard</a></div>
{% elif user.status == 'expired' %}
<div class="err">Your plan has expired. Renew below to regain access.</div>
{% else %}
<h1 style="margin-bottom:6px" data-edit-id="h1">Choose a plan</h1>
<p class="sub" data-edit-id="sub">Pay by card or crypto. Access unlocks as soon as it confirms.</p>
{% endif %}
{% if error %}<div class="err">{{ error }}</div>{% endif %}
<div class="ptoggle">
<button type="button" class="active" data-set-period="month">Monthly</button>
<button type="button" data-set-period="year">Annual <span class="save">Save {{ annual_save_pct }}%</span></button>
</div>
<p class="permo" style="margin:2px 0 10px">Card plans renew automatically and can be cancelled any time from your account. Crypto payments are one-off — you re-pay when the plan runs out.</p>
<div class="tiers">
{% for key in order %}{% set t = tiers[key] %}{% set ta = tiers[key + annual_suffix] %}
<div class="tier {{ 'featured' if key == 'pro' else '' }}">
<h2>{{ t.name }}</h2><div class="blurb">{{ t.blurb }}</div>
<div data-period="month">
<div class="price">{{ currency }}{{ t.price }}<span>/month</span></div>
<ul>{% for f in t.features %}<li>{{ f }}</li>{% endfor %}</ul>
{% set verb = 'Renew' if user.status == 'expired' else 'Pay' %}{% set card_verb = 'Resubscribe' if user.status == 'expired' else 'Subscribe' %}
{% if stripe_enabled %}<button data-tier="{{ key }}" data-provider="stripe">{{ card_verb }} with card</button>{% endif %}
{% if oxapay_enabled %}<button data-tier="{{ key }}" data-provider="oxapay"
 style="{{ 'margin-top:8px;' if stripe_enabled }}background:var(--surface);color:var(--text)">{{ verb }} with crypto</button>{% endif %}
{% if not stripe_enabled and not oxapay_enabled %}<button disabled>Payments not configured</button>{% endif %}
{% if dev_mode %}<button class="dev" data-dev-tier="{{ key }}"
 style="background:var(--surface);color:var(--star);margin-top:8px">Activate free (dev)</button>{% endif %}
</div>
<div data-period="year" hidden>
<div class="price">{{ currency }}{{ ta.price }}<span>/year</span></div>
<div class="permo">{{ currency }}{{ ta.monthly_equiv }}/mo billed annually</div>
<ul>{% for f in t.features %}<li>{{ f }}</li>{% endfor %}</ul>
{% set verb = 'Renew' if user.status == 'expired' else 'Pay' %}{% set card_verb = 'Resubscribe' if user.status == 'expired' else 'Subscribe' %}
{% if stripe_enabled %}<button data-tier="{{ key }}{{ annual_suffix }}" data-provider="stripe">{{ card_verb }} with card</button>{% endif %}
{% if oxapay_enabled %}<button data-tier="{{ key }}{{ annual_suffix }}" data-provider="oxapay"
 style="{{ 'margin-top:8px;' if stripe_enabled }}background:var(--surface);color:var(--text)">{{ verb }} with crypto</button>{% endif %}
{% if not stripe_enabled and not oxapay_enabled %}<button disabled>Payments not configured</button>{% endif %}
{% if dev_mode %}<button class="dev" data-dev-tier="{{ key }}{{ annual_suffix }}"
 style="background:var(--surface);color:var(--star);margin-top:8px">Activate free (dev)</button>{% endif %}
</div>
</div>{% endfor %}
<div class="tier{{ ' soon' if custom.coming_soon }}">
<h2>{{ custom.name }}</h2><div class="blurb">{{ custom.blurb }}</div>
<div class="price">{{ custom.price_label }}</div>
<div class="permo">{{ custom.note }}</div>
<ul>{% for f in custom.features %}<li>{{ f }}</li>{% endfor %}</ul>
<button disabled>{{ custom.cta }}</button>
</div>
</div>
{% if dev_mode %}<p style="text-align:center;color:#fbbf24;font-size:.8rem;margin-top:18px">
Dev mode: DEV_FAKE_PAYMENTS=1 is set, so plans can be activated without paying.
Unset it before going live.</p>{% endif %}
</div>
<script>
document.querySelectorAll('.ptoggle button').forEach(function(tab){
  tab.addEventListener('click', function(){
    var period = tab.dataset.setPeriod;
    document.querySelectorAll('.ptoggle button').forEach(function(t){
      t.classList.toggle('active', t === tab);
    });
    document.querySelectorAll('[data-period]').forEach(function(el){
      el.hidden = el.dataset.period !== period;
    });
  });
});
document.querySelectorAll('button[data-dev-tier]').forEach(function(b){
  b.addEventListener('click', async function(){
    b.disabled = true; b.textContent = 'Activating...';
    try {
      var r = await fetch('/api/billing/dev-activate', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({tier: b.dataset.devTier})});
      var d = await r.json();
      if (d.ok) { window.location = '/dashboard'; return; }
      alert(d.error || 'Could not activate.');
    } catch (e) { alert('Could not activate.'); }
    b.disabled = false; b.textContent = 'Activate free (dev)';
  });
});
document.querySelectorAll('button[data-tier]').forEach(function(b){
  b.addEventListener('click', async function(){
    b.disabled = true; var old = b.textContent; b.textContent = 'Redirecting...';
    try {
      var r = await fetch('/api/billing/checkout', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({tier: b.dataset.tier, provider: b.dataset.provider})});
      var d = await r.json();
      if (d.payment_url) { window.location = d.payment_url; return; }
      if (d.redirect) { window.location = d.redirect; return; }
      alert(d.error || 'Could not start checkout.');
    } catch (e) { alert('Could not start checkout.'); }
    b.disabled = false; b.textContent = old;
  });
});
</script></body></html>"""


ACCOUNT_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><script src="/js/theme.js"></script>
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" href="/favicon.png"><title>My account</title>
<style>""" + ACCOUNT_CSS + """
table{width:100%;border-collapse:collapse;margin-top:8px;font-size:.85rem}
th{text-align:left;color:var(--text-muted);font-weight:500;padding:6px 0;border-bottom:1px solid var(--border)}
td{padding:8px 0;border-bottom:1px solid var(--border);color:var(--text-2)}
.row{display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid var(--border);font-size:.9rem}
.row span:first-child{color:var(--text-muted)}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.75rem;font-weight:600}
.pill.active{background:#14321f;color:#86efac}.pill.unpaid,.pill.expired{background:#3f1515;color:#fca5a5}
:root[data-theme="light"] .pill.active{background:#e8f6ed;color:#0f7038}
:root[data-theme="light"] .pill.unpaid,:root[data-theme="light"] .pill.expired{background:#fdecec;color:#9b1c1c}
.btn{display:block;text-align:center;background:var(--grad);background-size:300% 100%;color:#fff;border-radius:10px;padding:12px;margin-top:18px;text-decoration:none;font-weight:600;font-size:.9rem}
</style></head><body><div class="wrap">
<div class="bar"><span>My account</span><a href="/logout">Sign out</a></div>
<div class="card">
<h1>{{ user.name or user.email }}</h1><p class="sub">{{ user.email }}</p>
<div class="row"><span>Plan</span><span>{{ tiers[user.tier].name if user.tier in tiers else '—' }}</span></div>
<div class="row"><span>Status</span><span class="pill {{ user.status }}">{{ user.status }}</span></div>
<div class="row"><span>{{ 'Renews' if user.status == 'active' else 'Expired' }}</span>
<span>{{ user.expires_at[:10] if user.expires_at else '—' }}</span></div>
<div class="row"><span>Billing</span><span>
{% if user.stripe_subscription_id %}Card · renews automatically
{% elif user.stripe_customer_id %}Card · cancelled, access runs to the date above
{% elif user.status == 'active' %}One-off payment · no automatic renewal
{% else %}—{% endif %}</span></div>
{% if user.is_admin %}<a class="btn" style="background:#2e1065;color:#c4b5fd" href="/admin/users">Admin · manage users</a>{% endif %}
<a class="btn" style="background:var(--surface);color:var(--text)" href="/account/profile">Edit profile</a>
{% if seats_cap != 1 and user.seat_role == 'owner' %}<a class="btn" style="background:var(--surface);color:var(--text)" href="/team">Team &middot; {{ seats_used }} of {{ seats_cap }} seats</a>{% endif %}
{% if user.status == 'active' %}<a class="btn" href="/dashboard">Go to dashboard</a>
<a class="btn" style="background:var(--surface);color:var(--text)" href="/billing">Change plan</a>
{% else %}<a class="btn" href="/billing">Choose a plan</a>{% endif %}
{% if user.stripe_customer_id %}
<a class="btn" style="background:var(--surface);color:var(--text)" href="#" id="portal">Manage billing · card, invoices, cancel</a>
<div class="err" id="portal-err" style="display:none"></div>{% endif %}
</div>
{% if user.stripe_customer_id %}<script>
document.getElementById('portal').addEventListener('click', async function(e){
  e.preventDefault();
  var err = document.getElementById('portal-err');
  err.style.display = 'none';
  this.textContent = 'Opening…';
  try {
    var r = await fetch('/api/billing/portal', {method: 'POST'});
    var d = await r.json();
    if (d.url) { location.href = d.url; return; }
    err.textContent = d.error || 'Could not open the billing portal.';
  } catch (_) {
    err.textContent = 'Could not open the billing portal.';
  }
  err.style.display = 'block';
  this.textContent = 'Manage billing · card, invoices, cancel';
});
</script>{% endif %}
{% if payments %}<div class="card" style="margin-top:16px">
<h1 style="font-size:1rem">Payment history</h1>
<table><tr><th>Date</th><th>Plan</th><th>Amount</th><th>Method</th><th>Status</th></tr>
{% for p in payments %}<tr><td>{{ p.date }}</td><td>{{ p.tier }}</td>
<td>€{{ p.amount }}</td><td>{{ p.provider }}</td><td>{{ p.status }}</td></tr>{% endfor %}</table>
</div>{% endif %}
</div></body></html>"""


PROFILE_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><script src="/js/theme.js"></script>
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" href="/favicon.png"><title>Your profile</title>
<style>""" + ACCOUNT_CSS + """
textarea{width:100%;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:11px 14px;color:var(--text);font-size:.95rem;outline:none;margin-bottom:16px;font-family:inherit;resize:vertical;min-height:88px}
textarea:focus{border-color:#7c3aed}
.two{display:grid;grid-template-columns:1fr 1fr;gap:0 12px}
.ghost{display:block;text-align:center;margin-top:12px;color:#71717a;font-size:.85rem;text-decoration:none}
</style></head><body><div class="wrap">
<div class="bar"><a href="/account">← My account</a><a href="/logout">Sign out</a></div>
<div class="card">
<h1>{{ 'Welcome — tell us about you' if not user.onboarded else 'Your profile' }}</h1>
<p class="sub">{{ 'This shapes the defaults in your persona builder. You can change it any time.' if not user.onboarded else 'Update your details.' }}</p>
{% if saved %}<div class="ok">Profile saved.</div>{% endif %}
{% if error %}<div class="err">{{ error }}</div>{% endif %}
<form method="post">
<label>Your name</label><input type="text" name="name" value="{{ p.name }}" autocomplete="name">
<label>Brand / creator name</label><input type="text" name="brand" value="{{ p.brand }}" placeholder="The name fans know you by">
<div class="two"><div><label>Country</label><input type="text" name="country" value="{{ p.country }}"></div>
<div><label>Timezone</label><input type="text" name="timezone" value="{{ p.timezone }}" placeholder="Europe/Amsterdam"></div></div>
<div class="two"><div><label>Phone</label><input type="text" name="phone" value="{{ p.phone }}"></div>
<div><label>Website</label><input type="text" name="website" value="{{ p.website }}" placeholder="https://"></div></div>
<label>About you</label><textarea name="bio" placeholder="A few lines about your content and audience.">{{ p.bio }}</textarea>
<button type="submit">{{ 'Save and continue' if not user.onboarded else 'Save changes' }}</button>
</form>
{% if not user.onboarded %}<a class="ghost" href="/dashboard">Skip for now</a>{% endif %}
</div></div></body></html>"""


ADMIN_USERS_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><script src="/js/theme.js"></script>
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" href="/favicon.png"><title>Users</title>
<style>""" + ACCOUNT_CSS + """
table{width:100%;border-collapse:collapse;font-size:.85rem}
th{text-align:left;color:var(--text-muted);font-weight:500;padding:8px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:10px;border-bottom:1px solid var(--border);color:var(--text-2)}
tr:hover td{background:#1c1c20}
a.email{color:#a78bfa;text-decoration:none;font-weight:500}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:.72rem;font-weight:600}
.pill.active{background:#14321f;color:#86efac}
.pill.unpaid,.pill.expired{background:#3f1515;color:#fca5a5}
.pill.admin{background:#2e1065;color:#c4b5fd}
.pill.role{background:#1f2937;color:#cbd5e1}
.pill.role.admin{background:#2e1065;color:#c4b5fd}
.pill.role.support{background:#0c4a6e;color:#bae6fd}
.pill.role.manager{background:#1e3a2f;color:#a7f3d0}
.pill.role.chatter{background:#3b2f14;color:#fcd34d}
.pill.legacy{background:#3b2f14;color:#fcd34d}
.scroll{overflow-x:auto}
</style></head><body><div class="wrap wide" style="max-width:1100px">
<div class="bar"><span>Admin · {{ users|length }} user{{ '' if users|length == 1 else 's' }}</span>
<span><a href="/dashboard">Dashboard</a> &nbsp; <a href="/logout">Sign out</a></span></div>
<div class="card"><div class="scroll"><table>
<tr><th>Email</th><th>Name</th><th>Role</th><th>Team</th><th>Plan</th><th>Status</th><th>Renews</th><th>Joined</th></tr>
{% for u in users %}<tr>
<td><a class="email" href="/admin/users/{{ u.id }}">{{ u.email }}</a></td>
<td>{{ u.name or '—' }}</td>
<td><span class="pill role {{ u.role }}">{{ u.role }}</span></td>
<td>{% if u.team %}seat of {{ u.team }}{% elif u.seats %}{{ u.seats }} / {{ u.seat_cap }} seats{% else %}—{% endif %}</td>
<td>{{ u.tier or '—' }}{% if u.grandfathered %} <span class="pill legacy" title="No plan limits until {{ u.grandfathered }}">legacy</span>{% endif %}</td>
<td><span class="pill {{ u.status }}">{{ u.status }}</span></td>
<td>{{ u.expires or '—' }}</td><td>{{ u.created or '—' }}</td>
</tr>{% endfor %}
</table></div></div></div></body></html>"""

ADMIN_USER_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><script src="/js/theme.js"></script>
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" href="/favicon.png"><title>{{ u.email }}</title>
<style>""" + ACCOUNT_CSS + """
textarea{width:100%;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:11px 14px;color:var(--text);font-size:.95rem;outline:none;margin-bottom:16px;font-family:inherit;resize:vertical;min-height:80px}
select{width:100%;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:11px 14px;color:var(--text);font-size:.95rem;margin-bottom:16px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:0 12px}
h2{font-size:1rem;margin-bottom:14px}
.danger{background:#7f1d1d}.danger:hover{background:#991b1b}
</style></head><body><div class="wrap">
<div class="bar"><a href="/admin/users">← All users</a><a href="/logout">Sign out</a></div>
{% if saved %}<div class="ok">{{ saved }}</div>{% endif %}
{% if error %}<div class="err">{{ error }}</div>{% endif %}

<div class="card"><h2>Account</h2>
<form method="post" action="/admin/users/{{ u.id }}">
<input type="hidden" name="action" value="account">
<label>Email</label><input type="email" name="email" value="{{ u.email }}" required>
<div class="two">
<div><label>Role</label><select name="role">
{% for r in roles %}<option value="{{ r }}" {{ 'selected' if u.role == r }}>{{ r }}</option>{% endfor %}
</select></div>
<div><label>Status</label><select name="status">
{% for s in ['unpaid','active','expired'] %}<option value="{{ s }}" {{ 'selected' if u.status == s }}>{{ s }}</option>{% endfor %}
</select></div></div>
<div class="two">
<div><label>Plan</label><select name="tier">
<option value="">— none —</option>
{% for k in order %}<option value="{{ k }}" {{ 'selected' if u.tier == k }}>{{ tiers[k].name }}</option>{% endfor %}
</select></div>
<div><label>Renews (YYYY-MM-DD)</label><input type="text" name="expires" value="{{ u.expires }}" placeholder="blank = none"></div>
</div>
<label>Legacy access until (YYYY-MM-DD)</label><input type="text" name="grandfathered" value="{{ u.grandfathered }}" placeholder="blank = plan limits apply now">
<p class="sub" style="margin:-8px 0 16px">While this date is in the future the account ignores its plan's limits — no persona, phase, platform or image caps. Set automatically for everyone who was already paying when limits were introduced; extend it here to give someone more time.</p>
<label>Team owner (email)</label><input type="email" name="team_owner" value="{{ u.team_owner }}" placeholder="blank = owns their own workspace">
<p class="sub" style="margin:-8px 0 16px">Set this to make the account a seat inside that owner's workspace: it shares their personas and their plan, and only the owner is billed. Manager and chatter only mean anything on a seat.</p>
<button type="submit">Save account</button></form></div>

<div class="card" style="margin-top:16px"><h2>Profile</h2>
<form method="post" action="/admin/users/{{ u.id }}">
<input type="hidden" name="action" value="profile">
<label>Name</label><input type="text" name="name" value="{{ p.name }}">
<label>Brand</label><input type="text" name="brand" value="{{ p.brand }}">
<div class="two"><div><label>Country</label><input type="text" name="country" value="{{ p.country }}"></div>
<div><label>Timezone</label><input type="text" name="timezone" value="{{ p.timezone }}"></div></div>
<div class="two"><div><label>Phone</label><input type="text" name="phone" value="{{ p.phone }}"></div>
<div><label>Website</label><input type="text" name="website" value="{{ p.website }}"></div></div>
<label>Bio</label><textarea name="bio">{{ p.bio }}</textarea>
<button type="submit">Save profile</button></form></div>

<div class="card" style="margin-top:16px"><h2>Set password</h2>
<p class="sub">Replaces the password immediately. Tell them out of band.</p>
<form method="post" action="/admin/users/{{ u.id }}">
<input type="hidden" name="action" value="password">
<label>New password</label><input type="password" name="password" required placeholder="At least 8 characters">
<button type="submit" class="danger">Set password</button></form></div>

</div></body></html>"""


def _require_admin():
    """None when the caller is an admin, else the response to send instead."""
    user = _current_user()
    if not user:
        if (request.path or '').startswith('/api/'):
            return jsonify({'error': 'Sign in required'}), 401
        return redirect('/login?next=' + urllib.parse.quote(request.path or '/'))
    if not user.get('is_admin'):
        logger.warning('ADMIN DENIED user=%s path=%s', user['email'], request.path)
        # 404 rather than 403, so the admin surface isn't discoverable.
        if (request.path or '').startswith('/api/'):
            return jsonify({'error': 'Not found'}), 404
        return ('Not found', 404)
    return None


ADMIN_ROLES = ('user', 'manager', 'chatter', 'support', 'admin')


def _apply_team_owner(s, u, owner_email):
    """Add or remove a seat in another account's workspace, from the console.

    Only the memberships this grants are touched; a user's own workspace is
    never removed, so revoking a seat cannot orphan them."""
    from db import (get_user_by_email, Workspace, Membership, get_membership,
                    count_workspace_members)
    guest = [m for m in s.query(Membership).filter(Membership.user_id == u.id).all()
             if m.role != 'owner']
    if not owner_email:
        for m in guest:
            s.delete(m)
        return ''
    owner = get_user_by_email(s, owner_email)
    if owner is None:
        return 'No account with that owner email.'
    if owner.id == u.id:
        return 'An account cannot be a seat in its own workspace.'
    ws = s.query(Workspace).filter(Workspace.owner_id == owner.id).order_by(
        Workspace.created_at.asc()).first()
    if ws is None:
        return f'{owner.email} has no workspace yet.'
    if get_membership(s, ws.id, u.id) is None:
        cap = tier_capabilities(owner.tier).get('seats')
        if cap is not None and count_workspace_members(s, ws.id) >= int(cap):
            return f'{owner.email} is using all {cap} seats on their plan.'
        s.add(Membership(workspace_id=ws.id, user_id=u.id,
                         role=(u.role if u.role in ('manager', 'chatter') else 'chatter')))
    for m in guest:
        if m.workspace_id != ws.id:
            s.delete(m)
    return ''


def _fmt_date(dt):
    return dt.strftime('%Y-%m-%d') if dt else ''


@app.route('/admin/users')
def admin_users():
    blocked = _require_admin()
    if blocked:
        return blocked
    from db import list_users
    s = _db_session()
    try:
        from db import Workspace, Membership
        users = list_users(s)
        by_id = {u.id: u for u in users}
        ws_owner = {w.id: w.owner_id for w in s.query(Workspace).all()}
        seats, guest_of = {}, {}
        for m in s.query(Membership).all():
            seats[m.workspace_id] = seats.get(m.workspace_id, 0) + 1
            if m.role != 'owner':
                guest_of.setdefault(m.user_id, []).append(m.workspace_id)
        rows = []
        for u in users:
            guests = [by_id.get(ws_owner.get(w)) for w in guest_of.get(u.id, [])]
            owner = next((g for g in guests if g is not None), None)
            n = seats.get(u.id, 0) - 1
            rows.append({
                'id': u.id, 'email': u.email, 'name': u.name,
                'role': u.role or 'user', 'tier': u.tier, 'status': u.status,
                'team': (owner.email if owner else ''),
                'seats': (n + 1) if n > 0 else 0,
                'seat_cap': tier_capabilities(u.tier).get('seats') or 1,
                'grandfathered': _fmt_date(u.grandfathered_until),
                'expires': _fmt_date(u.expires_at),
                'created': _fmt_date(u.created_at)})
    finally:
        s.close()
    return render_template_string(ADMIN_USERS_HTML, users=rows)


@app.route('/admin/users/<uid>', methods=['GET', 'POST'])
def admin_user_detail(uid):
    blocked = _require_admin()
    if blocked:
        return blocked
    from werkzeug.security import generate_password_hash
    from db import User, get_user_by_email
    me = _current_user()
    pfields = ('name', 'brand', 'country', 'timezone', 'phone', 'website', 'bio')
    s = _db_session()
    try:
        u = s.get(User, uid)
        if u is None:
            return ('Not found', 404)
        saved = error = ''
        if request.method == 'POST':
            action = request.form.get('action', '')
            if action == 'profile':
                for f in pfields:
                    setattr(u, f, (request.form.get(f) or '').strip()[:500])
                s.commit()
                saved = 'Profile updated.'
                logger.info('ADMIN EDIT profile by=%s target=%s', me['email'], u.email)

            elif action == 'password':
                pw = request.form.get('password') or ''
                if len(pw) < 8:
                    error = 'Password must be at least 8 characters.'
                else:
                    u.password_hash = generate_password_hash(pw)
                    s.commit()
                    saved = 'Password changed.'
                    logger.warning('ADMIN PASSWORD RESET by=%s target=%s',
                                   me['email'], u.email)

            elif action == 'account':
                email = (request.form.get('email') or '').strip().lower()
                role = request.form.get('role', 'user')
                clash = get_user_by_email(s, email) if email != u.email else None
                if not email or '@' not in email:
                    error = 'Enter a valid email.'
                elif clash:
                    error = 'Another account already uses that email.'
                elif u.id == me['id'] and role != 'admin':
                    # Otherwise an admin can lock themselves out of this page.
                    error = 'You cannot remove your own admin role.'
                elif role not in ADMIN_ROLES:
                    error = 'Unknown role.'
                else:
                    error = _apply_team_owner(
                        s, u, (request.form.get('team_owner') or '').strip().lower())
                if not error:
                    u.email = email
                    u.role = role
                    u.status = request.form.get('status', u.status)
                    u.tier = request.form.get('tier', '') or ''
                    raw = (request.form.get('expires') or '').strip()
                    gf = (request.form.get('grandfathered') or '').strip()
                    try:
                        u.expires_at = datetime.strptime(raw, '%Y-%m-%d') if raw else None
                        u.grandfathered_until = (datetime.strptime(gf, '%Y-%m-%d')
                                                 if gf else None)
                    except ValueError:
                        error = 'Dates must look like 2026-12-31.'
                    if not error:
                        s.commit()
                        saved = 'Account updated.'
                        logger.info('ADMIN EDIT account by=%s target=%s role=%s '
                                    'status=%s tier=%s team=%s', me['email'],
                                    u.email, u.role, u.status, u.tier,
                                    request.form.get('team_owner') or '-')
            if error:
                s.rollback()

        from db import Workspace, Membership
        guest = s.query(Membership).filter(Membership.user_id == u.id,
                                           Membership.role != 'owner').first()
        gws = s.get(Workspace, guest.workspace_id) if guest else None
        owner = s.get(User, gws.owner_id) if gws else None
        view = {'id': u.id, 'email': u.email, 'role': u.role or 'user',
                'team_owner': owner.email if owner else '',
                'grandfathered': _fmt_date(u.grandfathered_until),
                'status': u.status, 'tier': u.tier, 'expires': _fmt_date(u.expires_at)}
        p = {f: (getattr(u, f) or '') for f in pfields}
    finally:
        s.close()
    return render_template_string(ADMIN_USER_HTML, u=view, p=p, saved=saved,
                                  error=error, tiers=TIERS, order=DEFAULT_TIER_ORDER,
                                  roles=ADMIN_ROLES)


SEAT_ROLES = ('manager', 'chatter')
INVITE_DAYS = 14

TEAM_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><script src="/js/theme.js"></script>
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" href="/favicon.png"><title>Team</title>
<style>""" + ACCOUNT_CSS + """
table{width:100%;border-collapse:collapse;font-size:.88rem}
th{text-align:left;color:var(--text-muted);font-weight:500;padding:8px 10px;border-bottom:1px solid var(--border)}
td{padding:10px;border-bottom:1px solid var(--border);color:var(--text-2);vertical-align:middle}
select{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:7px 10px;color:var(--text);font-size:.85rem}
.row-actions{display:flex;gap:8px;align-items:center}
.row-actions button{padding:7px 12px;font-size:.82rem;width:auto}
.danger{background:#7f1d1d}.danger:hover{background:#991b1b}
.two{display:grid;grid-template-columns:1fr auto auto;gap:0 10px;align-items:end}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:.72rem;font-weight:600;background:#1f2937;color:#cbd5e1}
.pill.owner{background:#2e1065;color:#c4b5fd}
.link{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:9px 11px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.78rem;word-break:break-all;margin:6px 0 10px}
h2{font-size:1rem;margin-bottom:6px}
</style></head><body><div class="wrap" style="max-width:780px">
<div class="bar"><a href="/account">\u2190 My account</a><a href="/dashboard">Dashboard</a></div>
{% if saved %}<div class="ok">{{ saved }}</div>{% endif %}
{% if error %}<div class="err">{{ error }}</div>{% endif %}

<div class="card"><h2>Team</h2>
<p class="sub">{{ used }} of {{ cap }} seat{{ '' if cap == 1 else 's' }} on {{ tier_name }}.
Everyone here shares this workspace's personas, and only you are billed. They keep their own
workspace too, and switch between them.</p>
<table>
<tr><th>Member</th><th>Role</th><th></th></tr>
{% for m in members %}<tr>
<td>{{ m.email }}{% if m.name %}<br><span class="sub">{{ m.name }}</span>{% endif %}</td>
{% if m.is_owner %}
<td colspan="2"><span class="pill owner">owner \u00b7 you</span></td>
{% else %}
<td colspan="2"><form method="post" class="row-actions">
<input type="hidden" name="action" value="role"><input type="hidden" name="uid" value="{{ m.id }}">
<select name="role">{% for r in seat_roles %}<option value="{{ r }}" {{ 'selected' if m.role == r }}>{{ r }}</option>{% endfor %}</select>
<button type="submit">Save</button>
<button type="submit" name="action" value="remove" class="danger"
  onclick="return confirm('Remove {{ m.email }} from your team?')">Remove</button>
</form></td>
{% endif %}
</tr>{% endfor %}
</table>
<p class="sub" style="margin-top:14px"><strong>Manager</strong> can do everything except billing and
managing seats. <strong>Chatter</strong> can reply to fans but cannot edit personas, connect
platforms or generate images.</p>
</div>

{% if invites %}<div class="card" style="margin-top:16px"><h2>Pending invites</h2>
{% for i in invites %}
<p class="sub" style="margin-bottom:2px">{{ i.email or 'anyone with the link' }} \u00b7 {{ i.role }} \u00b7 expires {{ i.expires }}</p>
<div class="link">{{ i.url }}</div>
<form method="post" style="margin-bottom:18px"><input type="hidden" name="action" value="revoke">
<input type="hidden" name="token" value="{{ i.token }}">
<button type="submit" class="danger" style="width:auto;padding:7px 12px;font-size:.82rem">Revoke</button></form>
{% endfor %}
</div>{% endif %}

<div class="card" style="margin-top:16px"><h2>Invite someone</h2>
{% if seats_left > 0 %}
<p class="sub">We do not send the email for you \u2014 you get a link to pass on. It works once and
expires after {{ invite_days }} days.</p>
<form method="post"><input type="hidden" name="action" value="invite">
<div class="two">
<div><label>Their email (optional, but it locks the link to them)</label>
<input type="email" name="email" placeholder="them@example.com"></div>
<div><label>Role</label><select name="role">{% for r in seat_roles %}<option value="{{ r }}">{{ r }}</option>{% endfor %}</select></div>
<div><button type="submit" style="width:auto;padding:11px 18px">Create link</button></div>
</div></form>
{% else %}
<p class="sub">{{ 'Your plan has a single seat' if cap == 1 else 'All ' ~ cap ~ ' seats on your plan are in use' }}. Remove someone,
or <a href="/pricing">move to a bigger plan</a>.</p>
{% endif %}
</div>
</div></body></html>"""

JOIN_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><script src="/js/theme.js"></script>
<link rel="icon" href="/favicon.ico" sizes="any"><title>Join a team</title>
<style>""" + ACCOUNT_CSS + """h2{font-size:1.05rem;margin-bottom:8px}</style>
</head><body><div class="wrap"><div class="card">
{% if error %}<h2>This invite cannot be used</h2><div class="err">{{ error }}</div>
<a class="ghost" href="/dashboard">Go to your dashboard</a>
{% else %}<h2>Join {{ owner }}\u2019s team</h2>
<p class="sub">You will join as <strong>{{ role }}</strong> and share their personas. You will not be
billed \u2014 the workspace owner pays for the plan. Your own workspace, plan and personas stay
exactly as they are; you switch between them from the sidebar.</p>
<form method="post"><button type="submit">Accept invite</button></form>
<a class="ghost" href="/dashboard">No thanks</a>{% endif %}
</div></div></body></html>"""


def _seat_view(s, user):
    """Members, pending invites and the seat count for the active workspace."""
    from db import list_workspace_members, count_workspace_members, list_invites
    wid = _workspace_id(user)
    members = [{'id': u.id, 'email': u.email, 'name': u.name or '',
                'role': m.role or 'chatter', 'is_owner': (m.role == 'owner')}
               for m, u in list_workspace_members(s, wid)]
    invites = [{'token': i.token, 'email': i.email or '', 'role': i.role,
                'expires': _fmt_date(i.expires_at),
                'url': request.url_root.rstrip('/') + '/join/' + i.token}
               for i in list_invites(s, wid)
               if i.expires_at and i.expires_at > datetime.now(timezone.utc).replace(tzinfo=None)]
    cap = user_capabilities(user).get('seats')
    return members, invites, cap, count_workspace_members(s, wid)


@app.route('/team', methods=['GET', 'POST'])
def team():
    user = _current_user()
    if not user:
        return redirect('/login?next=/team')
    # Seats belong to whoever pays for them, so only the owner manages them.
    if user.get('seat_role') != 'owner' and not user.get('is_admin'):
        return ('Not found', 404)
    from db import (User, Invite, Membership, count_pending_invites,
                    count_workspace_members, get_membership)
    s = _db_session()
    try:
        wid = _workspace_id(user)
        saved = error = ''
        if request.method == 'POST':
            action = request.form.get('action', '')
            cap = user_capabilities(user).get('seats')

            if action == 'invite':
                pending = count_pending_invites(s, wid)
                if cap is not None and count_workspace_members(s, wid) + pending >= int(cap):
                    error = ('Every seat on your plan is taken or already invited. '
                             'Revoke an invite or remove a member first.')
                else:
                    role = request.form.get('role', 'chatter')
                    inv = Invite(
                        token=secrets.token_urlsafe(24),
                        workspace_id=wid,
                        email=(request.form.get('email') or '').strip().lower()[:255],
                        role=role if role in SEAT_ROLES else 'chatter',
                        created_by=user['id'],
                        expires_at=(datetime.now(timezone.utc).replace(tzinfo=None)
                                    + timedelta(days=INVITE_DAYS)))
                    s.add(inv)
                    s.commit()
                    saved = 'Invite link created. Copy it below and send it to them.'
                    logger.info('SEAT INVITE by=%s workspace=%s role=%s',
                                user['email'], wid, inv.role)

            elif action == 'revoke':
                inv = s.get(Invite, request.form.get('token', ''))
                if inv is not None and inv.workspace_id == wid and not inv.accepted_at:
                    s.delete(inv)
                    s.commit()
                    saved = 'Invite revoked.'

            elif action in ('role', 'remove'):
                target = s.get(User, request.form.get('uid', ''))
                mem = get_membership(s, wid, target.id) if target else None
                if mem is None:
                    error = 'That person is not on your team.'
                elif mem.role == 'owner':
                    error = 'The owner cannot be removed or demoted.'
                elif action == 'remove':
                    s.delete(mem)
                    s.commit()
                    saved = f'{target.email} removed from this workspace.'
                    logger.info('SEAT REMOVED by=%s target=%s workspace=%s',
                                user['email'], target.email, wid)
                else:
                    role = request.form.get('role', '')
                    if role not in SEAT_ROLES:
                        error = 'Pick a valid role.'
                    else:
                        mem.role = role
                        s.commit()
                        saved = f'{target.email} is now a {role}.'

        members, invites, cap, used = _seat_view(s, user)
    finally:
        s.close()
    tier = TIERS.get(user.get('tier') or '') or {}
    return render_template_string(
        TEAM_HTML, members=members, invites=invites, saved=saved, error=error,
        cap=('unlimited' if cap is None else cap), used=used,
        seats_left=(1 if cap is None else int(cap) - used),
        seat_roles=SEAT_ROLES, invite_days=INVITE_DAYS,
        tier_name=tier.get('name') or 'your plan')


def _invite_problem(s, inv, user):
    """Why this invite cannot be accepted by this user, or '' if it can.

    Joining no longer costs the invitee anything — their own workspace, plan
    and personas stay put and they switch between them — so the old refusals
    for "you already have a plan" and "you already own personas" are gone.
    """
    from db import User
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if inv is None or inv.accepted_at:
        return 'This invite has already been used.'
    if not inv.expires_at or inv.expires_at <= now:
        return 'This invite has expired. Ask for a new one.'
    if inv.email and inv.email != (user.get('email') or '').lower():
        return f'This invite was issued to {inv.email}. Sign in as that account to accept it.'
    from db import Workspace, get_membership, count_workspace_members
    ws = s.get(Workspace, inv.workspace_id)
    if ws is None:
        return 'That workspace no longer exists.'
    if get_membership(s, ws.id, user['id']) is not None:
        return 'You are already in this workspace.'
    owner = s.get(User, ws.owner_id)
    if owner is None:
        return 'That workspace no longer exists.'
    cap = tier_capabilities(owner.tier).get('seats')
    if cap is not None and count_workspace_members(s, ws.id) >= int(cap):
        return 'That team has no seats left. Ask the owner to free one up.'
    return ''


@app.route('/api/workspaces')
def api_workspaces():
    """Every workspace the caller can open, and which one is in view."""
    user = _current_user()
    if not user:
        return jsonify({'error': 'Sign in required'}), 401
    from db import list_memberships, User
    s = _db_session()
    try:
        out = []
        for m, ws in list_memberships(s, user['id']):
            owner = s.get(User, ws.owner_id)
            out.append({'id': ws.id,
                        'name': ws.name or (owner.email if owner else ws.id),
                        'role': m.role or 'chatter',
                        'plan': (owner.tier or '') if owner else '',
                        'active': ws.id == user['workspace_id']})
    finally:
        s.close()
    return jsonify({'workspaces': out, 'active': user['workspace_id']})


@app.route('/api/workspaces/switch', methods=['POST'])
def api_workspace_switch():
    """Put a different workspace in view. Membership is re-checked here rather
    than trusted from the session, which is why _active_workspace can fall back
    quietly instead of failing when a seat is later revoked."""
    user = _current_user()
    if not user:
        return jsonify({'error': 'Sign in required'}), 401
    wid = (request.json or {}).get('workspace_id', '')
    from db import get_membership
    s = _db_session()
    try:
        if get_membership(s, wid, user['id']) is None:
            return jsonify({'error': 'Not found'}), 404
    finally:
        s.close()
    session['workspace_id'] = wid
    return jsonify({'ok': True, 'workspace_id': wid})


@app.route('/join/<token>', methods=['GET', 'POST'])
def join_team(token):
    from db import User, Invite
    user = _current_user()
    if not user:
        # Survive the round trip through sign-up, which otherwise lands on billing.
        session['pending_invite'] = token
        return redirect('/register?invite=1')
    s = _db_session()
    try:
        inv = s.get(Invite, token)
        problem = _invite_problem(s, inv, user)
        if problem:
            return render_template_string(JOIN_HTML, error=problem)
        owner = s.get(User, inv.workspace_id)
        if request.method == 'GET':
            return render_template_string(JOIN_HTML, error='',
                                          owner=owner.name or owner.email,
                                          role=inv.role)
        from db import Membership
        me = s.get(User, user['id'])
        s.add(Membership(workspace_id=inv.workspace_id, user_id=me.id,
                         role=inv.role))
        # Land them in the workspace they just joined, not whichever they had open.
        session['workspace_id'] = inv.workspace_id
        inv.accepted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        inv.accepted_by = me.id
        s.commit()
        logger.info('SEAT ACCEPTED user=%s workspace=%s role=%s',
                    me.email, inv.workspace_id, inv.role)
    finally:
        s.close()
    return redirect('/dashboard')


@app.route('/account/profile', methods=['GET', 'POST'])
def account_profile():
    user = _current_user()
    if not user:
        return redirect('/login?next=/account/profile')
    from db import User
    fields = ('name', 'brand', 'country', 'timezone', 'phone', 'website', 'bio')
    s = _db_session()
    try:
        u = s.get(User, user['id'])
        if u is None:
            return redirect('/login')
        saved = False
        if request.method == 'POST':
            for f in fields:
                setattr(u, f, (request.form.get(f) or '').strip()[:500])
            first_time = u.onboarded_at is None
            if first_time:
                u.onboarded_at = datetime.now(timezone.utc).replace(tzinfo=None)
            s.commit()
            logger.info('PROFILE SAVED user=%s first_time=%s', u.email, first_time)
            if first_time:
                return redirect('/dashboard')
            saved = True
        p = {f: (getattr(u, f) or '') for f in fields}
        view = dict(user, onboarded=u.onboarded_at is not None)
    finally:
        s.close()
    return render_template_string(PROFILE_HTML, user=view, p=p, saved=saved)


@app.route('/api/me')
def api_me():
    """Who is signed in, for the header nav. Open by design: it reports nothing
    a signed-out caller could not already infer."""
    user = _current_user()
    if not user:
        return jsonify({'signed_in': False, 'is_operator': _is_operator(),
                        'capabilities': dict(DENIED_CAPS)}), 200
    caps = user_capabilities(user)
    used, limit = _image_quota(user)
    from db import count_team_members
    sdb = _db_session()
    try:
        seats = count_team_members(sdb, _workspace_id(user))
    finally:
        sdb.close()
    return jsonify({'signed_in': True, 'id': user['id'], 'email': user['email'],
                    'name': user.get('name', ''), 'tier': user.get('tier', ''),
                    'status': user.get('status'),
                    'is_admin': bool(user.get('is_admin')),
                    'is_operator': _is_operator(),
                    'seat_role': user.get('seat_role') or 'owner',
                    'workspace_id': user.get('workspace_id') or '',
                    'workspace_name': user.get('workspace_name') or '',
                    'capabilities': caps,
                    'grandfathered': _is_grandfathered(user),
                    'usage': {'personas': {'used': _persona_count(user),
                                           'limit': caps.get('personas')},
                              'seats': {'used': seats, 'limit': caps.get('seats')},
                              'images': {'used': used, 'limit': limit}},
                    'setup': _get_setup(user['id'])}), 200


def _get_setup(uid):
    """Guided-setup progress for every persona this creator owns."""
    from db import User
    s = _db_session()
    try:
        u = s.get(User, uid)
        return json.loads((u.setup_json if u else '') or '{}')
    except Exception:
        return {}
    finally:
        s.close()


@app.route('/api/me/setup', methods=['POST'])
def api_me_setup():
    """Record how far the creator got in the guided setup for one persona."""
    user = _current_user()
    if not user:
        return jsonify({'error': 'not signed in'}), 401
    data = request.get_json(silent=True) or {}
    slug = (data.get('slug') or '').strip()
    if not slug:
        return jsonify({'error': 'slug required'}), 400

    from db import User
    s = _db_session()
    try:
        u = s.get(User, user['id'])
        if not u:
            return jsonify({'error': 'not found'}), 404
        try:
            state = json.loads(u.setup_json or '{}')
        except Exception:
            state = {}
        entry = state.get(slug) or {}
        if 'done' in data:
            entry['done'] = bool(data['done'])
        if isinstance(data.get('seen'), list):
            # Steps only ever accumulate, so a stale client cannot un-see one.
            merged = set(entry.get('seen') or []) | {str(k) for k in data['seen']}
            entry['seen'] = sorted(merged)
        # Whether the creator has been shown the guided tour, and how it ended.
        # Once set it stays set — replaying is an explicit action, not a reset.
        if data.get('tour') in ('done', 'skipped'):
            entry['tour'] = data['tour']
        state[slug] = entry
        u.setup_json = json.dumps(state)
        s.commit()
        return jsonify({'ok': True, 'setup': state}), 200
    finally:
        s.close()


@app.route('/account')
def account():
    user = _current_user()
    if not user:
        return redirect('/login?next=/account')
    from db import Payment
    s = _db_session()
    try:
        rows = (s.query(Payment).filter(Payment.user_id == user['id'])
                .order_by(Payment.created_at.desc()).limit(25).all())
        payments = [{'date': p.created_at.strftime('%Y-%m-%d') if p.created_at else '',
                     'tier': (TIERS.get(p.tier) or {}).get('name', p.tier),
                     'amount': p.amount, 'status': p.status,
                     'provider': p.provider or 'oxapay'} for p in rows]
    finally:
        s.close()
    from db import count_workspace_members
    s = _db_session()
    try:
        seats_used = count_workspace_members(s, _workspace_id(user))
    finally:
        s.close()
    cap = user_capabilities(user).get('seats')
    return render_template_string(ACCOUNT_HTML, user=user, tiers=TIERS,
                                  payments=payments, seats_used=seats_used,
                                  seats_cap=('unlimited' if cap is None else cap))


# ── Sign in with Google (OAuth 2.0, credentials from Google Cloud Console) ───
# Set GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET to switch it on; the
# button stays hidden while they are unset, so the app runs without them.
GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://openidconnect.googleapis.com/v1/userinfo'


def _google_oauth_config():
    cid = (os.getenv('GOOGLE_OAUTH_CLIENT_ID') or '').strip()
    secret = (os.getenv('GOOGLE_OAUTH_CLIENT_SECRET') or '').strip()
    return (cid, secret) if (cid and secret) else (None, None)


def _google_redirect_uri():
    """Must match a redirect URI registered on the OAuth client exactly."""
    explicit = (os.getenv('GOOGLE_OAUTH_REDIRECT_URI') or '').strip()
    if explicit:
        return explicit
    return _callback_origin() + '/auth/google/callback'


@app.context_processor
def _inject_google_oauth():
    cid, _ = _google_oauth_config()
    nxt = request.args.get('next') or ''
    return {'google_enabled': bool(cid),
            'google_next': ('?next=' + urllib.parse.quote(nxt)) if nxt.startswith('/') else ''}


@app.route('/auth/google')
def auth_google():
    cid, _ = _google_oauth_config()
    if not cid:
        return render_template_string(
            SIGNIN_HTML, error='Google sign-in is not configured yet.'), 503
    state = secrets.token_urlsafe(24)
    session['google_oauth_state'] = state
    nxt = request.args.get('next') or ''
    session['google_oauth_next'] = nxt if nxt.startswith('/') else ''
    params = {'client_id': cid, 'redirect_uri': _google_redirect_uri(),
              'response_type': 'code', 'scope': 'openid email profile',
              'state': state, 'prompt': 'select_account'}
    return redirect(GOOGLE_AUTH_URL + '?' + urllib.parse.urlencode(params))


def _google_post(url, data):
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(data).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def _google_userinfo(access_token):
    req = urllib.request.Request(
        GOOGLE_USERINFO_URL, headers={'Authorization': 'Bearer ' + access_token})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


@app.route('/auth/google/callback')
def auth_google_callback():
    from db import create_user, get_user_by_email, get_user_by_google_sub
    cid, secret = _google_oauth_config()
    if not cid:
        return redirect('/login')
    state = session.pop('google_oauth_state', None)
    nxt = session.pop('google_oauth_next', '') or ''
    if not state or request.args.get('state') != state:
        return render_template_string(SIGNIN_HTML,
                                      error='Sign-in expired. Please try again.'), 400
    if request.args.get('error') or not request.args.get('code'):
        return render_template_string(SIGNIN_HTML, error='Google sign-in was cancelled.')
    try:
        tok = _google_post(GOOGLE_TOKEN_URL, {
            'code': request.args['code'], 'client_id': cid,
            'client_secret': secret, 'redirect_uri': _google_redirect_uri(),
            'grant_type': 'authorization_code'})
        info = _google_userinfo(tok['access_token'])
    except Exception as e:
        logger.warning('GOOGLE OAUTH failed: %s', e)
        return render_template_string(
            SIGNIN_HTML, error='Could not complete Google sign-in. Try again.'), 502
    sub = info.get('sub')
    email = (info.get('email') or '').strip().lower()
    if not sub or not email or not info.get('email_verified'):
        return render_template_string(
            SIGNIN_HTML, error='Google did not return a verified email address.'), 400
    s = _db_session()
    try:
        u = get_user_by_google_sub(s, sub) or get_user_by_email(s, email)
        if u is None:
            u = create_user(s, email, '', info.get('name') or '', google_sub=sub)
        # Links an existing password account to the Google account on first use.
        if not u.google_sub:
            u.google_sub = sub
        u.last_login = datetime.now(timezone.utc).replace(tzinfo=None)
        s.commit()
        session['user_id'] = u.id
        session.permanent = True
        active = u.status == 'active' or (u.role or 'user') == 'admin'
    finally:
        s.close()
    if nxt and active:
        return redirect(nxt)
    return _post_signin_redirect(active)


@app.route('/register', methods=['GET', 'POST'])
def register():
    from werkzeug.security import generate_password_hash
    from db import create_user, get_user_by_email
    if request.method == 'GET':
        if _current_user():
            return redirect('/billing')
        return render_template_string(REGISTER_HTML)
    email = (request.form.get('email') or '').strip().lower()
    password = request.form.get('password') or ''
    name = (request.form.get('name') or '').strip()
    if not email or '@' not in email:
        return render_template_string(REGISTER_HTML, error='Enter a valid email.',
                                      email=email, name=name)
    if len(password) < 8:
        return render_template_string(REGISTER_HTML,
                                      error='Password must be at least 8 characters.',
                                      email=email, name=name)
    s = _db_session()
    try:
        if get_user_by_email(s, email):
            return render_template_string(
                REGISTER_HTML, error='That email is already registered.',
                email=email, name=name)
        u = create_user(s, email, generate_password_hash(password), name)
        s.commit()
        session['user_id'] = u.id
        session.permanent = True
    finally:
        s.close()
    pending = session.pop('pending_invite', '')
    return redirect('/join/' + pending if pending else '/billing?signup=1')


def _post_signin_redirect(active, nxt=''):
    """Where a sign-in lands. A pending seat invite wins over the billing
    bounce: an invitee has no plan of their own and never will."""
    pending = session.pop('pending_invite', '')
    if pending:
        return redirect('/join/' + pending)
    return _post_signin_redirect(active, nxt)


@app.route('/login', methods=['GET', 'POST'])
def login():
    from werkzeug.security import check_password_hash
    from db import get_user_by_email
    nxt = request.args.get('next') or ''
    if request.method == 'GET':
        if _current_user():
            return redirect(nxt or '/billing')
        return render_template_string(SIGNIN_HTML)
    email = (request.form.get('email') or '').strip().lower()
    password = request.form.get('password') or ''
    s = _db_session()
    try:
        u = get_user_by_email(s, email)
        # A Google-only account has no password, so the generic error would send
        # the creator round in circles resetting one that never existed.
        if u and u.google_sub and not u.password_hash:
            return render_template_string(
                SIGNIN_HTML, email=email,
                error='That account uses Google. Use "Continue with Google".')
        if not u or not check_password_hash(u.password_hash, password):
            return render_template_string(SIGNIN_HTML, error='Wrong email or password.',
                                          email=email)
        u.last_login = datetime.now(timezone.utc).replace(tzinfo=None)
        s.commit()
        session['user_id'] = u.id
        # Permanent sessions last PERMANENT_SESSION_LIFETIME; otherwise the
        # cookie is dropped when the browser closes.
        session.permanent = bool(request.form.get('remember'))
        active = u.status == 'active' or (u.role or 'user') == 'admin'
    finally:
        s.close()
    if nxt.startswith('/') and active:
        return redirect(nxt)
    return _post_signin_redirect(active)


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('admin_authed', None)
    return redirect('/login')


@app.route('/api/pricing')
def api_pricing():
    """Public: tier cards for the homepage pricing section (and anywhere else
    that wants the same data without the full /pricing page)."""
    return jsonify({'order': DEFAULT_TIER_ORDER, 'tiers': TIERS,
                    'custom': CUSTOM_TIER})


@app.route('/pricing')
def pricing():
    user = _current_user() or {'email': '', 'status': 'unpaid', 'tier': '',
                               'expires_at': None}
    return render_template_string(BILLING_HTML, user=user, tiers=TIERS,
                                  order=DEFAULT_TIER_ORDER,
                                  dev_mode=_dev_payments_enabled(),
                                  oxapay_enabled=bool(_oxapay_key()),
                                  stripe_enabled=bool(_stripe_key()),
                                  currency=CURRENCY_SYMBOL,
                                  annual_suffix=ANNUAL_SUFFIX,
                                  annual_save_pct=ANNUAL_SAVE_PCT,
                                  custom=CUSTOM_TIER)


@app.route('/billing')
def billing():
    user = _current_user()
    if not user:
        return redirect('/login?next=/billing')
    return render_template_string(BILLING_HTML, user=user, tiers=TIERS,
                                  order=DEFAULT_TIER_ORDER,
                                  dev_mode=_dev_payments_enabled(),
                                  oxapay_enabled=bool(_oxapay_key()),
                                  stripe_enabled=bool(_stripe_key()),
                                  currency=CURRENCY_SYMBOL,
                                  annual_suffix=ANNUAL_SUFFIX,
                                  annual_save_pct=ANNUAL_SAVE_PCT,
                                  custom=CUSTOM_TIER)


@app.route('/billing/return')
def billing_return():
    user = _current_user()
    if not user:
        return redirect('/login')
    return redirect('/dashboard' if _user_is_active(user) else '/billing')


def _checkout_oxapay(user, tier_key, tier, order_id, base):
    body = json.dumps({
        'amount': tier['price'],
        'currency': CURRENCY,
        'lifetime': 60,
        'order_id': order_id,
        'email': user['email'],
        'description': f'{tier["name"]} plan — {tier["days"]} days',
        'callback_url': f'{base}/api/billing/webhook',
        'return_url': f'{base}/billing/return',
    }).encode()
    req = urllib.request.Request(
        OXAPAY_API, data=body,
        headers={'Content-Type': 'application/json',
                 'merchant_api_key': _oxapay_key()})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
    except Exception:
        error_logger.error('Oxapay invoice creation failed', exc_info=True)
        return None, None
    data = payload.get('data') or {}
    pay_url, track_id = data.get('payment_url'), data.get('track_id')
    if not pay_url:
        logger.error('Oxapay returned no payment_url: %s', str(payload)[:300])
        return None, None
    return pay_url, str(track_id or '')


def _stripe_price_id(tier_key):
    """A price id from the Stripe catalogue for this plan, if one is configured
    (STRIPE_PRICE_STARTER, STRIPE_PRICE_PRO_ANNUAL, ...). Optional: with none
    set, checkout sends an inline recurring price instead, which keeps the same
    code working against a sandbox and a live account without an id per tier."""
    return (os.getenv('STRIPE_PRICE_' + tier_key.upper()) or '').strip()


def _stripe_post(path, form, timeout=20):
    """POST to the Stripe API and return the parsed body, or None on failure."""
    req = urllib.request.Request(
        f'{STRIPE_API}{path}',
        data=urllib.parse.urlencode(form).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded',
                 'Authorization': 'Bearer ' + _stripe_key()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except url_error.HTTPError as e:
        error_logger.error('Stripe POST %s failed: %s', path, e.read()[:300])
    except Exception:
        error_logger.error('Stripe POST %s failed', path, exc_info=True)
    return None


def _checkout_stripe(user, tier_key, tier, order_id, base):
    """Stripe Checkout Session in subscription mode, so the plan renews itself.

    The tier's own `period` ('month'/'year') is Stripe's billing interval, and
    `days` stays the local grace window the webhook extends on each paid
    invoice — Oxapay cannot recur, so expires_at remains the single source of
    truth for access across both providers.
    """
    form = {
        'mode': 'subscription',
        'success_url': f'{base}/billing/return?session_id={{CHECKOUT_SESSION_ID}}',
        'cancel_url': f'{base}/billing',
        'client_reference_id': order_id,
        # No payment_method_types: Managed Payments rejects it and picks the
        # methods itself, which also gets EU customers iDEAL/SEPA for free.
        'line_items[0][quantity]': '1',
        'metadata[order_id]': order_id,
        'metadata[tier]': tier_key,
        'metadata[user_id]': user['id'],
        # Copied onto the subscription so renewal invoices, which carry none of
        # the session's metadata, can still be matched back to a plan.
        'subscription_data[metadata][order_id]': order_id,
        'subscription_data[metadata][tier]': tier_key,
        'subscription_data[metadata][user_id]': user['id'],
    }
    # Reuse the saved card and keep one Stripe customer per account; without
    # this Stripe makes a fresh customer per checkout and the billing portal
    # would only ever show the newest subscription.
    if user.get('stripe_customer_id'):
        form['customer'] = user['stripe_customer_id']
    else:
        form['customer_email'] = user['email']

    price_id = _stripe_price_id(tier_key)
    if price_id:
        form['line_items[0][price]'] = price_id
    else:
        form.update({
            'line_items[0][price_data][currency]': CURRENCY.lower(),
            'line_items[0][price_data][unit_amount]':
                str(int(round(tier['price'] * 100))),
            'line_items[0][price_data][recurring][interval]':
                tier.get('period', 'month'),
            'line_items[0][price_data][product_data][name]':
                f'{tier["name"]} plan',
            # Managed Payments requires a tax code on an inline price.
            'line_items[0][price_data][product_data][tax_code]': STRIPE_TAX_CODE,
        })

    payload = _stripe_post('/checkout/sessions', form)
    if not payload:
        return None, None
    pay_url, session_id = payload.get('url'), payload.get('id')
    if not pay_url:
        logger.error('Stripe returned no url: %s', str(payload)[:300])
        return None, None
    return pay_url, str(session_id or '')


_CHECKOUT_PROVIDERS = {'oxapay': (_checkout_oxapay, _oxapay_key),
                       'stripe': (_checkout_stripe, _stripe_key)}


@app.route('/api/billing/checkout', methods=['POST'])
def api_billing_checkout():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Sign in required'}), 401
    body = request.get_json(silent=True) or {}
    tier_key = body.get('tier', '')
    tier = TIERS.get(tier_key)
    if not tier:
        return jsonify({'error': 'Unknown plan'}), 400

    provider = (body.get('provider') or '').strip().lower()
    if provider not in _CHECKOUT_PROVIDERS:
        return jsonify({'error': 'Unknown payment method'}), 400
    checkout_fn, key_fn = _CHECKOUT_PROVIDERS[provider]
    if not key_fn():
        logger.error('Checkout attempted via %s with no key configured', provider)
        return jsonify({'error': 'Payments are not configured yet.'}), 503

    from db import Payment
    order_id = f'{user["id"]}-{secrets.token_hex(6)}'
    base = _callback_origin()
    pay_url, track_id = checkout_fn(user, tier_key, tier, order_id, base)
    if not pay_url:
        return jsonify({'error': 'Could not reach the payment provider.'}), 502

    s = _db_session()
    try:
        s.add(Payment(user_id=user['id'], tier=tier_key, provider=provider,
                      amount=str(tier['price']), currency=CURRENCY, order_id=order_id,
                      track_id=track_id, status='pending'))
        s.commit()
    finally:
        s.close()
    logger.info('CHECKOUT [%s] user=%s tier=%s order=%s track=%s',
                provider, user['email'], tier_key, order_id, track_id)
    return jsonify({'payment_url': pay_url, 'track_id': track_id})


@app.route('/api/billing/dev-activate', methods=['POST'])
def api_billing_dev_activate():
    """Activate a plan without payment. Requires DEV_FAKE_PAYMENTS=1."""
    if not _dev_payments_enabled():
        return jsonify({'error': 'Not available'}), 404
    user = _current_user()
    if not user:
        return jsonify({'error': 'Sign in required'}), 401
    tier_key = (request.get_json(silent=True) or {}).get('tier', '')
    tier = TIERS.get(tier_key)
    if not tier:
        return jsonify({'error': 'Unknown plan'}), 400

    from db import User, Payment
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    s = _db_session()
    try:
        u = s.get(User, user['id'])
        _activate_plan(s, u, tier_key)
        s.add(Payment(user_id=u.id, tier=tier_key, provider='dev',
                      amount=str(tier['price']),
                      currency=CURRENCY, order_id=f'dev-{secrets.token_hex(6)}',
                      status='dev', paid_at=now))
        expires = u.expires_at
        s.commit()
    finally:
        s.close()
    logger.warning('DEV ACTIVATION (no payment taken) user=%s tier=%s until=%s',
                   user['email'], tier_key, expires)
    return jsonify({'ok': True, 'tier': tier_key, 'expires_at': expires.isoformat()})


@app.route('/api/billing/webhook', methods=['POST'])
def api_billing_webhook():
    """Oxapay payment callback. Signed with HMAC-SHA512 over the raw body."""
    raw = request.get_data()
    key = _oxapay_key()
    sent = request.headers.get('HMAC', '')
    if not key:
        error_logger.error('Oxapay webhook received but no merchant key configured')
        return ('ok', 200)
    expected = hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()
    if not hmac.compare_digest(expected, sent):
        logger.warning('Oxapay webhook rejected: bad HMAC signature')
        return ('ok', 200)

    try:
        payload = json.loads(raw.decode() or '{}')
    except Exception:
        return ('ok', 200)
    order_id = str(payload.get('order_id') or '')
    status = str(payload.get('status') or '')
    logger.info('PAYMENT WEBHOOK order=%s status=%s track=%s type=%s',
                order_id, status, payload.get('track_id'), payload.get('type'))
    if not order_id:
        return ('ok', 200)

    from db import User, get_payment_by_order
    s = _db_session()
    try:
        pay = get_payment_by_order(s, order_id)
        if not pay:
            logger.warning('Oxapay webhook for unknown order %s', order_id)
            return ('ok', 200)
        pay.status = status
        if status.lower() == 'paid' and not pay.paid_at:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            pay.paid_at = now
            u = s.get(User, pay.user_id)
            if u:
                expires = _activate_plan(s, u, pay.tier)
                logger.info('PLAN ACTIVATED user=%s tier=%s until=%s order=%s',
                            u.email, pay.tier, expires, order_id)
        s.commit()
    finally:
        s.close()
    return ('ok', 200)


def _stripe_signature_ok(raw, sig_header, secret, tolerance=300):
    """Verifies Stripe's `Stripe-Signature` header: t=<timestamp>,v1=<hmac>.
    See https://stripe.com/docs/webhooks#verify-manually."""
    parts = dict(p.split('=', 1) for p in sig_header.split(',') if '=' in p)
    t, v1 = parts.get('t'), parts.get('v1')
    if not t or not v1:
        return False
    try:
        if abs(time.time() - int(t)) > tolerance:
            return False
    except ValueError:
        return False
    expected = hmac.new(secret.encode(), f'{t}.'.encode() + raw,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


@app.route('/api/billing/webhook/stripe', methods=['POST'])
def api_billing_webhook_stripe():
    """Stripe payment callback. Signed per Stripe's own scheme (not Oxapay's),
    so it gets a separate endpoint and secret.

    Three events matter, and they are deliberately not interchangeable:
      checkout.session.completed          — first payment, links the subscription
      invoice.paid (subscription_cycle)   — a renewal, extends the plan
      customer.subscription.deleted       — cancelled; the paid period still runs
    """
    raw = request.get_data()
    secret = _stripe_webhook_secret()
    sig = request.headers.get('Stripe-Signature', '')
    if not secret:
        error_logger.error('Stripe webhook received but no webhook secret configured')
        return ('ok', 200)
    if not _stripe_signature_ok(raw, sig, secret):
        logger.warning('Stripe webhook rejected: bad signature')
        return ('ok', 200)

    try:
        event = json.loads(raw.decode() or '{}')
    except Exception:
        return ('ok', 200)
    event_type = event.get('type') or ''
    obj = (event.get('data') or {}).get('object') or {}

    if event_type in ('checkout.session.completed',
                      'checkout.session.async_payment_succeeded'):
        return _stripe_checkout_completed(obj)
    if event_type == 'invoice.paid':
        return _stripe_invoice_paid(obj)
    if event_type == 'customer.subscription.deleted':
        return _stripe_subscription_deleted(obj)
    logger.info('STRIPE WEBHOOK ignored type=%s', event_type)
    return ('ok', 200)


def _stripe_sub_id(obj):
    """The subscription id off an invoice or session, which Stripe sends either
    as a bare id or as an expanded object depending on the event."""
    sub = obj.get('subscription')
    if isinstance(sub, dict):
        return str(sub.get('id') or '')
    return str(sub or '')


def _stripe_checkout_completed(obj):
    """First payment. Activates the plan and records the Stripe ids so renewals
    and the billing portal can find this account again."""
    order_id = str((obj.get('metadata') or {}).get('order_id') or
                   obj.get('client_reference_id') or '')
    logger.info('STRIPE WEBHOOK checkout.completed order=%s payment_status=%s',
                order_id, obj.get('payment_status'))
    if not order_id:
        return ('ok', 200)

    from db import User, get_payment_by_order
    s = _db_session()
    try:
        pay = get_payment_by_order(s, order_id)
        if not pay:
            logger.warning('Stripe webhook for unknown order %s', order_id)
            return ('ok', 200)
        # Subscription checkouts report 'paid'; 'no_payment_required' covers a
        # 100%-off coupon, which is still a live subscription.
        paid = obj.get('payment_status') in ('paid', 'no_payment_required')
        pay.status = 'paid' if paid else (obj.get('payment_status') or pay.status)
        if paid and not pay.paid_at:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            pay.paid_at = now
            u = s.get(User, pay.user_id)
            if u:
                customer = obj.get('customer')
                if isinstance(customer, dict):
                    customer = customer.get('id')
                if customer:
                    u.stripe_customer_id = str(customer)
                sub_id = _stripe_sub_id(obj)
                if sub_id:
                    u.stripe_subscription_id = sub_id
                expires = _activate_plan(s, u, pay.tier)
                logger.info('PLAN ACTIVATED user=%s tier=%s until=%s order=%s sub=%s',
                            u.email, pay.tier, expires, order_id, sub_id)
        s.commit()
    finally:
        s.close()
    return ('ok', 200)


def _stripe_invoice_paid(obj):
    """A renewal. Only 'subscription_cycle' invoices are handled here: the very
    first invoice of a subscription arrives as 'subscription_create' alongside
    checkout.session.completed, and acting on both would extend twice."""
    if obj.get('billing_reason') != 'subscription_cycle':
        return ('ok', 200)
    customer = obj.get('customer')
    if isinstance(customer, dict):
        customer = customer.get('id')
    customer = str(customer or '')
    invoice_id = str(obj.get('id') or '')

    from db import User, Payment, get_payment_by_order, get_user_by_stripe_customer
    s = _db_session()
    try:
        # The invoice id is the order_id for renewals, so a redelivered webhook
        # finds the existing row and extends nothing a second time.
        if invoice_id and get_payment_by_order(s, invoice_id):
            logger.info('STRIPE renewal %s already recorded', invoice_id)
            return ('ok', 200)
        u = get_user_by_stripe_customer(s, customer)
        if not u:
            logger.warning('Stripe renewal for unknown customer %s', customer)
            return ('ok', 200)
        tier_key = (obj.get('subscription_details') or {}).get('metadata', {}).get('tier') \
            or (obj.get('metadata') or {}).get('tier') or u.tier
        if tier_key not in TIERS:
            logger.warning('Stripe renewal for unknown tier %s user=%s', tier_key, u.email)
            return ('ok', 200)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        sub_id = _stripe_sub_id(obj)
        if sub_id:
            u.stripe_subscription_id = sub_id
        expires = _activate_plan(s, u, tier_key)
        s.add(Payment(user_id=u.id, tier=tier_key, provider='stripe',
                      amount=str(TIERS[tier_key]['price']), currency=CURRENCY,
                      order_id=invoice_id or f'stripe-{secrets.token_hex(6)}',
                      track_id=sub_id, status='paid', paid_at=now))
        s.commit()
        logger.info('PLAN RENEWED user=%s tier=%s until=%s invoice=%s',
                    u.email, tier_key, expires, invoice_id)
    finally:
        s.close()
    return ('ok', 200)


def _stripe_subscription_deleted(obj):
    """Cancellation. Access is not revoked here — expires_at already covers the
    period the customer paid for, and _current_user expires it when it lapses."""
    from db import get_user_by_stripe_customer
    customer = obj.get('customer')
    if isinstance(customer, dict):
        customer = customer.get('id')
    s = _db_session()
    try:
        u = get_user_by_stripe_customer(s, str(customer or ''))
        if u:
            u.stripe_subscription_id = None
            s.commit()
            logger.info('STRIPE subscription cancelled user=%s access until=%s',
                        u.email, u.expires_at)
    finally:
        s.close()
    return ('ok', 200)


@app.route('/api/billing/portal', methods=['POST'])
def api_billing_portal():
    """Stripe billing portal: update the card, see invoices, cancel. Stripe
    hosts it, so cancellation never needs a route of our own."""
    user = _current_user()
    if not user:
        return jsonify({'error': 'Sign in required'}), 401
    if not _stripe_key():
        return jsonify({'error': 'Payments are not configured yet.'}), 503
    if not user.get('stripe_customer_id'):
        return jsonify({'error': 'No card subscription on this account.'}), 400
    payload = _stripe_post('/billing_portal/sessions', {
        'customer': user['stripe_customer_id'],
        'return_url': f'{_callback_origin()}/account',
    })
    if not payload or not payload.get('url'):
        return jsonify({'error': 'Could not open the billing portal.'}), 502
    return jsonify({'url': payload['url']})


# ── Static pages ─────────────────────────────────────────────────────────────

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if not _check_admin():
        if request.method == 'POST':
            pw = request.form.get('password', '')
            if pw == _admin_password():
                session['admin_authed'] = True
                return redirect('/dashboard')
            return render_template_string(LOGIN_HTML, error='Incorrect password.')
        return render_template_string(LOGIN_HTML, error=None)
    return send_from_directory(BASE_DIR, 'dashboard.html')

@app.route('/chat', methods=['GET'])
def chat_page():
    return send_from_directory(BASE_DIR, 'chat.html')

LANDING_PERSONA_KEY = 'landing_persona'
LANDING_PERSONA_DEFAULT = 'nova'


def _all_persona_slugs():
    """Every slug the chat can open: repo files, /tmp writes on Vercel, and
    saved DB personas."""
    slugs = set()
    for d in (PERSONAS_DIR, TMP_PERSONAS_DIR):
        if os.path.isdir(d):
            for fname in os.listdir(d):
                if fname.endswith('.txt'):
                    slugs.add(fname[:-4])
    try:
        for sp in db_list_personas():
            slugs.add(sp['slug'])
    except Exception:
        pass
    return sorted(slugs)


def _landing_persona():
    """Persona the landing page's chat opens with. Falls back rather than
    breaking the page: the chosen persona can be deleted after the fact."""
    slugs = _all_persona_slugs()
    for candidate in (_get_setting(LANDING_PERSONA_KEY), LANDING_PERSONA_DEFAULT):
        if candidate and candidate in slugs:
            return candidate
    return slugs[0] if slugs else LANDING_PERSONA_DEFAULT


def _persona_display_name(slug):
    saved = db_get_persona(slug)
    if saved and isinstance(saved.get('config'), dict) and saved['config'].get('name'):
        return saved['config']['name']
    config_path = _persona_path(slug, '.config.json')
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                name = json.load(f).get('name')
            if name:
                return name
        except Exception:
            pass
    return slug.capitalize()


@app.route('/api/landing')
def api_landing_get():
    """Public: the landing page reads this to point its chat at the right model.
    Operators also get the pickable list, which is exactly what the POST below
    accepts — /api/personas is scoped to the caller's own personas and would
    hide the premades."""
    out = {
        'persona': _landing_persona(),
        'configured': _get_setting(LANDING_PERSONA_KEY) or '',
    }
    if _is_operator():
        out['options'] = [{'slug': s, 'name': _persona_display_name(s)}
                          for s in _all_persona_slugs()]
    return jsonify(out)


@app.route('/api/landing', methods=['POST'])
@operator_only
def api_landing_set():
    slug = ((request.get_json(silent=True) or {}).get('persona') or '').strip()
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    if slug not in _all_persona_slugs():
        return jsonify({'error': 'No persona with that name'}), 404
    _set_setting(LANDING_PERSONA_KEY, slug)
    return jsonify({'ok': True, 'persona': slug})


@app.route('/landing')
def landing():
    return send_from_directory(BASE_DIR, 'landingpage.html')


# Inline text/image editing for the plain marketing pages (not the persona
# landing page, not the dashboard) — see js/page-editor.js. One JSON blob per
# page, keyed by the data-edit-id an operator clicked on.
SITE_CONTENT_PAGES = {'home', 'login', 'register', 'pricing'}


@app.route('/api/site-content/<page>')
def api_site_content_get(page):
    if page not in SITE_CONTENT_PAGES:
        return jsonify({'error': 'Unknown page'}), 404
    raw = _get_setting(f'site_content_{page}')
    try:
        content = json.loads(raw) if raw else {}
    except Exception:
        content = {}
    return jsonify({'content': content if isinstance(content, dict) else {}})


@app.route('/api/site-content/<page>', methods=['POST'])
@operator_only
def api_site_content_set(page):
    if page not in SITE_CONTENT_PAGES:
        return jsonify({'error': 'Unknown page'}), 404
    content = (request.get_json(silent=True) or {}).get('content')
    if not isinstance(content, dict):
        return jsonify({'error': 'content must be an object'}), 400
    clean = {k: v for k, v in content.items()
             if re.match(r'^[a-zA-Z0-9_-]+$', k) and isinstance(v, str)}
    if len(json.dumps(clean)) > 3_000_000:
        return jsonify({'error': 'Content too large'}), 413
    _set_setting(f'site_content_{page}', json.dumps(clean))
    return jsonify({'ok': True})

# Operator consoles. _is_operator() rather than _check_admin() on purpose: with
# ADMIN_PASSWORD unset the latter is true for everyone, which would hand every
# paying creator the shared-bot registration and trace panels.
@app.route('/xbot')
def xbot_page():
    if not _is_operator():
        return redirect('/dashboard')
    return send_from_directory(BASE_DIR, 'xbot.html')

@app.route('/fanvue')
def fanvue_page():
    # Unlike xbot/threads, every /api/fanvue/* call a creator can reach is
    # already scoped to their own personas (platform_scoped, owned_slugs), so
    # this console is safe to open past the operator check — the entitlement
    # before_request (subscription + platforms capability) has already run.
    if not (_is_operator() or _current_user()):
        return redirect('/dashboard')
    return send_from_directory(BASE_DIR, 'fanvue.html')

@app.route('/threads')
def threads_page():
    if not _is_operator():
        return redirect('/dashboard')
    return send_from_directory(BASE_DIR, 'threads.html')

@app.route('/telegram')
def telegram_page():
    if not _is_operator():
        return redirect('/dashboard')
    return send_from_directory(BASE_DIR, 'telegram.html')

@app.route('/', methods=['GET'])
def home_page():
    return send_from_directory(BASE_DIR, 'comingsoon.html')


# The page moved to the root; the old paths stay as redirects for links already
# out in the wild.
@app.route('/comingsoon')
@app.route('/soon')
def comingsoon_page():
    return redirect('/')

@app.route('/profile')
def profile():
    return redirect('/landing')

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    return redirect('/dashboard')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_authed', None)
    return redirect('/dashboard')

@app.route('/dashboard/logout')
def dashboard_logout():
    session.pop('admin_authed', None)
    return redirect('/dashboard')


# ── Visitor log (who's on the site: IP, geo-location, time) ───────────────────
# Records one row per page view (IP + country/city via geo lookup + timestamp).
# View it at /admin/visitors ; download the raw log at /admin/visitors.log .

_GEO_CACHE = {}
_VISIT_LOG_FILE = os.path.join(LOG_DIR, 'visitors.log') if LOG_DIR else None
_VISIT_SKIP_PREFIXES = ('/api/', '/static/', '/css/', '/js/', '/assets/')
_VISIT_SKIP_EXACT = {'/healthz', '/favicon.ico', '/admin/visitors', '/admin/visitors.log'}


def _now_str():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def _client_ip():
    """Real client IP behind Cloud Run / proxies (first hop of X-Forwarded-For)."""
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.headers.get('X-Real-IP') or request.remote_addr or ''


def _is_private_ip(ip):
    if not ip:
        return True
    privates = ('10.', '127.', '192.168.', '169.254.',
                '172.16.', '172.17.', '172.18.', '172.19.', '172.20.', '172.21.',
                '172.22.', '172.23.', '172.24.', '172.25.', '172.26.', '172.27.',
                '172.28.', '172.29.', '172.30.', '172.31.', '::1', 'fc', 'fd')
    return ip.startswith(privates) or ip in ('localhost', '0.0.0.0')


def _flag(cc):
    if not cc or len(cc) != 2:
        return ''
    cc = cc.upper()
    try:
        return chr(0x1F1E6 + ord(cc[0]) - 65) + chr(0x1F1E6 + ord(cc[1]) - 65)
    except Exception:
        return ''


def _geo_lookup(ip):
    """Resolve an IP to {country, country_code, region, city}. Cached, best-effort."""
    if ip in _GEO_CACHE:
        return _GEO_CACHE[ip]
    geo = {'country': None, 'country_code': None, 'region': None, 'city': None}
    if _is_private_ip(ip):
        geo = {'country': 'Local/Private', 'country_code': '', 'region': '', 'city': ''}
    else:
        try:
            url = ('http://ip-api.com/json/' + urllib.parse.quote(ip) +
                   '?fields=status,country,countryCode,regionName,city')
            with urllib.request.urlopen(url, timeout=4) as r:
                d = json.loads(r.read())
            if d.get('status') == 'success':
                geo = {'country': d.get('country'), 'country_code': d.get('countryCode'),
                       'region': d.get('regionName'), 'city': d.get('city')}
        except Exception:
            pass
    _GEO_CACHE[ip] = geo
    return geo


def _append_visit_logfile(ts, ip, geo, path, ua):
    if not _VISIT_LOG_FILE:
        return
    try:
        os.makedirs(os.path.dirname(_VISIT_LOG_FILE), exist_ok=True)
        loc = ' / '.join(p for p in (geo.get('city'), geo.get('region'), geo.get('country')) if p) or '?'
        with open(_VISIT_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{ts}\t{ip}\t{loc}\t{path}\t{(ua or '')[:160]}\n")
    except Exception:
        pass


def _finalize_visit(vid, ip, path, ua):
    """Background: geo-locate the IP, persist it to the row, append to the log file."""
    geo = _geo_lookup(ip)
    try:
        from db import SessionLocal, set_visit_geo
        s = SessionLocal()
        try:
            set_visit_geo(s, vid, geo['country'], geo['country_code'], geo['region'], geo['city'])
            s.commit()
        finally:
            s.close()
    except Exception:
        pass
    _append_visit_logfile(_now_str(), ip, geo, path, ua)


@app.after_request
def _log_visit(response):
    try:
        path = request.path or '/'
        if (request.method == 'GET'
                and response.status_code == 200
                and (response.mimetype or '').startswith('text/html')
                and path not in _VISIT_SKIP_EXACT
                and not path.startswith(_VISIT_SKIP_PREFIXES)):
            ip = _client_ip()
            ua = request.headers.get('User-Agent', '')
            ref = request.headers.get('Referer', '')
            from db import SessionLocal, add_visit
            s = SessionLocal()
            try:
                v = add_visit(s, ip=ip, path=path, user_agent=ua, referrer=ref)
                s.commit()
                vid = v.id
            finally:
                s.close()
            threading.Thread(target=_finalize_visit, args=(vid, ip, path, ua), daemon=True).start()
    except Exception:
        pass
    return response


def _visitors_rows(limit=1000):
    from db import SessionLocal, list_visits
    s = SessionLocal()
    try:
        rows = []
        for v in list_visits(s, limit=limit):
            rows.append({
                'time': v.created_at.strftime('%Y-%m-%d %H:%M:%S UTC') if v.created_at else '',
                'ip': v.ip or '',
                'flag': _flag(v.country_code),
                'country': v.country or '',
                'region': v.region or '',
                'city': v.city or '',
                'path': v.path or '',
                'user_agent': v.user_agent or '',
            })
        return rows
    finally:
        s.close()


VISITORS_HTML = """<!DOCTYPE html>
<html lang="en" data-theme="dark"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<meta http-equiv="refresh" content="30">
<title>Visitor log</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0f;color:#e7e9ee;font-family:-apple-system,Segoe UI,system-ui,sans-serif;padding:24px}
h1{font-size:1.25rem;margin-bottom:4px}
.sub{color:#8b8f9a;font-size:.85rem;margin-bottom:18px}
.bar{display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap}
a.btn{background:#7c3aed;color:#fff;text-decoration:none;padding:8px 14px;border-radius:9px;font-size:.85rem;font-weight:600}
a.btn.ghost{background:#26262b}
table{width:100%;border-collapse:collapse;font-size:.82rem}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #232327;white-space:nowrap}
th{color:#a1a1aa;font-weight:600;position:sticky;top:0;background:#0d0d0f}
td.ua{white-space:normal;color:#8b8f9a;max-width:340px;font-size:.74rem}
tr:hover td{background:#16161a}
.empty{color:#71717a;padding:30px 0}
</style></head><body>
<h1>Visitor log <span style="color:#8b8f9a;font-weight:400">({{ rows|length }})</span></h1>
<p class="sub">Page views on this service — newest first. Auto-refreshes every 30s. Times are UTC.</p>
<div class="bar">
  <a class="btn" href="/admin/visitors">↻ Refresh now</a>
  <a class="btn ghost" href="/admin/visitors.log">⬇ Download visitors.log</a>
  <a class="btn ghost" href="/api/visitors">JSON</a>
</div>
{% if rows %}
<table>
<tr><th>Time (UTC)</th><th>Country</th><th>City / Region</th><th>IP</th><th>Page</th><th>Device / browser</th></tr>
{% for r in rows %}
<tr>
  <td>{{ r.time }}</td>
  <td>{{ r.flag }} {{ r.country }}</td>
  <td>{{ r.city }}{% if r.city and r.region %}, {% endif %}{{ r.region }}</td>
  <td>{{ r.ip }}</td>
  <td>{{ r.path }}</td>
  <td class="ua">{{ r.user_agent }}</td>
</tr>
{% endfor %}
</table>
{% else %}
<p class="empty">No visits recorded yet. Open a page (e.g. /chat) and refresh.</p>
{% endif %}
</body></html>"""


@app.route('/api/visitors')
@operator_only
def api_visitors():
    try:
        limit = min(int(request.args.get('limit', 1000)), 5000)
    except (ValueError, TypeError):
        limit = 1000
    return jsonify(_visitors_rows(limit=limit))


@app.route('/admin/visitors.log')
@operator_only
def admin_visitors_logfile():
    lines = ['time\tip\tlocation\tpath\tuser_agent']
    for r in _visitors_rows(limit=5000):
        loc = ' / '.join(p for p in (r['city'], r['region'], r['country']) if p) or '?'
        lines.append(f"{r['time']}\t{r['ip']}\t{loc}\t{r['path']}\t{r['user_agent'][:160]}")
    return ('\n'.join(lines), 200,
            {'Content-Type': 'text/plain; charset=utf-8',
             'Content-Disposition': 'attachment; filename=visitors.log'})


@app.route('/admin/visitors', methods=['GET', 'POST'])
@operator_only
def admin_visitors():
    return render_template_string(VISITORS_HTML, rows=_visitors_rows(limit=1000))


# ── X-account access log (who connects/operates X, from which IP) ─────────────

def _finalize_x_event(eid, ip):
    geo = _geo_lookup(ip)
    try:
        from db import SessionLocal, set_x_event_geo
        s = SessionLocal()
        try:
            set_x_event_geo(s, eid, geo['country'], geo['country_code'], geo['region'], geo['city'])
            s.commit()
        finally:
            s.close()
    except Exception:
        pass


def _log_x_event(action, persona='', detail='', x_username=''):
    """Record an X action with the caller's IP + geo. Best-effort, never raises."""
    try:
        ip = _client_ip()
        ua = request.headers.get('User-Agent', '')
        from db import SessionLocal, add_x_event
        s = SessionLocal()
        try:
            e = add_x_event(s, action=action, ip=ip, persona=persona,
                            detail=detail, x_username=x_username, user_agent=ua)
            s.commit()
            eid = e.id
        finally:
            s.close()
        threading.Thread(target=_finalize_x_event, args=(eid, ip), daemon=True).start()
    except Exception:
        pass


X_LOG_RETENTION_DAYS = 14
_last_prune = [0.0]


def _x_maybe_prune():
    """Drop X messages/events/visits older than the retention window. Throttled
    to at most once an hour so it doesn't run on every message."""
    import time as _t
    if _t.time() - _last_prune[0] < 3600:
        return
    _last_prune[0] = _t.time()
    try:
        from db import SessionLocal, prune_x_data
        s = SessionLocal()
        try:
            prune_x_data(s, days=X_LOG_RETENTION_DAYS)
            s.commit()
        finally:
            s.close()
    except Exception:
        pass


_last_x_log_error = [None]


def _ensure_x_tables():
    """Create the x_messages / x_openers / app_settings tables if missing
    (self-heal when a model was added after the DB was first initialized)."""
    try:
        from db import XMessage, XOpener, AppSetting, PpvDrop, engine
        XMessage.__table__.create(bind=engine, checkfirst=True)
        XOpener.__table__.create(bind=engine, checkfirst=True)
        AppSetting.__table__.create(bind=engine, checkfirst=True)
        PpvDrop.__table__.create(bind=engine, checkfirst=True)
        return True
    except Exception as e:
        _last_x_log_error[0] = f'ensure tables: {str(e)[:140]}'
        return False


def _get_setting(key, default=None):
    try:
        from db import SessionLocal, get_app_setting
        s = SessionLocal()
        try:
            return get_app_setting(s, key, default)
        finally:
            s.close()
    except Exception:
        return default


class SettingUnreadable(Exception):
    """The store could not be reached — which is not the same as an empty value.
    Callers that would otherwise treat a missing row as 'nothing has happened
    yet' must not act on a read that raised this."""


def _get_setting_strict(key, default=None):
    """Like _get_setting, but a database failure raises instead of looking like
    an unset key. Used on paths where 'no row' and 'could not read' would
    otherwise both mean 'this fan is brand new' — and cost the fan a repeat of
    every unlock they already bought."""
    try:
        from db import SessionLocal, get_app_setting
    except Exception as e:
        raise SettingUnreadable(str(e)[:200])
    try:
        s = SessionLocal()
    except Exception as e:
        raise SettingUnreadable(str(e)[:200])
    try:
        return get_app_setting(s, key, default)
    except Exception as e:
        raise SettingUnreadable(str(e)[:200])
    finally:
        try:
            s.close()
        except Exception:
            pass


def _json_setting_strict(key, fallback):
    """A JSON-valued setting, distinguishing 'unset' (fallback) from
    'unreadable' (raises). Malformed JSON is treated as unset — it is a value we
    wrote, so re-deriving it is safe; an unreachable database is not."""
    raw = _get_setting_strict(key, None)
    if raw is None:
        return copy.deepcopy(fallback)
    try:
        v = json.loads(raw)
    except Exception:
        return copy.deepcopy(fallback)
    return v if isinstance(v, type(fallback)) else copy.deepcopy(fallback)


def _set_setting(key, value):
    def _do():
        from db import SessionLocal, set_app_setting
        s = SessionLocal()
        try:
            set_app_setting(s, key, value)
            s.commit()
        finally:
            s.close()
    try:
        _do()
    except Exception:
        if _ensure_x_tables():
            try:
                _do()
            except Exception:
                pass


def _write_x_message(persona, x_user_id, x_username, direction, text):
    from db import SessionLocal, add_x_message
    s = SessionLocal()
    try:
        add_x_message(s, persona=persona, x_user_id=x_user_id,
                      x_username=x_username, direction=direction, text=text)
        s.commit()
    finally:
        s.close()


def _log_x_message(persona, x_user_id, x_username, direction, text):
    """Persist one side of a DM conversation. Best-effort, never raises, but
    records the last failure in _last_x_log_error and self-heals a missing table."""
    if not text:
        return
    try:
        _write_x_message(persona, x_user_id, x_username, direction, text)
        _last_x_log_error[0] = None
        _x_maybe_prune()
    except Exception as e:
        # Most likely the table doesn't exist yet — create it and retry once.
        if _ensure_x_tables():
            try:
                _write_x_message(persona, x_user_id, x_username, direction, text)
                _last_x_log_error[0] = None
                return
            except Exception as e2:
                _last_x_log_error[0] = f'write: {str(e2)[:140]}'
        else:
            _last_x_log_error[0] = f'write: {str(e)[:140]}'


def _xevents_rows(limit=1000):
    from db import SessionLocal, list_x_events
    s = SessionLocal()
    try:
        rows = []
        for e in list_x_events(s, limit=limit):
            rows.append({
                'time': e.created_at.strftime('%Y-%m-%d %H:%M:%S UTC') if e.created_at else '',
                'action': e.action or '',
                'persona': e.persona or '',
                'x_username': e.x_username or '',
                'detail': e.detail or '',
                'ip': e.ip or '',
                'flag': _flag(e.country_code),
                'country': e.country or '',
                'region': e.region or '',
                'city': e.city or '',
                'user_agent': e.user_agent or '',
            })
        return rows
    finally:
        s.close()


def _xchat_list_rows(limit=200):
    from db import SessionLocal, list_x_conversations
    s = SessionLocal()
    try:
        out = []
        for c in list_x_conversations(s, limit=limit):
            out.append({
                'persona': c['persona'], 'x_user_id': c['x_user_id'],
                'x_username': c['x_username'], 'last': c['last'],
                'last_dir': c['last_dir'], 'count': c['count'],
                'time': c['time'].strftime('%Y-%m-%d %H:%M UTC') if c['time'] else '',
            })
        return out
    finally:
        s.close()


def _xchat_thread_rows(persona, uid):
    from db import SessionLocal, list_x_messages
    s = SessionLocal()
    try:
        out, username = [], ''
        for m in list_x_messages(s, persona, uid, limit=500):
            username = m.x_username or username
            out.append({
                'dir': m.direction, 'text': m.text,
                'time': m.created_at.strftime('%d %b %H:%M UTC') if m.created_at else '',
            })
        return out, username
    finally:
        s.close()


XLOG_HTML = """<!DOCTYPE html>
<html lang="en" data-theme="dark"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<meta http-equiv="refresh" content="30">
<title>X access log</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0f;color:#e7e9ee;font-family:-apple-system,Segoe UI,system-ui,sans-serif;padding:24px}
h1{font-size:1.25rem;margin-bottom:4px}
.sub{color:#8b8f9a;font-size:.85rem;margin-bottom:18px}
.bar{display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap}
a.btn{background:#1d9bf0;color:#fff;text-decoration:none;padding:8px 14px;border-radius:9px;font-size:.85rem;font-weight:600}
a.btn.ghost{background:#26262b}
table{width:100%;border-collapse:collapse;font-size:.82rem}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #232327;white-space:nowrap}
th{color:#a1a1aa;font-weight:600;position:sticky;top:0;background:#0d0d0f}
td.ua{white-space:normal;color:#8b8f9a;max-width:280px;font-size:.72rem}
tr:hover td{background:#16161a}
.act{font-weight:600;color:#7cc7ff}
.empty{color:#71717a;padding:30px 0}
</style></head><body>
<h1>X-account access log <span style="color:#8b8f9a;font-weight:400">({{ rows|length }})</span></h1>
<p class="sub">Every X connect/action taken through this site — IP + location of whoever did it. Newest first, UTC. Auto-refresh 30s.</p>
<div class="bar">
  <a class="btn" href="/admin/xlog">↻ Refresh</a>
  <a class="btn ghost" href="/admin/xchats">Chat messages →</a>
  <a class="btn ghost" href="/api/xlog">JSON</a>
  <a class="btn ghost" href="/admin/visitors">Visitor log →</a>
</div>
{% if rows %}
<table>
<tr><th>Time (UTC)</th><th>Action</th><th>Persona</th><th>X account</th><th>Target/detail</th><th>Country</th><th>City</th><th>IP</th><th>Device</th></tr>
{% for r in rows %}
<tr>
  <td>{{ r.time }}</td>
  <td class="act">{{ r.action }}</td>
  <td>{{ r.persona }}</td>
  <td>{% if r.x_username %}@{{ r.x_username }}{% endif %}</td>
  <td>{{ r.detail }}</td>
  <td>{{ r.flag }} {{ r.country }}</td>
  <td>{{ r.city }}{% if r.city and r.region %}, {% endif %}{{ r.region }}</td>
  <td>{{ r.ip }}</td>
  <td class="ua">{{ r.user_agent }}</td>
</tr>
{% endfor %}
</table>
{% else %}
<p class="empty">No X actions logged yet. Connect or operate an X account from /xbot and refresh.</p>
{% endif %}
</body></html>"""


@app.route('/api/xlog')
@operator_only
def api_xlog():
    try:
        limit = min(int(request.args.get('limit', 1000)), 5000)
    except (ValueError, TypeError):
        limit = 1000
    return jsonify(_xevents_rows(limit=limit))


@app.route('/admin/xlog', methods=['GET', 'POST'])
@operator_only
def admin_xlog():
    return render_template_string(XLOG_HTML, rows=_xevents_rows(limit=1000))


XCHATS_HTML = """<!DOCTYPE html>
<html lang="en" data-theme="dark"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<meta http-equiv="refresh" content="30">
<title>X conversations</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0f;color:#e7e9ee;font-family:-apple-system,Segoe UI,system-ui,sans-serif;padding:24px}
h1{font-size:1.25rem;margin-bottom:4px}
.sub{color:#8b8f9a;font-size:.85rem;margin-bottom:18px}
.bar{display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap}
a.btn{background:#1d9bf0;color:#fff;text-decoration:none;padding:8px 14px;border-radius:9px;font-size:.85rem;font-weight:600}
a.btn.ghost{background:#26262b}
table{width:100%;border-collapse:collapse;font-size:.85rem}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid #232327;vertical-align:top}
th{color:#a1a1aa;font-weight:600;position:sticky;top:0;background:#0d0d0f;white-space:nowrap}
td.t{white-space:nowrap;color:#8b8f9a;font-size:.78rem}
td.who{white-space:nowrap;color:#7cc7ff;font-weight:600}
td.msg{color:#b9bdc7}
tr:hover td{background:#16161a}
a.row{color:inherit;text-decoration:none}
.cnt{color:#8b8f9a;font-size:.78rem}
.empty{color:#71717a;padding:30px 0}
</style></head><body>
<h1>X conversations <span style="color:#8b8f9a;font-weight:400">({{ rows|length }})</span></h1>
<p class="sub">Full two-sided DM threads on your connected X personas. Click a row to read the conversation. Logs are kept for {{ days }} days, then deleted. Newest first, UTC. Auto-refresh 30s.</p>
<div class="bar">
  <a class="btn" href="/admin/xchats">↻ Refresh</a>
  <a class="btn ghost" href="/admin/xlog">Action log →</a>
  <a class="btn ghost" href="/xbot">X bot →</a>
</div>
{% if rows %}
<table>
<tr><th>Last activity</th><th>Persona</th><th>Fan</th><th>Latest message</th><th>Msgs</th></tr>
{% for r in rows %}
<tr onclick="location='/admin/xchat?persona={{ r.persona|urlencode }}&uid={{ r.x_user_id|urlencode }}'" style="cursor:pointer">
  <td class="t">{{ r.time }}</td>
  <td>{{ r.persona }}</td>
  <td class="who">{% if r.x_username %}@{{ r.x_username }}{% else %}id {{ r.x_user_id }}{% endif %}</td>
  <td class="msg">{% if r.last_dir == 'out' %}↩ {% endif %}{{ r.last[:90] }}</td>
  <td class="cnt">{{ r.count }}</td>
</tr>
{% endfor %}
</table>
{% else %}
<p class="empty">No conversations logged yet. They appear once a connected persona sends or receives DMs (via auto mode, poll, or chat-up).</p>
{% endif %}
</body></html>"""


XTHREAD_HTML = """<!DOCTYPE html>
<html lang="en" data-theme="dark"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Conversation — {{ persona }}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0f;color:#e7e9ee;font-family:-apple-system,Segoe UI,system-ui,sans-serif;padding:24px}
h1{font-size:1.15rem;margin-bottom:2px}
.sub{color:#8b8f9a;font-size:.82rem;margin-bottom:16px}
a.btn{background:#26262b;color:#fff;text-decoration:none;padding:8px 14px;border-radius:9px;font-size:.85rem;font-weight:600;display:inline-block;margin-bottom:18px}
.thread{max-width:680px;display:flex;flex-direction:column;gap:10px}
.m{max-width:75%;padding:9px 13px;border-radius:14px;font-size:.9rem;line-height:1.35;word-wrap:break-word}
.m .ts{display:block;font-size:.68rem;color:#8b8f9a;margin-top:5px}
.in{background:#1c1c20;align-self:flex-start;border-bottom-left-radius:4px}
.out{background:#1d4e74;align-self:flex-end;border-bottom-right-radius:4px}
.out .ts{color:#bcd9ee}
.empty{color:#71717a;padding:30px 0}
</style></head><body>
<a class="btn" href="/admin/xchats">← All conversations</a>
<h1>{% if username %}@{{ username }}{% else %}id {{ uid }}{% endif %} <span style="color:#8b8f9a;font-weight:400">· {{ persona }}</span></h1>
<p class="sub">Incoming (left) and the persona's replies (right). {{ msgs|length }} messages.</p>
{% if msgs %}
<div class="thread">
{% for m in msgs %}
  <div class="m {{ 'out' if m.dir == 'out' else 'in' }}">{{ m.text }}<span class="ts">{{ m.time }}</span></div>
{% endfor %}
</div>
{% else %}
<p class="empty">No messages in this conversation (they may have been pruned after {{ days }} days).</p>
{% endif %}
</body></html>"""


@app.route('/admin/xchats', methods=['GET', 'POST'])
@operator_only
def admin_xchats():
    return render_template_string(XCHATS_HTML, rows=_xchat_list_rows(limit=300),
                                  days=X_LOG_RETENTION_DAYS)


@app.route('/admin/xchat')
@operator_only
def admin_xchat():
    persona = request.args.get('persona', '')
    uid = request.args.get('uid', '')
    msgs, username = _xchat_thread_rows(persona, uid)
    return render_template_string(XTHREAD_HTML, persona=persona, uid=uid,
                                  username=username, msgs=msgs,
                                  days=X_LOG_RETENTION_DAYS)


# ── Chat endpoint ─────────────────────────────────────────────────────────────

def _chat_exchanges(history):
    """How many messages this fan has sent, counting the one being answered."""
    n = sum(1 for m in (history or []) if (m.get('role') or '') == 'user')
    return n + 1


def _chat_first_seen(history):
    """When this fan first wrote, from the oldest timestamped message.

    The browser chat keeps no fan record, so a days-based phase has no other
    clock. Histories saved before messages carried a `ts` return 0, which reads
    as "started today" rather than erroring — those fans age from now on.
    """
    for m in (history or []):
        try:
            ts = int(m.get('ts') or 0)
        except (TypeError, ValueError):
            continue
        if ts > 0:
            return ts
    return 0


def _chat_fan(history):
    """The fan shape _fan_phase expects, derived from the posted history."""
    return {'in_count': _chat_exchanges(history), 'first_in': _chat_first_seen(history)}


def _recent_bot_questions(history, n):
    """Whether each of the last n bot replies asked a question, newest first."""
    bots = [m.get('content') or '' for m in (history or [])
            if (m.get('role') or '') != 'user']
    return [response_asks_question(t) for t in reversed(bots[-n:])]


def _spicy_ask_count(history, incoming=''):
    """How many times the fan has asked for explicit content, across the visible
    history plus the message they just sent. Used to release the NSFW vault
    after N asks — a repeated ask is as strong a buying signal as one instant
    one, it just takes the fan longer to make it.
    """
    fan_msgs = [m.get('content') or '' for m in (history or [])
                if (m.get('role') or '') == 'user']
    return sum(1 for t in fan_msgs + [incoming] if _spicy_asked(t))


def question_allowed(config, history):
    """False when she has already asked more than her configured frequency
    allows, so the next reply just responds instead of stacking questions."""
    freq = (config or {}).get('question_freq') or 'often'
    if freq == 'very often':
        return True
    asked = _recent_bot_questions(history, 2)
    if not asked:
        return True
    if freq == 'rarely':
        return not any(asked)
    if freq == 'sometimes':
        return not asked[0]
    return not (len(asked) > 1 and asked[0] and asked[1])


def _chat_channel_rules(slug, config, incoming, skip_spicy, history=None):
    """Per-request rules for the browser chat.

    The stored system prompt is shared by every channel, so what a "drop" means
    is decided here: in this chat she sends a real photo from her library, never
    a PPV marker — PPV only exists on Fanvue, where the backend mints the unlock.
    """
    rules = ['\n\nYou are texting in a direct chat. There are no paid unlocks here — '
             'never write "[PPV]", "PPV", a price, or any bracketed marker.']

    if _chat_photo_pool(slug):
        rules.append(
            'You can send real photos of yourself. To send one, put the tag '
            '[SEND_PHOTO:purpose=X] at the very end of your message — purpose is '
            'one word describing the shot (tease, selfie, outfit, spicy). The photo '
            'is attached for you; write only the caption and never mention the tag. '
            'Send one whenever the fan asks to see you, and at every drop step of '
            'the funnel.')
    else:
        rules.append(
            'You have no photos to send yet, so never claim you just sent one — '
            'promise it instead, in your own voice.')

    if not question_allowed(config, history):
        rules.append(
            'You have just asked the fan a question — do not ask another one in '
            'this reply. React to what they said and share something of your own '
            'instead. No question mark at the end.')

    spicy_mode = config.get('spicy_cta', 'normal')
    spicy_now = not skip_spicy and _spicy_asked(incoming)
    spicy = spicy_mode != 'off' and spicy_now

    cta_cfg = _phases_cta(slug)
    url = (cta_cfg.get('cta_url') or '').strip()
    label = (cta_cfg.get('cta_label') or 'come find me here').strip()

    # The browser chat holds no fan record, so the funnel is read off the
    # history the client sends: how many messages they have sent, and whether
    # the link already went out in one of her earlier replies.
    phases = _phases(slug)
    exchanges = _chat_exchanges(history)
    is_cta_phase = _fan_phase(phases, _chat_fan(history)) == len(phases) - 1
    already_sent = bool(url) and any(
        url in (m.get('content') or '') for m in (history or [])
        if (m.get('role') or '') != 'user')
    fan = {'cta_sent': already_sent}
    cta_asked = _cta_asked(incoming)
    spicy_count = 0 if skip_spicy else _spicy_ask_count(history, incoming)
    cta_due = (not skip_spicy) and _cta_due(slug, incoming, fan, is_cta_phase, url, spicy_count)
    logger.info('CTA decision [%s]: due=%s url=%s exchanges=%d cta_phase=%s '
                'already_sent=%s asked=%s spicy=%s spicy_count=%d opener=%s',
                slug, cta_due, 'set' if url else 'MISSING', exchanges,
                is_cta_phase, already_sent, cta_asked, spicy, spicy_count, skip_spicy)

    if cta_due:
        if cta_asked:
            rules.append(
                'They are asking where else to find you — answer directly and warmly, '
                'say yes, that is where the rest of it lives. Do not deflect and do '
                f'not paste a URL yourself; the link is appended after your message, '
                f'phrased as "{label}".')
        elif spicy_now:
            rules.append(
                'They are asking to see explicit content. That is a buying signal: '
                'tease them once, then tell them where the rest of it lives and invite '
                f'them over — phrase the invite as "{label}". Do not paste a URL '
                'yourself; the link is appended after your message.')
        else:
            rules.append(
                'You have been talking a while and she is warm on you now. Answer what '
                'they actually said first, then tease — in one natural sentence — that '
                'you post more somewhere more private. Do not hard-sell, do not name '
                f'the site, and do not paste a URL; the link is appended after your '
                f'message, phrased as "{label}".')
        return '\n'.join(rules), {'url': url, 'label': label}

    if spicy:
        rules.append('They are asking to see explicit content — lean into it rather '
                     'than deflecting, and let it move the conversation forward.')
    return '\n'.join(rules), None


def _chat_photo_pool(slug):
    """(tagged media rows, gallery data URLs) she can actually send here."""
    rows = []
    try:
        from db import SessionLocal, list_persona_media
        sess = SessionLocal()
        try:
            rows = list_persona_media(sess, slug) or []
        finally:
            sess.close()
    except Exception:
        rows = []
    if rows:
        return rows
    try:
        return db_get_images(slug)
    except Exception:
        return []


def db_get_nsfw_images(slug):
    """NSFW image data URLs for a persona — a wholly separate pool from
    db_get_images, held out of every normal send and only ever reached
    through _chat_nsfw_photo at the CTA moment. No repo-file fallback: there
    is no baked-in NSFW set to fall back to."""
    try:
        from db import SessionLocal, get_persona_nsfw_images_row
        s = SessionLocal()
        try:
            row = get_persona_nsfw_images_row(s, slug)
            if row:
                parsed = json.loads(row.images_json)
                if isinstance(parsed, list):
                    return parsed
        finally:
            s.close()
    except Exception:
        pass
    return []


def db_set_nsfw_images(slug, images):
    """Persist a persona's NSFW images (list of data URLs, capped at 5)."""
    from db import SessionLocal, set_persona_nsfw_images_row
    clean = [i for i in (images or []) if isinstance(i, str) and i.startswith('data:')][:5]
    s = SessionLocal()
    try:
        set_persona_nsfw_images_row(s, slug, json.dumps(clean))
        s.commit()
    finally:
        s.close()
    return clean


def _chat_nsfw_photo(slug):
    """Pick one NSFW photo to send with the CTA, or None if she has none."""
    pool = db_get_nsfw_images(slug)
    if not pool:
        return None
    idx = random.randrange(len(pool))
    return f'/api/personas/{slug}/nsfw-image/{idx}'


def _chat_take_photo(slug, reply):
    """Pull a [SEND_PHOTO:...] tag off the reply and resolve it to an image URL.
    Returns (clean_reply, url_or_None)."""
    reply, tags = _tg_parse_photo_tag(reply)
    if not tags:
        return reply, None
    pool = _chat_photo_pool(slug)
    if not pool:
        return reply, None
    if isinstance(pool[0], str):
        idx = random.randrange(len(pool))
        return reply, f'/api/personas/{slug}/image/{idx}'
    safe = {k: v for k, v in tags.items()
            if k in ('purpose', 'lighting', 'location', 'outfit')}
    try:
        picked = _pick_media(pool, outfits=_outfits(slug), **safe)
    except Exception:
        picked = None
    if not picked:
        return reply, None
    return reply, f'/api/personas/{slug}/media/{picked.id}/image'


def _chat_sent_photo_ids(history):
    ids = set()
    for m in history or []:
        found = re.search(r'/media/(\d+)/image', (m.get('photo') or ''))
        if found:
            ids.add(int(found.group(1)))
    return ids


def _chat_phase_photo(slug, history):
    """Roll the current phase's photo rate, the way the Telegram side does.

    The browser chat keeps no fan record, so the phase and the photos already
    sent are read off the history the client posts.
    """
    pool = _chat_photo_pool(slug)
    if not pool:
        return None
    phases = _phases(slug)
    idx = _fan_phase(phases, _chat_fan(history))
    phase = phases[idx] if idx < len(phases) else phases[-1]
    try:
        rate = max(0, min(100, int(phase.get('photo_rate', 20))))
    except (TypeError, ValueError):
        rate = 20
    if rate <= 0 or random.randint(1, 100) > rate:
        return None
    if isinstance(pool[0], str):
        return f'/api/personas/{slug}/image/{random.randrange(len(pool))}'
    picked = _pick_phase_photo(pool, _outfits(slug), _chat_sent_photo_ids(history))
    if not picked:
        return None
    return f'/api/personas/{slug}/media/{picked.id}/image'


# gemini-2.5 models reason before answering. When the response carries those
# thought parts, reaching for response.text pastes the whole chain of thought
# into the chat bubble, so every call that produces a message the fan sees
# turns thinking off and reads only the visible parts.
def _no_thinking(cfg):
    try:
        cfg.thinking_config = types.ThinkingConfig(thinking_budget=0)
    except Exception:
        pass
    return cfg


_THINK_HEADER = re.compile(
    r'^[\s*_#>`]*(think(ing)?|thought(s)?|reasoning|analysis|scratchpad)[\s:*_#>`-]*$',
    re.I)
_REASONING_BLOCK = re.compile(
    r'^\s*(\d+[.)]\s|[-*]\s|constraint|confidence|checklist|plan\b|draft\b)', re.I)


def _strip_thinking(text):
    """Drop a reasoning preamble a model leaked into its visible reply."""
    if not text:
        return ''
    text = re.sub(r'(?is)<(think|thinking|thought|reasoning)>.*?(</\1>|\Z)', '', text)
    blocks = [b for b in re.split(r'\n\s*\n', text.strip()) if b.strip()]
    if not blocks or not _THINK_HEADER.match(blocks[0].splitlines()[0]):
        return text.strip()
    rest = blocks[1:]
    while rest and _REASONING_BLOCK.match(rest[0]):
        rest = rest[1:]
    return '\n\n'.join(rest).strip()


def _gemini_text(resp):
    """Visible reply text from a response, excluding any thought parts."""
    cand = (getattr(resp, 'candidates', None) or [None])[0]
    content = getattr(cand, 'content', None) if cand is not None else None
    parts = list(getattr(content, 'parts', None) or [])
    if parts:
        visible = ''.join(p.text for p in parts
                          if getattr(p, 'text', None)
                          and not getattr(p, 'thought', False))
        return _strip_thinking(visible)
    return _strip_thinking(getattr(resp, 'text', None) or '')


def generate_reply(system_prompt, chat_history, user_message, is_continue=False):
    """Call Gemini with the given history and return the reply text.

    Shared by the browser /chat route and the /api/v1/chat programmatic API.
    Raises on API errors so callers decide how to handle failure.
    """
    contents = []
    for msg in chat_history:
        role = 'user' if msg.get('role') == 'user' else 'model'
        contents.append({'role': role, 'parts': [{'text': msg.get('content', '')}]})

    if is_continue:
        contents.append({'role': 'user', 'parts': [{'text': '(continuing the conversation naturally)'}]})
    else:
        contents.append({'role': 'user', 'parts': [{'text': user_message}]})

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
        config=_no_thinking(types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.75,
            max_output_tokens=1024,
        ))
    )

    reply = _gemini_text(response) or "Hmm. What were you saying?"
    if len(reply) > 1400:
        reply = reply[:1397] + '...'

    recent_bot = [msg.get('content', '').strip().lower()
                  for msg in chat_history[-6:] if msg.get('role') in ('lilith', 'bot', 'model')]
    if reply.strip().lower() in recent_bot:
        reply = "still here, just thinking"
    return reply


@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_message = data.get('message', '').strip()
    chat_history = data.get('history', [])
    is_continue = data.get('continue', False)
    user = data.get('user', 'unknown')
    persona_slug = data.get('persona', DEFAULT_PERSONA)

    chat_logger, safe_user = get_chat_logger(user)

    is_greeting = user_message == '__greeting__'
    if is_greeting:
        user_message = 'Start the conversation with your opening message. Keep it to 1 sentence.'

    if not is_continue and not user_message:
        return jsonify({'error': 'No message'}), 400

    config = load_persona_config(persona_slug)
    pacing = _pacing(config)
    system_prompt = data.get('system_prompt')
    if system_prompt:
        if 'Question frequency:' not in system_prompt:
            system_prompt = system_prompt.rstrip() + '\n\n' + question_freq_rule(config)
    else:
        system_prompt = get_system_prompt(persona_slug)
    rules, cta = _chat_channel_rules(persona_slug, config, user_message,
                                     is_greeting or is_continue, chat_history)
    system_prompt += rules

    if client is None:
        reply = local_fallback_reply(user_message)
        return jsonify({'reply': reply + ' (Local mode — add GEMINI_API_KEY to .env)',
                        'pacing': pacing})

    try:
        chat_logger.info(f'USER [{persona_slug}]: {user_message if not is_continue else "[continue]"}')
        reply = generate_reply(system_prompt, chat_history, user_message, is_continue)
        reply, photo = _chat_take_photo(persona_slug, reply)
        if not photo and not is_greeting:
            photo = _chat_phase_photo(persona_slug, chat_history)
        reply = trim_extra_questions(
            reply, is_greeting or question_allowed(config, chat_history))
        reply = strip_ppv_marker(reply)
        if cta:
            # The vault photo is the preview that goes with the link — she is
            # not showing a tease shot and then, separately, asking the fan to
            # go look elsewhere. Falls back to whatever photo was already
            # resolved (or none) if nothing in the vault is marked NSFW.
            nsfw_photo = _chat_nsfw_photo(persona_slug)
            if nsfw_photo:
                photo = nsfw_photo
            reply = f"{reply}\n\n{cta['label']} → {cta['url']}"
            logger.info('CTA SENT [%s] user=%s exchange=%d nsfw_photo=%s -> %s',
                        persona_slug, safe_user,
                        _chat_exchanges(chat_history), bool(nsfw_photo), cta['url'])
        chat_logger.info(f'BOT [{persona_slug}]: {reply[:120]}{" +photo" if photo else ""}')
        return jsonify({'reply': reply, 'photo': photo, 'pacing': pacing})

    except Exception as e:
        err_msg = str(e)[:300]
        error_logger.error(f'Gemini error [{persona_slug}] user={safe_user}: {err_msg}', exc_info=True)
        reply = local_fallback_reply(user_message)
        return jsonify({'reply': reply + f' (Gemini error: {err_msg})', 'pacing': pacing})


# ── Programmatic API (v1) ─────────────────────────────────────────────────────
# Server-side conversation memory keyed by conversation_id, so an external app
# can drive a client's chat without holding the history itself.

def _valid_api_key():
    """True only if the request carries a key from API_KEYS.

    An unset API_KEYS closes this API rather than opening it. The route sits
    outside the paywall, so the key is the only thing in front of it: treating
    "no keys configured" as "everyone may call it" published a Gemini-backed
    endpoint to the internet on every deployment that had not set the var.
    """
    configured = [k.strip() for k in os.getenv('API_KEYS', '').split(',') if k.strip()]
    if not configured:
        logger.warning('API v1 refused: API_KEYS is unset, so the endpoint is closed')
        return False
    provided = request.headers.get('Authorization', '')
    if provided.startswith('Bearer '):
        provided = provided[7:]
    provided = provided or request.headers.get('X-API-Key', '')
    if not provided:
        return False
    # compare_digest against each key: a plain `in` leaks key length and prefix
    # through timing, and this endpoint is reachable without a session.
    return any(hmac.compare_digest(provided, k) for k in configured)


@app.route('/api/v1/chat', methods=['POST'])
def api_v1_chat():
    if not _valid_api_key():
        return jsonify({'error': 'Invalid or missing API key'}), 401

    data = request.json or {}
    user_message = (data.get('message') or '').strip()
    persona_slug = data.get('persona', DEFAULT_PERSONA)
    conversation_id = data.get('conversation_id')
    client_id = data.get('client_id')

    if not user_message:
        return jsonify({'error': 'message is required'}), 400

    system_prompt = get_system_prompt(persona_slug)

    from db import SessionLocal, get_or_create_conversation, add_message, history_as_dicts
    session_db = SessionLocal()
    try:
        conv = get_or_create_conversation(session_db, conversation_id, persona_slug, client_id)
        history = history_as_dicts(conv)
        add_message(session_db, conv.id, 'user', user_message)

        if client is None:
            reply = local_fallback_reply(user_message)
        else:
            reply = generate_reply(system_prompt, history, user_message)

        add_message(session_db, conv.id, 'model', reply)
        session_db.commit()
        return jsonify({
            'reply': reply,
            'conversation_id': conv.id,
            'persona': persona_slug,
        })
    except Exception as e:
        session_db.rollback()
        error_logger.error(f'API v1 chat error [{persona_slug}]: {str(e)[:300]}', exc_info=True)
        return jsonify({'error': 'Internal error generating reply'}), 500
    finally:
        session_db.close()


# ── Profile API ───────────────────────────────────────────────────────────────

@app.route('/api/profile')
def api_profile():
    slug = request.args.get('persona', DEFAULT_PERSONA)
    data = load_persona_profile(slug)
    return jsonify(data)

@app.route('/api/profile', methods=['POST'])
def api_profile_save():
    slug = request.args.get('persona', '')
    if not slug or not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid persona slug'}), 400
    data = request.json
    path = os.path.join(PERSONAS_DIR, f'{slug}.json')
    # On Vercel, write to /tmp
    if IS_VERCEL:
        path = f'/tmp/{slug}.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return jsonify({'ok': True, 'path': path})


# ── Persona Builder API ───────────────────────────────────────────────────────

@app.route('/api/personas')
def api_personas():
    """List personas visible to the caller.

    Signed-in customers see only personas they own; admins see everything,
    premade repo personas included. Anonymous callers get the full list
    because that is the public fan chat picker.
    """
    viewer = _current_user()
    if viewer and not viewer.get('is_admin'):
        own = []
        for sp in db_list_personas(owner_id=viewer['id']):
            config = sp.get('config', {})
            has_img = bool(config.get('avatar')) or len(db_get_images(sp['slug'])) > 0
            own.append({
                'slug': sp['slug'],
                'name': sp.get('name') or config.get('name') or sp['slug'].capitalize(),
                'avatar': f"/api/personas/{sp['slug']}/avatar" if has_img else None,
                'config': config,
                'premade': False,
                'created_at': sp.get('created_at'),
                'updated_at': sp.get('updated_at'),
            })
        return jsonify(own)

    # Collect slugs from both static personas dir and /tmp (Vercel writes)
    slugs = set()
    for fname in os.listdir(PERSONAS_DIR):
        if fname.endswith('.txt'):
            slugs.add(fname[:-4])
    if os.path.isdir(TMP_PERSONAS_DIR):
        for fname in os.listdir(TMP_PERSONAS_DIR):
            if fname.endswith('.txt'):
                slugs.add(fname[:-4])

    personas = []
    for slug in sorted(slugs):
        config_path = _persona_path(slug, '.config.json')
        config = {}
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        meta_path = _persona_path(slug, '.json')
        meta = {}
        if os.path.exists(meta_path):
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
        # A saved DB override for this original wins over the repo file.
        override = db_get_persona(slug)
        if override and isinstance(override.get('config'), dict):
            config = override['config']
        has_img = bool(config.get('avatar')) or len(db_get_images(slug)) > 0
        personas.append({
            'slug': slug,
            'name': config.get('name') or meta.get('cover_label') or slug.capitalize(),
            'avatar': f'/api/personas/{slug}/avatar' if has_img else None,
            'config': config,
            'premade': True
        })

    # Saved copies live in the DB (durable across redeploys)
    for sp in db_list_personas():
        if _is_premade(sp['slug']):
            continue  # a committed original shadows any stale DB copy of the same slug
        config = sp.get('config', {})
        has_img = bool(config.get('avatar')) or len(db_get_images(sp['slug'])) > 0
        personas.append({
            'slug': sp['slug'],
            'name': sp.get('name') or config.get('name') or sp['slug'].capitalize(),
            'avatar': f"/api/personas/{sp['slug']}/avatar" if has_img else None,
            'config': config,
            'premade': False,
            'created_at': sp.get('created_at'),
            'updated_at': sp.get('updated_at'),
        })
    return jsonify(personas)


@app.route('/api/personas/<slug>', methods=['GET'])
def api_persona_get(slug):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    saved = db_get_persona(slug)
    if saved:
        return jsonify({'slug': slug, 'config': saved['config'],
                        'pacing': _pacing(saved['config']),
                        'prompt': saved.get('prompt') or '', 'premade': False})
    config_path = _persona_path(slug, '.config.json')
    config = {}
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    prompt = load_persona_prompt(slug) or ''
    return jsonify({'slug': slug, 'config': config, 'pacing': _pacing(config),
                    'prompt': prompt, 'premade': _is_premade(slug)})


def _validate_age(config):
    try:
        return int(config.get('age', 0)) >= 18
    except (ValueError, TypeError):
        return False


@app.route('/api/personas/<slug>', methods=['POST'])
def api_persona_save(slug):
    """Save edits to a saved (copied) persona. Premade originals are read-only —
    editing one must go through the copy endpoint instead."""
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400

    config = request.json
    if not _validate_age(config):
        return jsonify({'error': 'Age must be 18 or older'}), 400

    me = _current_user()
    if _persona_owner(slug) is None:
        blocked = _persona_cap_blocked(me)
        if blocked:
            return blocked

    # Premade originals can be overridden in place: the edit is saved to the DB
    # and shadows the repo file (durable in Postgres).
    try:
        prompt = build_system_prompt(config)
        name = config.get('name') or slug.capitalize()
        db_save_persona(slug, name, config, prompt,
                        owner_id=_workspace_id(me) or None)
    except Exception as e:
        logging.exception('persona save failed for %s', slug)
        return jsonify({'error': f'Save failed: {e}'}), 500
    return jsonify({'ok': True, 'slug': slug, 'prompt': prompt})


@app.route('/api/personas/<slug>/favorite', methods=['POST'])
def api_persona_favorite(slug):
    """Toggle (or set) the favorite flag used to build the sidebar quick-list."""
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    data = request.json or {}

    saved = db_get_persona(slug)
    if saved:
        config = dict(saved['config'])
    else:
        config_path = _persona_path(slug, '.config.json')
        config = {}
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

    config['favorite'] = bool(data.get('favorite')) if 'favorite' in data else not bool(config.get('favorite'))
    try:
        prompt = build_system_prompt(config)
        name = config.get('name') or slug.capitalize()
        me = _current_user()
        db_save_persona(slug, name, config, prompt, owner_id=_workspace_id(me) or None)
    except Exception as e:
        logging.exception('favorite toggle failed for %s', slug)
        return jsonify({'error': f'Could not save: {e}'}), 500
    return jsonify({'ok': True, 'slug': slug, 'favorite': config['favorite']})


@app.route('/api/personas/copy', methods=['POST'])
def api_persona_copy():
    """Copy any persona (premade or saved) into a new, renamed saved persona."""
    data = request.json or {}
    source = data.get('source', '')
    new_name = (data.get('name') or '').strip()
    if not re.match(r'^[a-z0-9_-]+$', source):
        return jsonify({'error': 'Invalid source slug'}), 400
    if not new_name:
        return jsonify({'error': 'A name is required for the copy'}), 400
    # You may only copy a persona you can already see.
    me = _current_user()
    if not me:
        return jsonify({'error': 'Sign in required'}), 401
    if not me.get('is_admin') and _persona_owner(source) != _workspace_id(me):
        return jsonify({'error': 'Not found'}), 404
    blocked = _persona_cap_blocked(me)
    if blocked:
        return blocked

    # Prefer a live config sent from the editor (carries unsaved edits); else
    # pull the source config from the DB or repo files.
    config = data.get('config')
    if not isinstance(config, dict):
        src = db_get_persona(source)
        if src:
            config = dict(src['config'])
        else:
            cfg_path = _persona_path(source, '.config.json')
            if not os.path.exists(cfg_path):
                return jsonify({'error': 'Source persona not found'}), 404
            with open(cfg_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
    else:
        config = dict(config)

    config['name'] = new_name
    if not _validate_age(config):
        return jsonify({'error': 'Age must be 18 or older'}), 400

    try:
        slug = unique_copy_slug(new_name)
        prompt = build_system_prompt(config)
        me = _current_user()
        db_save_persona(slug, new_name, config, prompt,
                        owner_id=_workspace_id(me) or None)
    except Exception as e:
        logging.exception('persona copy failed for %s', new_name)
        return jsonify({'error': f'Copy failed: {e}'}), 500
    return jsonify({'ok': True, 'slug': slug, 'name': new_name})


@app.route('/api/personas/<slug>', methods=['DELETE'])
def api_persona_delete(slug):
    """Delete a saved copy. Premade originals cannot be deleted."""
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    if _is_premade(slug) and not db_get_persona(slug):
        return jsonify({'error': 'Premade models cannot be deleted'}), 403
    from db import SessionLocal, delete_saved_persona
    s = SessionLocal()
    try:
        ok = delete_saved_persona(s, slug)
        s.commit()
    finally:
        s.close()
    _prompt_cache.pop(slug, None)
    return jsonify({'ok': ok})


@app.route('/api/personas/<slug>/preview', methods=['POST'])
def api_persona_preview(slug):
    """Return the generated system prompt without saving."""
    config = request.json
    prompt = build_system_prompt(config)
    return jsonify({'prompt': prompt})


@app.route('/api/generate/speech-style', methods=['POST'])
def api_generate_speech_style():
    if client is None:
        return jsonify({'ok': False, 'error': 'Gemini not configured.'}), 200
    data = request.json or {}
    name = data.get('name', 'the persona')
    age = data.get('age', '')
    archetype = data.get('archetype', '')
    backstory = data.get('backstory', '')
    interests = data.get('interests', '')
    randomize = data.get('randomize', False)

    rand_note = ' Be creative and unexpected — avoid clichés.' if randomize else ''

    baseline_rules = (
        "- Replies are short to medium length — 1 to 3 sentences max per message, never walls of text.\n"
        "- Sends messages in quick natural bursts, not one long block.\n"
        "- Lowercase by default; uses proper caps only for emphasis or names.\n"
        "- Minimal punctuation — no full stops at end of casual lines, occasional ellipsis for trailing off.\n"
        "- Response timing feels natural: short replies come fast, longer ones have a brief pause."
    )

    system = (
        "You write persona-specific speech style rules for an AI chatbot. "
        "The following baseline chat rules are already applied to every persona — do NOT repeat them:\n"
        + baseline_rules
        + "\n\nOutput ONLY the additional rules that are missing from the baseline above, "
        "tailored to this specific persona's personality and backstory. "
        "2-4 bullet points. No preamble, no headers, no quotation marks."
    )
    prompt = (
        f"Write the persona-specific speech style additions for {name}"
        + (f", age {age}" if age else "")
        + (f", archetype: {archetype}" if archetype else "")
        + (f". Backstory: {backstory}" if backstory else "")
        + (f". Interests: {interests}" if interests else "")
        + ".\n\nFocus on what makes THIS persona's texting voice distinct: "
        "their slang, emoji use (or absence), warmth level in word choice, "
        "how they flirt or tease through language, and one distinctive quirk."
        + rand_note
    )
    try:
        resp = client.models.generate_content(
            model=MODEL_NAME,
            contents=[{'role': 'user', 'parts': [{'text': prompt}]}],
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.9 if randomize else 0.7),
        )
        persona_rules = (resp.text or '').strip()
        full_text = "— Baseline rules (always active) —\n" + baseline_rules + "\n\n— Persona-specific —\n" + persona_rules
        return jsonify({'ok': True, 'text': full_text})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 200


_ARCHETYPES = ['Deadpan / Dry', 'Bubbly / Sweet', 'Dominant / Edgy', 'Girl-Next-Door',
               'Mysterious / Dark', 'Playful / Teasing', 'Intellectual / Witty']


def _parse_persona_json(raw):
    """Parse a JSON persona from model output, tolerating code fences."""
    if not raw:
        return None
    txt = raw.strip()
    if txt.startswith('```'):
        txt = re.sub(r'^```[a-zA-Z]*\n?', '', txt)
        txt = re.sub(r'\n?```$', '', txt).strip()
    try:
        return json.loads(txt)
    except Exception:
        m = re.search(r'\{.*\}', txt, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def _pick(value, allowed, default):
    if isinstance(value, str):
        for a in allowed:
            if value.strip().lower() == a.lower():
                return a
    return default


def _normalize_persona(cfg):
    """Coerce a generated persona into valid, in-range field values."""
    out = {}
    out['name'] = str(cfg.get('name') or 'Aria').strip()[:40]
    try:
        age = int(cfg.get('age', 22))
    except (ValueError, TypeError):
        age = 22
    out['age'] = str(max(18, min(60, age)))
    out['gender'] = _pick(cfg.get('gender'), ['Female', 'Male', 'Trans Female to Male', 'Trans Male to Female'], 'Female')
    out['location'] = str(cfg.get('location') or '').strip()[:80]
    out['archetype'] = _pick(cfg.get('archetype'), _ARCHETYPES, 'Girl-Next-Door')
    out['backstory'] = str(cfg.get('backstory') or '').strip()
    out['speech_style'] = str(cfg.get('speech_style') or '').strip()
    try:
        warmth = int(cfg.get('warmth', 3))
    except (ValueError, TypeError):
        warmth = 3
    out['warmth'] = max(1, min(5, warmth))
    out['question_freq'] = _pick(cfg.get('question_freq'), ['rarely', 'sometimes', 'often', 'very often'], 'often')
    out['emoji_use'] = _pick(cfg.get('emoji_use'), ['none', 'rare', 'some', 'lots'], 'none')
    out['reply_length'] = _pick(cfg.get('reply_length'), ['short', 'medium', 'long'], 'short')
    out['lowercase'] = bool(cfg.get('lowercase', True))
    interests = cfg.get('interests')
    if isinstance(interests, list):
        interests = ', '.join(str(i) for i in interests)
    out['interests'] = str(interests or '').strip()
    out['flirt_pace'] = _pick(cfg.get('flirt_pace'), ['slow', 'moderate', 'fast', 'instant'], 'moderate')
    out['nsfw_enabled'] = bool(cfg.get('nsfw_enabled', True))
    out['nsfw_level'] = _pick(cfg.get('nsfw_level'), ['suggestive', 'moderate', 'explicit'], 'moderate')
    out['conversion_triggers'] = str(cfg.get('conversion_triggers') or '').strip()
    return out


@app.route('/api/geo/suggest')
def api_geo_suggest():
    """City/country autocomplete via OpenStreetMap Nominatim (country mandatory)."""
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify({'suggestions': []})
    try:
        # Photon (Komoot) supports prefix/typeahead matching, e.g. "heerl" → Heerlen.
        url = 'https://photon.komoot.io/api/?' + urllib.parse.urlencode({'q': q, 'limit': 10, 'lang': 'en'})
        req = urllib.request.Request(url, headers={'User-Agent': 'ai-model-chat/1.0'})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode())
        seen, out = set(), []
        for feat in data.get('features', []):
            p = feat.get('properties', {})
            country = p.get('country')
            if not country:
                continue
            # Only place-like results (cities, towns, countries, states).
            if p.get('osm_key') not in (None, 'place', 'boundary'):
                continue
            city = p.get('city') or (p.get('name') if p.get('osm_value') in ('city', 'town', 'village', 'municipality') else '')
            label = f'{city}, {country}' if city else country
            if label not in seen:
                seen.add(label)
                out.append({'label': label, 'city': city or '', 'country': country})
            if len(out) >= 8:
                break
        return jsonify({'suggestions': out})
    except Exception as e:
        return jsonify({'suggestions': [], 'error': str(e)[:120]})


@app.route('/api/generate/persona', methods=['POST'])
def api_generate_persona():
    """Generate a complete, internally-consistent persona config in one shot."""
    if client is None:
        return jsonify({'ok': False, 'error': 'Gemini not configured.'}), 200
    data = request.json or {}
    hint = (data.get('hint') or '').strip()

    system = (
        "You design coherent, believable AI companion personas for an adult creator platform. "
        "Invent ONE person whose every trait fits together and is realistic: age, where they're from, "
        "backstory, personality archetype, the way they text, warmth, interests, and flirt style must ALL be "
        "mutually consistent. A shy bookish 19-year-old student must not text like a brash 28-year-old dominatrix. "
        "Backstory must be age-appropriate: 18-24 = student or recent school-leaver with a realistic side job "
        "(barista, waiter, retail, dishwasher, delivery); 25-30 = early career, junior role, freelancing, or a small venture. "
        "No fictional careers for young ages.\n\n"
        "Return ONLY a JSON object (no markdown, no prose) with EXACTLY these keys: "
        "name (first name), age (integer 18-32), gender (one of "
        '["Female","Male","Trans Female to Male","Trans Male to Female"]), '
        'location ("City, Country"), archetype (one of ' + json.dumps(_ARCHETYPES) + "), "
        "backstory (2-3 specific sentences), speech_style (2-4 sentences on how they text — sentence length, "
        "capitalisation, emoji use, slang, quirks — matching the archetype and backstory), "
        "warmth (integer 1-5, consistent with the archetype), "
        'question_freq (one of ["rarely","sometimes","often","very often"]), '
        "interests (comma-separated string of 5-8 topics that fit the backstory — keep it down-to-earth "
        "and relatable, things a normal person spends time on, at most one leaning intellectual or niche), "
        'flirt_pace (one of ["slow","moderate","fast","instant"], consistent with the archetype), '
        "nsfw_enabled (boolean), nsfw_level (one of [\"suggestive\",\"moderate\",\"explicit\"]), "
        "conversion_triggers (1-2 sentences, in their own voice, on how they naturally introduce paid content). "
        "Ensure deep internal consistency across every field."
    )
    user = "Create a fresh, original, distinctive persona."
    if hint:
        user += f" Use this theme or starting idea: {hint}."
    else:
        user += " Make it unexpected — avoid clichés."

    try:
        resp = client.models.generate_content(
            model=MODEL_NAME,
            contents=[{'role': 'user', 'parts': [{'text': user}]}],
            config=types.GenerateContentConfig(
                system_instruction=system, temperature=1.0,
                response_mime_type='application/json'),
        )
        cfg = _parse_persona_json(resp.text or '')
        if not cfg:
            return jsonify({'ok': False, 'error': 'Could not parse the generated persona.'}), 200
        return jsonify({'ok': True, 'config': _normalize_persona(cfg)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 200


def _appearance_from_config(cfg):
    """Build a fixed physical-appearance description so multiple photos of a
    persona depict the same fictional person."""
    if cfg.get('appearance'):
        return cfg['appearance']
    name = cfg.get('name', '')
    age = cfg.get('age', '24')
    archetype = cfg.get('archetype', '')
    location = cfg.get('location', '')
    bits = [f"a {age}-year-old woman"]
    if location:
        bits.append(f"from {location}")
    if archetype:
        bits.append(f"with a {archetype.lower().split('/')[0].strip()} look")
    return ', '.join(bits)


_SHOT_FRAMING = {
    'portrait': 'a head-and-shoulders portrait selfie, looking at the camera',
    'half': 'a waist-up casual photo',
    'full': 'a full-body photo in a casual outfit',
    'candid': 'a candid lifestyle photo doing an everyday activity',
    'mirror': 'a mirror selfie holding a phone',
    # NSFW gallery shots. Imagen's own safety filter still applies (relaxed a
    # notch below, not disabled), so these are written to be the most
    # suggestive framing that plausibly clears it — lingerie and implied,
    # never explicit. Genuinely explicit content is a manual upload into the
    # same slots, not something this endpoint can promise to generate.
    'lingerie': 'a boudoir photo in matching lingerie, soft window light',
    'implied': "an implied-nude photo — bare shoulders and back, the camera "
               "angle and framing suggesting more than it shows",
    'sheer': 'a photo in a sheer, partially see-through robe, artistic and moody',
    'bedroom': 'a sultry, relaxed bedroom photo, intimate mood',
}

_NSFW_SHOTS = ('lingerie', 'implied', 'sheer', 'bedroom')


def _gemini_image_parts(resp):
    """Parts from an image response, tolerating a candidate with no content.
    A filtered request comes back with content set to None, which reaching
    straight through to .parts turns into an unhelpful AttributeError."""
    cand = (getattr(resp, 'candidates', None) or [None])[0]
    content = getattr(cand, 'content', None) if cand is not None else None
    return list(getattr(content, 'parts', None) or [])


IMAGE_ATTEMPTS = 3


def _gemini_finish_reason(resp):
    """The finish reason name from an image response, or '' if there is none."""
    cand = (getattr(resp, 'candidates', None) or [None])[0]
    finish = getattr(cand, 'finish_reason', None) if cand is not None else None
    return str(getattr(finish, 'name', finish) or '')


# IMAGE_OTHER is a catch-all for "no image came back" and is NOT a safety block:
# an ambiguous prompt, a stalled generation or a transient API problem all land
# here, and Google's own guidance is to retry before concluding anything.
_RETRYABLE_IMAGE_REASONS = {'IMAGE_OTHER', 'OTHER', ''}

_IMAGE_REASON_HELP = {
    'IMAGE_SAFETY': 'the safety filter rejected it — try a different pose or photo',
    'PROHIBITED_CONTENT': 'the content policy rejected it — try a different pose or photo',
    'IMAGE_PROHIBITED_CONTENT': 'the content policy rejected it — try a different pose or photo',
    'SAFETY': 'the safety filter rejected it — try a different pose or photo',
    'IMAGE_RECITATION': 'it looked too close to a copyrighted image',
    'RECITATION': 'it looked too close to a copyrighted image',
    'IMAGE_OTHER': 'the model just did not produce one. This is usually temporary '
                   'rather than anything about your photo — try again',
    'OTHER': 'the model just did not produce one. This is usually temporary — try again',
    'MAX_TOKENS': 'the response was cut short',
}


def _gemini_block_reason(resp):
    """Plain-English reason an image response carried no picture."""
    bits = []
    cand = (getattr(resp, 'candidates', None) or [None])[0]
    if cand is None:
        bits.append('the model returned no candidates')
    else:
        finish = _gemini_finish_reason(resp)
        if finish:
            bits.append(_IMAGE_REASON_HELP.get(finish, f'finish reason {finish}'))
        safety = getattr(cand, 'safety_ratings', None) or []
        blocked = [getattr(r, 'category', '') for r in safety if getattr(r, 'blocked', False)]
        if blocked:
            bits.append('blocked for ' + ', '.join(str(b) for b in blocked))
    feedback = getattr(resp, 'prompt_feedback', None)
    block = getattr(feedback, 'block_reason', None) if feedback else None
    if block:
        bits.append(f'the prompt itself was blocked ({getattr(block, "name", block)})')
    return ', '.join(bits) if bits else 'no reason was given. Try again.' 


@app.route('/api/generate/image', methods=['POST'])
def api_generate_image():
    """Generate a photorealistic image of a fictional person via Google Imagen.
    Reuse the same `appearance` text across shots to keep the same person."""
    me = _current_user()
    used, limit = _image_quota(me)
    if limit is not None and used >= limit:
        return _cap_denied('image_generations_month', me,
                           {'used': used, 'limit': limit})

    # The route has several success returns and a failed generation should not
    # cost the creator an allowance, so the meter runs on the way out and only
    # when an image actually came back.
    if limit is not None:
        @after_this_request
        def _meter(response):
            try:
                if (response.get_json(silent=True) or {}).get('ok') is True:
                    from db import bump_usage
                    sdb = _db_session()
                    try:
                        bump_usage(sdb, _workspace_id(me), 'image_generations',
                                   _usage_period())
                    finally:
                        sdb.close()
            except Exception:
                logger.exception('image usage metering failed')
            return response

    if client is None:
        return jsonify({'ok': False, 'error': 'Gemini not configured.'}), 200

    data = request.json or {}
    cfg = data.get('config') or {}
    appearance = (data.get('appearance') or _appearance_from_config(cfg)).strip()
    shot = (data.get('shot') or 'portrait').strip().lower()
    framing = _SHOT_FRAMING.get(shot, _SHOT_FRAMING['portrait'])
    nsfw_shot = shot in _NSFW_SHOTS

    import base64
    reference = data.get('reference')  # optional data URL of an existing photo

    # A stored photo can be referenced by id; the browser only holds its URL,
    # not the bytes, so the image is loaded here.
    ref_media_id = data.get('reference_media')
    ref_slug = data.get('reference_slug')
    if ref_media_id and not reference:
        try:
            from db import SessionLocal, get_persona_media
            s = SessionLocal()
            try:
                row = get_persona_media(s, ref_media_id)
                if row and (not ref_slug or row.slug == ref_slug):
                    reference = row.image_data
            finally:
                s.close()
        except Exception:
            logger.warning('reference_media lookup failed for %s', ref_media_id)
        if not reference:
            return jsonify({'ok': False,
                            'error': 'Could not load the reference photo.'}), 200

    # A requested-but-unusable reference must not silently fall through to a
    # fresh face: that returns a different person, which is never what was asked.
    if reference and not (isinstance(reference, str) and reference.startswith('data:')):
        logger.warning('generate/image got an unusable reference (%s)',
                       str(reference)[:60])
        return jsonify({'ok': False,
                        'error': 'The reference photo could not be read.'}), 200

    # Optional outfit lock: keeps clothing, place and lighting identical across
    # a set, so the shots read as one moment rather than five separate days.
    outfit = data.get('outfit') or {}
    outfit_bits = []
    if outfit.get('clothing'):
        outfit_bits.append(f"wearing exactly {outfit['clothing']}")
    if outfit.get('location'):
        outfit_bits.append(f"in the same place: {outfit['location']}")
    if outfit.get('lighting'):
        outfit_bits.append(f"{outfit['lighting']} lighting")
    if outfit_bits:
        outfit_clause = ' She must be ' + ', '.join(outfit_bits) + '. '
    else:
        # Nothing described, so the reference image is the only spec there is.
        outfit_clause = (' Copy the clothing, hairstyle, location and lighting '
                         'exactly as they appear in the reference image. ')

    try:
        # With a reference photo, use the Gemini image model to keep the SAME
        # person across shots. Without one, generate a fresh face via Imagen.
        if reference and isinstance(reference, str) and reference.startswith('data:'):
            head, b64 = reference.split(',', 1)
            ref_mime = head.split(';')[0].replace('data:', '') or 'image/png'
            edit_prompt = (
                f"Generate a new photorealistic photo of the exact same woman shown in the reference image — "
                f"identical face, hair and features — now as {framing}."
                + outfit_clause +
                "Keep her identity, clothing and surroundings perfectly consistent with the reference, "
                "as if taken in the same session minutes apart — only the pose and framing change. "
                "Realistic, natural lighting. "
                + ("Tasteful boudoir aesthetic, alluring but not explicit. "
                   if nsfw_shot else "Instagram aesthetic. ")
                + "Fictional AI-generated person."
            )
            # The image model returns nothing at all often enough that a single
            # attempt is unreliable, so retry the retryable reasons before
            # telling the creator it failed.
            resp = None
            for attempt in range(IMAGE_ATTEMPTS):
                resp = client.models.generate_content(
                    model=os.getenv('GEMINI_IMAGE_MODEL', 'gemini-2.5-flash-image'),
                    # Image first: the reference is the subject, the text only says
                    # what to change about it. Leading with text invites the model
                    # to treat the prompt as a fresh generation and ignore the photo.
                    contents=[{'role': 'user', 'parts': [
                        {'inline_data': {'mime_type': ref_mime, 'data': b64}},
                        {'text': edit_prompt},
                    ]}],
                )
                for part in _gemini_image_parts(resp):
                    inline = getattr(part, 'inline_data', None)
                    if inline and getattr(inline, 'data', None):
                        raw = inline.data
                        mime = getattr(inline, 'mime_type', None) or 'image/png'
                        b = raw if isinstance(raw, (bytes, bytearray)) else base64.b64decode(raw)
                        durl = f"data:{mime};base64," + base64.b64encode(b).decode()
                        if attempt:
                            logger.info('reference image succeeded on attempt %d', attempt + 1)
                        return jsonify({'ok': True, 'image': durl,
                                        'appearance': appearance,
                                        'used_reference': True})
                reason = _gemini_finish_reason(resp)
                logger.warning('reference image attempt %d/%d produced nothing (%s)',
                               attempt + 1, IMAGE_ATTEMPTS, reason or 'no reason')
                if reason not in _RETRYABLE_IMAGE_REASONS:
                    break  # a real refusal — retrying just wastes a call
                if attempt < IMAGE_ATTEMPTS - 1:
                    time.sleep(0.8 * (attempt + 1))
            return jsonify({'ok': False,
                            'error': 'No image came back from the reference photo — '
                                     + _gemini_block_reason(resp)}), 200

        style_bits = (
            "Tasteful boudoir aesthetic, soft flattering light, alluring but not explicit."
            if nsfw_shot else
            "Modern Instagram aesthetic, attractive, friendly expression."
        )
        prompt = (
            f"Photorealistic {framing} of {appearance}. "
            "Natural lighting, realistic skin texture and detail. "
            f"{style_bits} "
            "This is a fictional, AI-generated person who does not exist in real life."
        )
        resp = client.models.generate_images(
            model=os.getenv('IMAGEN_MODEL', 'imagen-4.0-fast-generate-001'),
            prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio='3:4',
                person_generation='ALLOW_ADULT',
                # Widened a notch for the suggestive shots, not disabled —
                # Imagen still refuses anything it reads as explicit either way.
                safety_filter_level=(
                    'BLOCK_MEDIUM_AND_ABOVE' if nsfw_shot else 'BLOCK_LOW_AND_ABOVE'),
            ),
        )
        gen = (resp.generated_images or [None])[0]
        if not gen or not getattr(gen, 'image', None):
            msg = ('No image returned — the model likely filtered this shot. '
                   'Try a different one, or upload your own photo instead.'
                   if nsfw_shot else
                   'No image returned (it may have been filtered).')
            return jsonify({'ok': False, 'error': msg}), 200
        img = gen.image
        raw = getattr(img, 'image_bytes', None)
        mime = getattr(img, 'mime_type', None) or 'image/png'
        if not raw:
            return jsonify({'ok': False, 'error': 'Empty image data.'}), 200
        durl = f"data:{mime};base64," + base64.b64encode(raw).decode()
        # used_reference False means this is a brand-new face, not the same person.
        return jsonify({'ok': True, 'image': durl, 'appearance': appearance,
                        'used_reference': False})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:300]}), 200


@app.route('/api/generate/backstory', methods=['POST'])
def api_generate_backstory():
    if client is None:
        return jsonify({'ok': False, 'error': 'Gemini not configured.'}), 200
    data = request.json or {}
    name = data.get('name', 'the persona')
    age = data.get('age', '')
    location = data.get('location', '')
    archetype = data.get('archetype', '')
    interests = data.get('interests', '')

    age_int = int(age) if str(age).isdigit() else 0
    if age_int and age_int <= 24:
        age_job_rule = (
            f"IMPORTANT: {name} is {age} years old. At this age, people are typically students or recent school leavers. "
            "Give them a realistic student side-job: waiter, barista, supermarket cashier, dishwasher, retail assistant, delivery driver, babysitter, or similar. "
            "They do NOT have a career, a full-time office job, or a management role. They might be studying something specific at uni or college."
        )
    elif age_int and age_int <= 28:
        age_job_rule = (
            f"IMPORTANT: {name} is {age} years old. At this age, people are typically finishing a degree, doing a postgrad, "
            "starting a junior/entry-level role in their field, freelancing on the side, or running a very small venture like an Etsy shop or local service. "
            "They are NOT senior, not managing teams, not running established businesses."
        )
    else:
        age_job_rule = ""

    system = (
        "You write vivid, realistic character backstories for AI chatbot personas. "
        "Output ONLY the backstory — 2-4 sentences in third person. Plain prose, no JSON, no headings. "
        "Make the life situation feel genuinely believable for the person's age."
    )
    prompt = (
        (age_job_rule + "\n\n" if age_job_rule else "")
        + f"Write a unique, specific backstory for a persona named {name}"
        + (f", age {age}" if age else "")
        + (f", from {location}" if location else "")
        + (f", with a {archetype} personality" if archetype else "")
        + (f". Interests: {interests}" if interests else "")
        + ". Give them a memorable real-life detail and a subtle reason they're on this platform. "
        "If any details are missing, invent plausible ones that fit the age. Keep it to 2-4 sentences."
    )
    try:
        resp = client.models.generate_content(
            model=MODEL_NAME,
            contents=[{'role': 'user', 'parts': [{'text': prompt}]}],
            config=types.GenerateContentConfig(system_instruction=system, temperature=1.0),
        )
        return jsonify({'ok': True, 'text': (resp.text or '').strip()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 200


@app.route('/api/generate/conversion-triggers', methods=['POST'])
def api_generate_conversion_triggers():
    if client is None:
        return jsonify({'ok': False, 'error': 'Gemini not configured.'}), 200
    data = request.json or {}
    name = data.get('name', 'the persona')
    archetype = data.get('archetype', '')
    flirt_pace = data.get('flirt_pace', 'moderate')
    nsfw_level = data.get('nsfw_level', 'suggestive')
    system = (
        "You write short, practical conversion trigger instructions for an AI chatbot persona on a fan platform. "
        "Output ONLY the instructions — 2-3 sentences. No preamble, no headers."
    )
    prompt = (
        f"Write conversion trigger instructions for {name} ({archetype} archetype, "
        f"flirt pace: {flirt_pace}, content level: {nsfw_level}). "
        "Instructions should tell the AI when and how to naturally introduce her paid "
        "offer — tied to emotional moments in the conversation, never pushy, always "
        "in-character. Never use the word PPV or any bracketed marker: the offer is "
        "worded in her own voice, and what gets attached is decided outside the text."
    )
    try:
        resp = client.models.generate_content(
            model=MODEL_NAME,
            contents=[{'role': 'user', 'parts': [{'text': prompt}]}],
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.7),
        )
        return jsonify({'ok': True, 'text': (resp.text or '').strip()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 200

@app.route('/api/generate/interests', methods=['POST'])
def api_generate_interests():
    if client is None:
        return jsonify({'ok': False, 'error': 'Gemini not configured.'}), 200
    data = request.json or {}
    name = data.get('name', 'the persona')
    age = data.get('age', '')
    archetype = data.get('archetype', '')
    backstory = data.get('backstory', '')
    randomize = data.get('randomize', False)
    system = (
        "You generate comma-separated interest topics for an AI chatbot persona on a fan platform. "
        "Output ONLY a comma-separated list of 6-10 topics. No preamble, no explanation."
    )
    prompt = (
        f"{'Randomly generate' if randomize else 'Generate'} a comma-separated list of interests for "
        f"{name}, {age}, archetype: {archetype}. Backstory: {backstory}. "
        "Keep it down-to-earth and relatable — things a normal person actually spends time on: shows, "
        "food, workouts, music, pets, travel, hobbies, sports, beauty, family, her city. At most one or "
        "two can lean intellectual or niche if it fits the backstory — don't make the whole list read "
        "like a resume. Examples of format: reality TV, iced coffee, her dog, hot yoga, k-pop, tattoos, "
        "true crime podcasts, thrifting"
    )
    try:
        resp = client.models.generate_content(
            model=MODEL_NAME,
            contents=[{'role': 'user', 'parts': [{'text': prompt}]}],
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.9 if randomize else 0.7),
        )
        return jsonify({'ok': True, 'text': (resp.text or '').strip()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 200

@app.route('/api/platforms/overview')
def api_platforms_overview():
    """Per-persona connection state across every platform, for the creator's
    own personas only. One owner check here replaces four fan-out calls."""
    user = _current_user()
    if _is_operator():
        personas = db_list_personas()
    elif user:
        personas = db_list_personas(owner_id=_workspace_id(user))
    else:
        personas = []
    tg_bots = _tg_load_bots()
    tg_plat = _tg_platform()
    tgu_accounts = _tgu_accounts()
    tgu_ready = all(_tgu_app_creds())
    x_tokens = _load_x_tokens() or {}
    threads_tokens = _threads_load_tokens() or {}
    out = {}
    for p in personas:
        slug = p['slug']
        bot = tg_bots.get(slug) or {}
        acct = tgu_accounts.get(slug) or {}
        hosted = bot.get('mode') == 'hosted'
        tg_user = tg_plat.get('username', '') if hosted else bot.get('username', '')
        xt = x_tokens.get(slug) or {}
        th = threads_tokens.get(slug) or {}
        out[slug] = {
            'name': p.get('name') or slug,
            'telegram': ({
                # A personal account and a bot are mutually exclusive per model,
                # and the personal account is the stronger claim on the slug.
                'connected': True, 'mode': 'user',
                'username': acct.get('username', '') or acct.get('first_name', ''),
                'share_link': '',
            } if acct.get('session') else {
                'connected': bool(bot.get('bot_token')) or (hosted and bool(tg_plat.get('bot_token'))),
                'mode': bot.get('mode', ''),
                'username': tg_user,
                'share_link': _tg_share_link({**bot, 'username': tg_user}) if bot else '',
            }),
            'x': {'connected': bool(xt.get('access_token')),
                  'username': xt.get('username', '')},
            'fanvue': {'connected': bool(_fanvue_tokens(slug).get('access_token')),
                       'username': ''},
            'threads': {'connected': bool(th.get('access_token')),
                        'username': th.get('username', '')},
        }
    return jsonify({'personas': out,
                    'telegram_platform_ready': bool(tg_plat.get('bot_token')),
                    'telegram_user_ready': tgu_ready})


@app.route('/api/personas/<slug>/avatar')
def api_persona_avatar(slug):
    """Return the persona's avatar image from the config."""
    import base64
    from flask import Response
    saved = db_get_persona(slug)
    if saved:
        cfg = saved['config']
    else:
        config_path = _persona_path(slug, '.config.json')
        cfg = {}
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
    avatar = cfg.get('avatar', '')
    # Fall back to the first gallery image so personas with photos get a profile pic.
    if not avatar or not avatar.startswith('data:'):
        imgs = db_get_images(slug)
        if imgs:
            avatar = imgs[0]
    if not avatar or not avatar.startswith('data:'):
        return ('', 404)
    try:
        header, b64 = avatar.split(',', 1)
        mime = header.split(';')[0].replace('data:', '') or 'image/jpeg'
        data = base64.b64decode(b64)
        return Response(data, mimetype=mime)
    except Exception:
        return ('', 400)


def _serve_data_url(data_url):
    """Serve a data: URL as an image Response, or 404/400."""
    import base64
    from flask import Response
    if not data_url or not data_url.startswith('data:'):
        return ('', 404)
    try:
        header, b64 = data_url.split(',', 1)
        mime = header.split(';')[0].replace('data:', '') or 'image/jpeg'
        return Response(base64.b64decode(b64), mimetype=mime)
    except Exception:
        return ('', 400)


@app.route('/api/personas/<slug>/images', methods=['GET'])
def api_persona_images(slug):
    """List a persona's gallery images. ?full=1 returns the raw data URLs
    (for the dashboard editor); otherwise per-image endpoint URLs."""
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    imgs = db_get_images(slug)
    if request.args.get('full') == '1':
        return jsonify({'count': len(imgs), 'images': imgs})
    return jsonify({'count': len(imgs), 'images': [f'/api/personas/{slug}/image/{i}' for i in range(len(imgs))]})


@app.route('/api/personas/<slug>/image/<int:idx>')
def api_persona_image(slug, idx):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return ('', 400)
    imgs = db_get_images(slug)
    if idx < 0 or idx >= len(imgs):
        return ('', 404)
    return _serve_data_url(imgs[idx])


@app.route('/api/personas/<slug>/images', methods=['POST'])
def api_persona_images_save(slug):
    """Save a persona's images to the DB (max 5). Allowed for originals too —
    photos are an overlay and don't touch the read-only original config."""
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    data = request.json or {}
    images = data.get('images', [])
    if not isinstance(images, list):
        return jsonify({'error': 'images must be a list'}), 400
    saved = db_set_images(slug, images)
    return jsonify({'ok': True, 'count': len(saved)})


# Same shape as the gallery above, entirely separate storage — these never
# reach a normal send, only _chat_nsfw_photo at the CTA moment.
@app.route('/api/personas/<slug>/nsfw-images', methods=['GET'])
def api_persona_nsfw_images(slug):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    imgs = db_get_nsfw_images(slug)
    if request.args.get('full') == '1':
        return jsonify({'count': len(imgs), 'images': imgs})
    return jsonify({'count': len(imgs), 'images': [f'/api/personas/{slug}/nsfw-image/{i}' for i in range(len(imgs))]})


@app.route('/api/personas/<slug>/nsfw-image/<int:idx>')
def api_persona_nsfw_image(slug, idx):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return ('', 400)
    imgs = db_get_nsfw_images(slug)
    if idx < 0 or idx >= len(imgs):
        return ('', 404)
    return _serve_data_url(imgs[idx])


@app.route('/api/personas/<slug>/nsfw-images', methods=['POST'])
def api_persona_nsfw_images_save(slug):
    """Save a persona's NSFW images to the DB (max 5)."""
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    data = request.json or {}
    images = data.get('images', [])
    if not isinstance(images, list):
        return jsonify({'error': 'images must be a list'}), 400
    saved = db_set_nsfw_images(slug, images)
    return jsonify({'ok': True, 'count': len(saved)})


# ── Tagged media library ──────────────────────────────────────────────────────

OUTFIT_COUNT = 6


def _outfits(slug):
    """The persona's six outfit definitions. Each outfit is one consistent look:
    same clothing, same place, same setting — so every photo tagged to it is
    visually coherent and can be sent as if shot in one sitting."""
    try:
        saved = json.loads(_get_setting(f'outfits_{slug}') or '[]')
    except Exception:
        saved = []
    out = []
    for i in range(OUTFIT_COUNT):
        s = saved[i] if i < len(saved) and isinstance(saved[i], dict) else {}
        out.append({
            'n': i + 1,
            'name': str(s.get('name', ''))[:80],
            'clothing': str(s.get('clothing', ''))[:120],
            'location': str(s.get('location', ''))[:120],
            'lighting': str(s.get('lighting', ''))[:60],
        })
    return out


@app.route('/api/personas/<slug>/outfits', methods=['GET'])
def api_persona_outfits(slug):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    return jsonify({'outfits': _outfits(slug)})


@app.route('/api/personas/<slug>/outfits', methods=['POST'])
def api_persona_outfits_save(slug):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    me = _current_user()
    if not user_capabilities(me).get('outfit_lock'):
        return _cap_denied('outfit_lock', me)
    data = request.json or {}
    items = data.get('outfits', [])
    if not isinstance(items, list):
        return jsonify({'error': 'outfits must be a list'}), 400
    clean = []
    for i in range(OUTFIT_COUNT):
        s = items[i] if i < len(items) and isinstance(items[i], dict) else {}
        clean.append({
            'name': str(s.get('name', ''))[:80],
            'clothing': str(s.get('clothing', ''))[:120],
            'location': str(s.get('location', ''))[:120],
            'lighting': str(s.get('lighting', ''))[:60],
        })
    _set_setting(f'outfits_{slug}', json.dumps(clean))
    return jsonify({'ok': True, 'outfits': _outfits(slug)})


# ── Chat phases ──────────────────────────────────────────────────────────────

DEFAULT_PHASES = [
    {'name': 'Phase 1', 'duration_type': 'exchanges', 'duration_value': 20,
     'interest': 'low', 'photo_rate': 40},
    {'name': 'CTA Phase', 'duration_type': 'exchanges', 'duration_value': 0,
     'interest': 'high', 'photo_rate': 60},
]


def _phases(slug):
    try:
        saved = json.loads(_get_setting(f'phases_{slug}') or '[]')
        if isinstance(saved, list) and saved:
            # Saves require at least 2 phases, but personas persisted before that
            # rule (or edited directly) can still carry just one. Pad rather than
            # serve a state the save endpoint would itself reject.
            if len(saved) < 2:
                saved = saved + DEFAULT_PHASES[len(saved):2]
            return saved
    except Exception:
        pass
    return list(DEFAULT_PHASES)


def _clean_phase(p):
    return {
        'name': str(p.get('name', ''))[:60],
        'duration_type': p.get('duration_type', 'exchanges') if p.get('duration_type') in ('exchanges', 'days', 'exchanges_and_days') else 'exchanges',
        'duration_value': max(0, int(p.get('duration_value', 0))),
        'duration_days': max(0, int(p.get('duration_days', 0))),
        # No 'interest' key: it was validated here but never read by the prompt
        # builder, so creators were setting a control that did nothing. Existing
        # saved phases still carry it; it is simply dropped on the next save.
        'photo_rate': max(0, min(100, int(p.get('photo_rate', 20)))),
    }


def _phases_cta(slug):
    try:
        raw = json.loads(_get_setting(f'phases_cta_{slug}') or '{}')
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    bot = _tg_load_bots().get(slug) or {}
    return {'cta_url': bot.get('cta_url', ''), 'cta_label': bot.get('cta_label', '')}


# She mirrors the fan's language (see _language_block), so an English-only
# trigger meant a Dutch or German fan asking for the link was never heard.
_CTA_ASK_RE = re.compile(
    r'\b('
    r'fanvue|fan\s?vue|onlyfans|only\s?fans|\bof\s?page\b|patreon|'
    r'subscri\w*|premium|paywall|'
    r'abonnement|abonnieren|abonament|suscri\w*|assinatura|abbonamento|'
    r'linkje|enlace|lien|collegamento|'
    r'(where|how)\s+(else\s+)?(can|do)\s+i\s+(find|see|follow|get)\s+(you|more)|'
    r'(other|another|different)\s+(page|site|platform|account)|'
    r'(send|share|got|have|drop)\s+(me\s+)?(a\s+|the\s+|your\s+)?link|'
    r'link\s+(to|for)\b|'
    r'(your|the)\s+(cta|link|page|profile)|'
    # waar kan/vind ik je / meer van jou — Dutch
    r'waar\s+(kan|vind|zie)\s+ik\s+(je|jou|jullie|meer)|meer\s+van\s+(je|jou)|'
    r'(stuur|geef|deel)\s+(me\s+|mij\s+)?(je\s+|de\s+|een\s+)?link|'
    # wo finde ich / schick mir den link — German
    r'wo\s+(finde|sehe)\s+ich\s+(dich|mehr)|(schick|gib)\s+mir\s+(den\s+|deinen\s+)?link|'
    # où puis-je te trouver / ton lien — French
    r'o\u00f9\s+(puis-je|est-ce que je peux)\s+te\s+(trouver|suivre)|ton\s+lien|'
    # dónde puedo verte / tu enlace — Spanish / Portuguese
    r'd\u00f3nde\s+puedo\s+(verte|encontrarte|seguirte)|tu\s+(enlace|link)|'
    r'onde\s+(posso|eu)\s+(te\s+)?(ver|achar|encontrar)|seu\s+link|'
    # dove posso trovarti / il tuo link — Italian
    r'dove\s+posso\s+(trovarti|seguirti|vederti)|il\s+tuo\s+link'
    r')\b', re.I)


def _cta_asked(text):
    """True when the fan is asking where else to find her / for the link, so the
    CTA goes out on request instead of waiting for the final phase."""
    return bool(_CTA_ASK_RE.search(text or ''))


def _cta_due(persona, text, fan, is_cta_phase, cta_url, spicy_count=0):
    """Whether the CTA link goes out with this reply.

    Asking where else to find her always overrides the phase gate. Asking to see
    explicit content does too, when the creator set spicy_cta to fast/instant —
    on "instant" it also overrides the once-only gate, so a fan who keeps asking
    keeps being pointed at the page.

    spicy_count is how many times the fan has asked for explicit content so
    far, this message included — 0 unless the caller tracks it (currently
    only the browser chat does, via _spicy_ask_count). Once it reaches the
    creator's threshold the CTA fires too, on top of whatever spicy_cta is
    set to: a fan who keeps asking is a strong signal on its own, separate
    from the fast/instant single-shot modes above.
    """
    if not cta_url:
        return False
    if _cta_asked(text):
        return True
    cfg = load_persona_config(persona)
    mode = cfg.get('spicy_cta', 'normal')
    if mode in ('fast', 'instant') and _spicy_asked(text):
        return mode == 'instant' or not fan.get('cta_sent')
    threshold = cfg.get('spicy_release_after') or 0
    if threshold and spicy_count >= threshold and not fan.get('cta_sent'):
        return True
    return not fan.get('cta_sent') and is_cta_phase


@app.route('/api/personas/<slug>/phases', methods=['GET'])
def api_persona_phases(slug):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    cta = _phases_cta(slug)
    return jsonify({'phases': _phases(slug), 'cta_url': cta.get('cta_url', ''),
                    'cta_label': cta.get('cta_label', '')})


@app.route('/api/personas/<slug>/phases', methods=['POST'])
def api_persona_phases_save(slug):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    data = request.json or {}
    items = data.get('phases', [])
    if not isinstance(items, list) or len(items) < 2:
        return jsonify({'error': 'At least 2 phases required'}), 400
    cap = user_capabilities(_current_user()).get('phases_max')
    limit = 10 if cap is None else min(10, int(cap))
    clean = [_clean_phase(p) for p in items[:limit]]
    _set_setting(f'phases_{slug}', json.dumps(clean))
    cta = {
        'cta_url': str(data.get('cta_url', ''))[:500].strip(),
        'cta_label': str(data.get('cta_label', ''))[:120].strip(),
    }
    _set_setting(f'phases_cta_{slug}', json.dumps(cta))
    bots = _tg_load_bots()
    if slug in bots:
        bots[slug]['cta_url'] = cta['cta_url']
        bots[slug]['cta_label'] = cta['cta_label']
        _tg_save_bots(bots)
    return jsonify({'ok': True, 'phases': _phases(slug)})


def _fan_phase(phases, fan):
    """Determine which phase index a fan is in based on exchange count and days."""
    exchanges = int(fan.get('in_count', 0))
    first_ts = int(fan.get('first_in', 0))
    days = 0
    if first_ts:
        days = max(0, (int(time.time()) - first_ts)) // 86400
    total_ex = 0
    total_days = 0
    for i, ph in enumerate(phases):
        dv = ph.get('duration_value', 0)
        dd = ph.get('duration_days', 0)
        dt = ph.get('duration_type', 'exchanges')
        if i == len(phases) - 1:
            return i
        if dt == 'exchanges':
            if dv <= 0 or exchanges < total_ex + dv:
                return i
            total_ex += dv
        elif dt == 'days':
            if dd <= 0 or days < total_days + dd:
                return i
            total_days += dd
        else:  # exchanges_and_days
            if (dv <= 0 or exchanges < total_ex + dv) and (dd <= 0 or days < total_days + dd):
                return i
            total_ex += dv
            total_days += dd
    return len(phases) - 1


def _fan_sent_photos(persona, chat_id):
    """Set of media IDs already sent to this fan."""
    try:
        raw = json.loads(_get_setting(f'sent_photos_{persona}_{chat_id}') or '[]')
        return set(raw) if isinstance(raw, list) else set()
    except Exception:
        return set()


def _fan_record_sent_photo(persona, chat_id, media_id):
    sent = _fan_sent_photos(persona, chat_id)
    sent.add(media_id)
    _set_setting(f'sent_photos_{persona}_{chat_id}', json.dumps(list(sent)))


OUTFIT_LOCK_SECONDS = 7200  # 2 hours before switching outfit


def _fan_outfit_lock(persona, chat_id):
    """Return (locked_outfit_number, lock_timestamp) or (None, 0)."""
    try:
        raw = json.loads(_get_setting(f'outfit_lock_{persona}_{chat_id}') or '{}')
        outfit = raw.get('outfit')
        ts = int(raw.get('ts', 0))
        if outfit and (int(time.time()) - ts) < OUTFIT_LOCK_SECONDS:
            return int(outfit), ts
    except Exception:
        pass
    return None, 0


def _fan_set_outfit_lock(persona, chat_id, outfit_num):
    _set_setting(f'outfit_lock_{persona}_{chat_id}',
                 json.dumps({'outfit': outfit_num, 'ts': int(time.time())}))


def _pick_phase_photo(media_rows, outfits, sent_ids, locked_outfit=None):
    """Pick a random photo, respecting outfit lock and avoiding duplicates."""
    available = [r for r in media_rows if r.id not in sent_ids]
    if not available:
        return None
    if locked_outfit is not None:
        outfit_avail = [r for r in available
                        if _safe_outfit_num(r) == locked_outfit]
        if outfit_avail:
            available = outfit_avail
    hour = datetime.now(timezone.utc).hour
    if 6 <= hour < 12:
        tod = 'Day time'
    elif 12 <= hour < 17:
        tod = 'Day time'
    elif 17 <= hour < 21:
        tod = 'Golden hour'
    else:
        tod = 'Night time'
    scored = []
    for r in available:
        s = 1
        o = _outfit_of(r, outfits)
        if o and o.get('lighting', '').lower() == tod.lower():
            s += 2
        if locked_outfit is None:
            outfit_day = (datetime.now(timezone.utc).toordinal() % OUTFIT_COUNT) + 1
            if _safe_outfit_num(r) == outfit_day:
                s += 3
        scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    top = [x for x in scored if x[0] == scored[0][0]]
    return random.choice(top)[1]


def _safe_outfit_num(row):
    try:
        return int(row.outfit)
    except (TypeError, ValueError):
        return None


@app.route('/api/personas/<slug>/media', methods=['GET'])
def api_persona_media_list(slug):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    from db import SessionLocal, list_persona_media, list_media_links
    outfits = _outfits(slug)
    s = SessionLocal()
    try:
        rows = list_persona_media(s, slug)
        links = list_media_links(s, slug)
        by_media = {}
        for l in links:
            by_media.setdefault(l.media_id, []).append(l.outfit)

        vault = []
        for r in rows:
            o = _outfit_of(r, outfits)
            vault.append({
                'id': r.id, 'purpose': r.purpose or '',
                'outfits': by_media.get(r.id, []),
                'outfit': r.outfit or '',        # legacy, kept during migration
                'location': (o or {}).get('location', ''),
                'lighting': (o or {}).get('lighting', ''),
                'thumb': f'/api/personas/{slug}/media/{r.id}/image',
            })

        # One entry per placement, which is what the outfit strips render.
        placements = [{'media_id': l.media_id, 'outfit': l.outfit,
                       'position': l.position or 0} for l in links]
        # `items` keeps the old shape so anything still reading it works.
        items = [dict(v, outfit=(v['outfits'][0] if v['outfits'] else ''))
                 for v in vault]
        return jsonify({'items': items, 'vault': vault,
                        'links': placements, 'outfits': outfits})
    finally:
        s.close()


def _outfit_of(row, outfits):
    """Resolve a media row's outfit number to its definition, or None."""
    try:
        n = int(row.outfit)
    except (TypeError, ValueError):
        return None
    if 1 <= n <= len(outfits):
        return outfits[n - 1]
    return None


@app.route('/api/personas/<slug>/media', methods=['POST'])
def api_persona_media_save(slug):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    data = request.json or {}
    image = data.get('image', '')
    if not image or not image.startswith('data:'):
        return jsonify({'error': 'image must be a data URL'}), 400
    from db import SessionLocal, PersonaMedia
    s = SessionLocal()
    try:
        row = PersonaMedia(
            slug=slug, image_data=image,
            location=str(data.get('location', ''))[:120],
            outfit=str(data.get('outfit', ''))[:120],
            lighting=str(data.get('lighting', ''))[:60],
            purpose=str(data.get('purpose', ''))[:60],
        )
        s.add(row)
        s.flush()
        # Uploading straight into an outfit also places it there. Without an
        # outfit the photo simply lands in the vault, unassigned.
        if row.outfit:
            from db import link_media_to_outfit
            link_media_to_outfit(s, slug, row.id, row.outfit)
        s.commit()
        return jsonify({'ok': True, 'id': row.id})
    finally:
        s.close()


@app.route('/api/personas/<slug>/media/reorder', methods=['POST'])
def api_persona_media_reorder(slug):
    """Persist the outfit layout after a drag.
    Body: {order: [{media_id, outfit}, ...]} in display order."""
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    order = (request.json or {}).get('order')
    if not isinstance(order, list):
        return jsonify({'error': 'order must be a list'}), 400
    # Accept the older {id, outfit} shape too.
    entries = [{'media_id': e.get('media_id') or e.get('id'),
                'outfit': e.get('outfit', '')} for e in order]
    from db import SessionLocal, reorder_media_links, sync_legacy_outfit
    s = SessionLocal()
    try:
        n = reorder_media_links(s, slug, entries)
        s.flush()
        sync_legacy_outfit(s, [e['media_id'] for e in entries if e.get('media_id')])
        s.commit()
    finally:
        s.close()
    return jsonify({'ok': True, 'updated': n})


@app.route('/api/personas/<slug>/media/link', methods=['POST'])
def api_persona_media_link(slug):
    """Put vault photos into an outfit. Body: {media_ids: [...], outfit: 'N'}"""
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    data = request.json or {}
    outfit = str(data.get('outfit', '')).strip()
    media_ids = data.get('media_ids') or ([data['media_id']] if data.get('media_id') else [])
    if not outfit or not media_ids:
        return jsonify({'error': 'outfit and media_ids are required'}), 400
    from db import (SessionLocal, PersonaMedia, link_media_to_outfit,
                    sync_legacy_outfit)
    s = SessionLocal()
    try:
        added = 0
        for mid in media_ids:
            row = s.get(PersonaMedia, mid)
            if row is None or row.slug != slug:
                continue        # never link another persona's photo
            link_media_to_outfit(s, slug, mid, outfit)
            added += 1
        s.flush()
        sync_legacy_outfit(s, media_ids)
        s.commit()
    finally:
        s.close()
    return jsonify({'ok': True, 'linked': added})


@app.route('/api/personas/<slug>/media/unlink', methods=['POST'])
def api_persona_media_unlink(slug):
    """Take a photo out of an outfit. The photo stays in the vault."""
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    data = request.json or {}
    outfit = str(data.get('outfit', '')).strip()
    media_id = data.get('media_id')
    if not outfit or not media_id:
        return jsonify({'error': 'outfit and media_id are required'}), 400
    from db import SessionLocal, unlink_media_from_outfit, sync_legacy_outfit
    s = SessionLocal()
    try:
        n = unlink_media_from_outfit(s, media_id, outfit)
        s.flush()
        sync_legacy_outfit(s, [media_id])
        s.commit()
    finally:
        s.close()
    return jsonify({'ok': True, 'removed': n})


@app.route('/api/personas/<slug>/media/<media_id>', methods=['PUT'])
def api_persona_media_update(slug, media_id):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    data = request.json or {}
    from db import SessionLocal, get_persona_media
    s = SessionLocal()
    try:
        row = get_persona_media(s, media_id)
        if not row or row.slug != slug:
            return jsonify({'error': 'Not found'}), 404
        for f in ('location', 'outfit', 'lighting', 'purpose'):
            if f in data:
                setattr(row, f, str(data[f])[:120])
        if 'image' in data and data['image'].startswith('data:'):
            row.image_data = data['image']
        s.commit()
        return jsonify({'ok': True})
    finally:
        s.close()


@app.route('/api/personas/<slug>/media/<media_id>', methods=['DELETE'])
def api_persona_media_delete(slug, media_id):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    from db import SessionLocal, delete_persona_media, delete_media_links
    s = SessionLocal()
    try:
        # Links first: a leftover link would point at a photo that is gone.
        delete_media_links(s, media_id)
        row = delete_persona_media(s, media_id)
        if not row or row.slug != slug:
            s.rollback()
            return jsonify({'error': 'Not found'}), 404
        s.commit()
        return jsonify({'ok': True})
    finally:
        s.close()


@app.route('/api/personas/<slug>/media/<media_id>/image')
def api_persona_media_image(slug, media_id):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return ('', 400)
    from db import SessionLocal, get_persona_media
    s = SessionLocal()
    try:
        row = get_persona_media(s, media_id)
        if not row or row.slug != slug:
            return ('', 404)
        return _serve_data_url(row.image_data)
    finally:
        s.close()


@app.route('/api/personas/<slug>/media/pick', methods=['POST'])
def api_persona_media_pick(slug):
    """Pick the best media item for a given context. Used by the chat engine
    to decide which photo to send alongside a message."""
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    data = request.json or {}
    from db import SessionLocal, list_persona_media
    s = SessionLocal()
    try:
        rows = list_persona_media(s, slug)
        if not rows:
            return jsonify({'match': None})
        picked = _pick_media(rows, outfits=_outfits(slug),
                             purpose=data.get('purpose', ''),
                             lighting=data.get('lighting', ''),
                             location=data.get('location', ''),
                             outfit=data.get('outfit', ''))
        if not picked:
            return jsonify({'match': None})
        return jsonify({'match': {
            'id': picked.id,
            'url': f'/api/personas/{slug}/media/{picked.id}/image',
        }})
    finally:
        s.close()


def _pick_media(rows, outfits=None, purpose='', lighting='', location='', outfit='', **_ignored):
    """Score media items against requested tags; highest match wins.

    Lighting and location live on the outfit, not the photo, so a photo inherits
    whatever its outfit defines. Unknown tag keys are ignored rather than raising —
    the tags come from the model, so a stray key must not break the send."""
    outfits = outfits or []
    want_outfit = str(outfit).strip().lower().replace('outfit ', '')
    best, best_score = None, -1
    for r in rows:
        o = _outfit_of(r, outfits) or {}
        score = 0
        if purpose and r.purpose and r.purpose.lower() == purpose.lower():
            score += 4
        if want_outfit and str(r.outfit or '').strip() == want_outfit:
            score += 3
        if lighting and o.get('lighting') and o['lighting'].lower() == lighting.lower():
            score += 2
        if location and o.get('location') and location.lower() in o['location'].lower():
            score += 2
        if score > best_score:
            best, best_score = r, score
    return best if best_score > 0 else None


# ── Backstory AI interview ────────────────────────────────────────────────────

BACKSTORY_TOPICS = [
    {'label': 'work or job',
     'detail': 'how they earn money day to day'},
    {'label': 'hobbies',
     'detail': 'what they do for fun, not related to work or study'},
    {'label': 'social life and free time',
     'detail': 'who they spend time with and where they go'},
    {'label': 'study or education background',
     'detail': 'what they studied or are studying, and how they feel about it'},
    {'label': 'personality quirks',
     'detail': 'a specific habit, fear, or contradiction that makes them memorable'},
]


@app.route('/api/backstory/interview', methods=['POST'])
def api_backstory_interview():
    """Drive an interactive backstory interview via Gemini.

    Body: { basics: {name, age, archetype, location}, messages: [{role, content}],
            action: 'ask' | 'finalize' }
    - action 'ask': returns the next single question (or a check-in offering to
      finalize or go deeper once the basics are covered).
    - action 'finalize': writes the final backstory from everything gathered.
    """
    if client is None:
        return jsonify({'ok': False, 'error': 'Gemini is not configured. Add an API key in Settings first.'}), 200

    data = request.json or {}
    basics = data.get('basics', {})
    messages = data.get('messages', [])
    action = data.get('action', 'ask')

    name = basics.get('name') or 'the persona'
    age = basics.get('age', '')
    archetype = basics.get('archetype', '')
    location = basics.get('location', '')

    age_int = int(age) if str(age).isdigit() else 0
    if age_int and age_int <= 24:
        age_job_context = (
            f"AGE RULE: {name} is {age}. Options for work/study must reflect this. "
            "Realistic choices: student with a side-job (waiter, barista, supermarket cashier, dishwasher, retail worker, delivery driver, babysitter). "
            "Do NOT suggest full-time careers, office jobs, or management roles."
        )
    elif age_int and age_int <= 28:
        age_job_context = (
            f"AGE RULE: {name} is {age}. Options for work/study must reflect this. "
            "Realistic choices: finishing a degree or postgrad, first junior/entry-level job in their field, "
            "freelancing on the side, running a tiny venture (Etsy shop, local gigs). "
            "Do NOT suggest senior roles or established businesses."
        )
    else:
        age_job_context = ""

    # The topic sequence is tracked server-side. Left to the model it loses its
    # place — especially once answers are "(skipped)" — and re-asks a topic it
    # has already covered, reworded.
    asked = []
    for msg in messages:
        if msg.get('role') != 'assistant':
            continue
        try:
            q = json.loads(msg.get('content', '')).get('question')
        except Exception:
            q = None
        if q:
            asked.append(q)

    topic_index = len(asked)
    if action == 'randomize' and topic_index:
        topic_index -= 1        # same topic again, just fresh options
    topic = BACKSTORY_TOPICS[topic_index] if topic_index < len(BACKSTORY_TOPICS) else None

    if asked:
        already = ('\n\nAlready asked — do NOT ask these again, and do NOT ask a '
                   'reworded version of them:\n'
                   + '\n'.join(f'- {q}' for q in asked))
    else:
        already = ''

    if topic:
        this_turn = (
            f'\n\nThis turn ask about ONE topic only: {topic["label"]} '
            f'({topic["detail"]}). Ask nothing outside that topic. '
            f'This is question {topic_index + 1} of {len(BACKSTORY_TOPICS)}.'
        )
    elif topic_index == len(BACKSTORY_TOPICS):
        this_turn = (
            '\n\nAll topics are covered. Output exactly: '
            '{"question": "Got everything I need for a solid backstory. '
            'Generate it now or add more?", '
            '"options": ["Generate now", "Add one more thing", "Keep going"]}'
        )
    else:
        # They chose to keep going past the check-in: ask something genuinely new.
        this_turn = (
            '\n\nThe core topics are done and they asked for more. Ask ONE fresh '
            'question about a detail not covered yet — family, a formative moment, '
            'what they want next, a possession that matters to them. It must not '
            'overlap with anything already asked.'
        )

    interview_system = (
        f"You are building a backstory for an AI chatbot persona named {name}, "
        + (f"age {age}, " if age else "")
        + (f"from {location}, " if location else "")
        + (f"personality archetype: {archetype}." if archetype else ".")
        + ("\n\n" + age_job_context if age_job_context else "")
        + "\n\n"
        "Name, age, and location are already known — do NOT ask about them.\n\n"
        "Rules:\n"
        "- Output ONLY a valid JSON object each turn, nothing else.\n"
        f'- Question format: {{"question": "short conversational question", "options": ["A", "B", "C"]}}\n'
        "- Questions: simple, direct, mid-length. Not too basic (not 'what do you do?'), "
        "not too complex. One sentence.\n"
        "- Options: 3 distinct, specific, realistic answers. "
        f"Tailor them to {archetype} archetype, age {age}, location {location}. "
        "Each option is 4-10 words. No vague options like 'something creative'.\n"
        "- A skipped answer means move on, never re-ask that topic.\n"
        "- Never write the backstory itself unless told to finalize."
        + already + this_turn
    )

    if age_int and age_int <= 24:
        age_finalize_rule = (
            f"IMPORTANT: {name} is {age}. Their job/life situation must be age-realistic: "
            "student with a side-job like waiter, cashier, barista, etc. — NOT a full-time career. "
            "Invent missing details that fit this age bracket."
        )
    elif age_int and age_int <= 28:
        age_finalize_rule = (
            f"IMPORTANT: {name} is {age}. Invent missing details that fit this age: "
            "finishing studies, starting out in a junior role, freelancing, or a small side project."
        )
    else:
        age_finalize_rule = "Invent any missing details that feel realistic and believable."

    finalize_system = (
        f"You are a creative writer. Write a vivid character backstory for {name}"
        + (f", age {age}" if age else "")
        + (f", from {location}" if location else "")
        + (f", with a {archetype} personality" if archetype else "")
        + f". {age_finalize_rule} "
        "Even if only a few answers were given or most were skipped, produce a full, coherent 2-4 sentence backstory in third person. "
        "Fill gaps with realistic invented details. Plain prose only — no JSON, no bullet points, no headings."
    )

    contents = []
    for m in messages:
        role = 'user' if m.get('role') == 'user' else 'model'
        contents.append({'role': role, 'parts': [{'text': m.get('content', '')}]})

    if action == 'finalize':
        system = finalize_system
        # Build a clean Q&A summary to avoid JSON bleed-through from interview history
        qa_lines = []
        pending_q = None
        for msg in messages:
            if msg.get('role') == 'assistant':
                try:
                    parsed = json.loads(msg.get('content', ''))
                    pending_q = parsed.get('question', msg.get('content', ''))
                except Exception:
                    pending_q = msg.get('content', '')
            elif msg.get('role') == 'user' and pending_q:
                answer = msg.get('content', '')
                if answer and answer != '(skipped)':
                    qa_lines.append(f"Q: {pending_q}\nA: {answer}")
                pending_q = None
        summary = '\n\n'.join(qa_lines) if qa_lines else 'No specific details provided.'
        contents = [{'role': 'user', 'parts': [{'text': (
            f"Write a backstory for {name} based on these interview answers:\n\n{summary}\n\n"
            "Plain prose, 2-4 sentences, third person. No JSON, no questions, no headings."
        )}]}]
    else:
        system = interview_system
        if action == 'randomize':
            contents.append({'role': 'user', 'parts': [{'text': (
                'Repeat the same question topic but generate 3 completely different, fresh answer options. '
                'Do not reuse any of the previous options. Output JSON only.'
            )}]})
        elif not contents:
            contents.append({'role': 'user', 'parts': [{'text': 'Ask the first question. Output JSON only.'}]})

    try:
        resp = client.models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.85,
            ),
        )
        text = (resp.text or '').strip()
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 200

    if action == 'finalize':
        return jsonify({'ok': True, 'action': 'finalize', 'text': text})

    # Parse JSON question+options from Gemini
    try:
        # Strip markdown code fences if present
        clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.MULTILINE).strip()
        parsed = json.loads(clean)
        question = parsed.get('question', '')
        options = parsed.get('options', [])
        if not isinstance(options, list):
            options = []
        options = [str(o) for o in options[:3]]
        return jsonify({'ok': True, 'action': 'ask', 'question': question, 'options': options})
    except Exception:
        # Fallback: treat whole text as the question with no options
        return jsonify({'ok': True, 'action': 'ask', 'question': text, 'options': []})


# ── Config API (Gemini API key) ───────────────────────────────────────────────

def _mask_key(key):
    if not key:
        return None
    return (key[:5] + '…' + key[-4:]) if len(key) > 12 else '••••'


@app.route('/api/config')
def api_config_get():
    """Report current Gemini connection status (key never returned in full)."""
    key = os.getenv('GEMINI_API_KEY', '')
    return jsonify({
        'gemini_configured': client is not None,
        'auth_mode': auth_mode,
        'key_masked': _mask_key(key),
        'is_vercel': IS_VERCEL,
    })


@app.route('/api/config/gemini-key', methods=['POST'])
@operator_only
def api_config_set_key():
    """Save a Gemini API key to .env, reload the client, and verify it works."""
    data = request.json or {}
    api_key = (data.get('api_key') or '').strip()

    if not api_key:
        return jsonify({'ok': False, 'error': 'API key is required'}), 400
    if any(c.isspace() for c in api_key):
        return jsonify({'ok': False, 'error': 'API key must not contain spaces or line breaks'}), 400

    if IS_VERCEL:
        return jsonify({
            'ok': False,
            'error': 'On Vercel the filesystem is read-only. Set GEMINI_API_KEY in Project Settings → Environment Variables instead.'
        }), 400

    try:
        update_env_var('GEMINI_API_KEY', api_key)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Could not write .env: {e}'}), 500

    # Apply live without a server restart
    os.environ['GEMINI_API_KEY'] = api_key
    err = init_gemini_client()
    if err or client is None:
        return jsonify({'ok': False, 'error': err or 'Client failed to initialize'}), 200

    # Best-effort live verification with a tiny call
    verified, verify_error = None, None
    try:
        r = client.models.generate_content(
            model=MODEL_NAME,
            contents=[{'role': 'user', 'parts': [{'text': 'Reply with: ok'}]}],
            config=types.GenerateContentConfig(max_output_tokens=5),
        )
        verified = bool(r.text)
    except Exception as e:
        verified = False
        verify_error = str(e)[:200]

    return jsonify({
        'ok': True,
        'auth_mode': auth_mode,
        'key_masked': _mask_key(api_key),
        'verified': verified,
        'verify_error': verify_error,
    })


# ── X.com OAuth 2.0 PKCE + DM bot ───────────────────────────────────────────

class XApiError(url_error.HTTPError):
    """HTTPError that carries X's own explanation. A bare "HTTP Error 403:
    Forbidden" hides whether the fan has DMs closed, the token is missing a
    scope, or the API tier forbids the call — the JSON body says which."""

    def __init__(self, e, detail, raw=b''):
        super().__init__(e.url, e.code, e.reason, e.headers, None)
        self.detail = detail
        self._raw = raw

    def read(self, *_a):
        # The body was already consumed to build `detail`; hand back the copy so
        # callers that report e.read() keep working.
        return self._raw

    def __str__(self):
        return f'X API {self.code}: {self.detail}' if self.detail else super().__str__()


_X_403_HINTS = (
    ('cannot send messages', 'they only accept DMs from people they follow'),
    ('not permitted', 'your X app is missing a required scope — reconnect the account'),
    ('client-not-enrolled', 'your X API plan does not include this endpoint'),
    ('unsupported authentication', 'reconnect the account with OAuth 2.0'),
)


def _x_http_error(e):
    """Rebuild an HTTPError with X's JSON detail folded into the message."""
    detail = ''
    try:
        body = e.read()
    except Exception:
        body = b''
    raw = body.decode(errors='ignore') if body else ''
    if raw:
        try:
            d = json.loads(raw)
            errs = d.get('errors') or []
            detail = (d.get('detail') or d.get('title')
                      or (errs[0].get('message') if errs and isinstance(errs[0], dict) else '')
                      or raw[:200])
        except Exception:
            detail = raw[:200]
    if e.code == 403 and detail:
        low = detail.lower()
        for needle, hint in _X_403_HINTS:
            if needle in low:
                detail = f'{detail} ({hint})'
                break
    return XApiError(e, detail.strip(), body)


def _x_api(method, path, access_token=None, bearer=None, body=None):
    """Simple X API v2 helper. Returns parsed JSON dict."""
    url = f'https://api.twitter.com/2{path}'
    headers = {'Content-Type': 'application/json'}
    if access_token:
        headers['Authorization'] = f'Bearer {access_token}'
    elif bearer:
        headers['Authorization'] = f'Bearer {bearer}'
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except url_error.HTTPError as e:
        raise _x_http_error(e) from None


def _x_refresh(persona):
    """Refresh a persona's access token using its stored refresh_token.
    Returns the new access token, or None if refresh isn't possible."""
    tokens = _load_x_tokens()
    t = tokens.get(persona) or {}
    refresh_token = t.get('refresh_token')
    client_id = t.get('client_id') or (_get_setting('x_client_id') or '')
    if not refresh_token or not client_id:
        return None
    body = urllib.parse.urlencode({
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': client_id,
    }).encode()
    req = urllib.request.Request(
        'https://api.twitter.com/2/oauth2/token', data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            td = json.loads(r.read())
    except Exception:
        return None
    t['access_token'] = td.get('access_token', t.get('access_token'))
    if td.get('refresh_token'):
        t['refresh_token'] = td['refresh_token']
    tokens[persona] = t
    _save_x_tokens(tokens)
    return t['access_token']


def _x_call(persona, method, path, body=None):
    """Call the X API as a persona, transparently refreshing the token once on 401."""
    tokens = _load_x_tokens()
    t = tokens.get(persona) or {}
    access_token = t.get('access_token')
    if not access_token:
        raise RuntimeError(f'No X account connected for persona "{persona}".')
    try:
        return _x_api(method, path, access_token=access_token, body=body)
    except url_error.HTTPError as e:
        if e.code == 401:
            new_token = _x_refresh(persona)
            if new_token:
                return _x_api(method, path, access_token=new_token, body=body)
        raise


# ── Fan memory ────────────────────────────────────────────────────────────────
# What she has been told about a fan, kept per fan and fed back into every
# reply. Without it she re-asks what he already answered, which is the fastest
# way to sound like a bot.

FAN_MEM_KEYS = ('name', 'job', 'schedule', 'location', 'partner', 'age',
                'doing_now', 'plans', 'interests', 'notes')


def _fan_mem_key(persona, fan_key):
    return f'fan_mem_{persona}_{fan_key}'


def _persona_identity(persona):
    """The creator's own name/age/location, so they can never be filed as the
    fan's."""
    cfg = load_persona_config(persona) or {}
    return {k: str(cfg.get(k) or '').strip() for k in ('name', 'age', 'location')}


def _fan_memory(persona, fan_key):
    try:
        m = json.loads(_get_setting(_fan_mem_key(persona, fan_key)) or '{}')
    except Exception:
        return {}
    if not isinstance(m, dict):
        return {}
    # Profiles written before the guard below could hold her own name as his.
    # Heal on read so an already-poisoned fan does not need a manual reset.
    return _fan_mem_clean(m, _persona_identity(persona))


def _fan_mem_clean(mem, mine=None):
    """Keep the known shape and drop anything empty or oversized.

    Also drop any value that is really the creator's own — a profile saying the
    fan is called Lilly is how she ends up greeting him by her own name, and it
    persists until something rewrites it, so it must not be storable at all.
    """
    mine = mine or {}
    out = {}
    for k in FAN_MEM_KEYS:
        v = mem.get(k)
        if isinstance(v, list):
            v = [str(x).strip()[:120] for x in v if str(x).strip()][:10]
            if v:
                out[k] = v
        elif v not in (None, '', [], {}):
            v = str(v).strip()[:200]
            if mine.get(k) and v.casefold() == mine[k].casefold():
                continue
            out[k] = v
    return out


def _fan_memory_update(persona, fan_key, incoming, reply=''):
    """Fold what the fan just said into his profile. Best effort: a failure here
    must never stop a reply going out."""
    if not (incoming or '').strip():
        return _fan_memory(persona, fan_key)
    old = _fan_memory(persona, fan_key)
    mine = _persona_identity(persona)
    # The creator's own details are in the transcript too — she introduces
    # herself. Without naming her here the model files "I'm Lilly, 22" as the
    # FAN's name and age, and she spends the rest of the chat calling him Lilly.
    hers = ', '.join(f'{k} {v}' for k, v in mine.items() if v) or 'not configured'
    system = (
        "You maintain a short factual profile of THE FAN — the person the "
        "creator is chatting with. Never record the creator's own details as "
        f"the fan's. The creator is: {hers}. Anything she says about herself is "
        "not about him. "
        "Return ONLY JSON with these keys: name, job, schedule, location, partner, "
        "age, doing_now, plans, interests, notes. `interests`, `plans` and `notes` "
        "are arrays of short strings; the rest are short strings. "
        "Carry every existing value forward unless the new message contradicts or "
        "updates it. `job` is what they do for work, in their words. `schedule` is "
        "their working hours or shift pattern, including when they finish. "
        "`doing_now` is what they are doing right now and when they said it. "
        "Add nothing that was not said or clearly implied. Use \"\" for unknown.")
    user = (f"Existing profile of the fan:\n{json.dumps(old, ensure_ascii=False)}\n\n"
            f"The fan just said:\n{incoming[:1500]}")
    if reply:
        user += ("\n\nThe creator replied (this is about HER, never about him):\n"
                 + reply[:500])
    try:
        resp = client.models.generate_content(
            model=MODEL_NAME,
            contents=[{'role': 'user', 'parts': [{'text': user}]}],
            config=types.GenerateContentConfig(
                system_instruction=system, temperature=0.2,
                max_output_tokens=500, response_mime_type='application/json'),
        )
        mem = json.loads((resp.text or '{}').strip())
        if not isinstance(mem, dict):
            return old
    except Exception as e:
        logger.info('fan memory update failed for %s: %s', fan_key, str(e)[:120])
        return old
    merged = _fan_mem_clean({**old, **mem}, mine)
    if merged != old:
        _set_setting(_fan_mem_key(persona, fan_key), json.dumps(merged))
    return merged


LABELS = {'name': 'Name', 'job': 'Work', 'schedule': 'Their hours',
          'location': 'Where they are', 'partner': 'Relationship', 'age': 'Age',
          'doing_now': 'Doing right now', 'plans': 'Coming up',
          'interests': 'Into', 'notes': 'Other things they told you'}


def _fan_memory_block(mem, persona=None):
    """The prompt fragment that stops her asking what she already knows."""
    mine = _persona_identity(persona) if persona else {}
    mem = _fan_mem_clean(mem or {}, mine)
    # Say plainly whose name is whose. When the transcript is ambiguous about
    # who said what, this is the only thing standing between her and greeting
    # the fan by her own name.
    whose = ''
    if mine.get('name'):
        whose = (f"YOU are {mine['name']}. The fan is a different person: "
                 + (f"his name is {mem['name']}." if mem.get('name') else
                    "you have not been told his name yet, so do not use one.")
                 + f" Never address him as {mine['name']} — that is you.\n\n")
    if not mem:
        return whose
    lines = []
    for k in FAN_MEM_KEYS:
        v = mem.get(k)
        if not v:
            continue
        lines.append(f"- {LABELS[k]}: " + ('; '.join(v) if isinstance(v, list) else v))
    return whose + (
        "WHAT YOU ALREADY KNOW ABOUT THIS FAN — treat it as remembered, never ask "
        "for it again:\n" + '\n'.join(lines) +
        "\nBuild on it instead: if you know they are at work, ask about that job, "
        "when their shift ends, how it is going today — not what they are doing. "
        "Refer back to it naturally, the way someone who was listening would.\n\n")


def _persona_text(persona, instruction, history=None, max_tokens=1024, temperature=0.9):
    """Generate an in-character message for a persona via Gemini."""
    system_prompt = get_system_prompt(persona)
    contents = []
    for m in (history or [])[-20:]:
        contents.append({'role': 'model' if m['role'] in ('bot', 'model') else 'user',
                         'parts': [{'text': m['content']}]})
    contents.append({'role': 'user', 'parts': [{'text': instruction}]})
    cfg = _no_thinking(types.GenerateContentConfig(
        system_instruction=system_prompt, temperature=temperature,
        max_output_tokens=max_tokens))
    response = client.models.generate_content(
        model=MODEL_NAME, contents=contents, config=cfg,
    )
    return _gemini_text(response)


def _x_resolve_user(persona, handle):
    """Resolve an @handle (or id) to {id, username}. Accepts URLs too."""
    handle = handle.strip().lstrip('@')
    if 'x.com/' in handle or 'twitter.com/' in handle:
        handle = handle.rstrip('/').split('/')[-1].split('?')[0]
    if handle.isdigit():
        data = _x_call(persona, 'GET', f'/users/{handle}?user.fields=username,name')
    else:
        data = _x_call(persona, 'GET', f'/users/by/username/{handle}?user.fields=username,name')
    u = data.get('data') or {}
    if not u.get('id'):
        raise RuntimeError(f'Could not find X user "{handle}".')
    return {'id': u['id'], 'username': u.get('username', handle), 'name': u.get('name', '')}


def _x_extract_tweet_id(ref):
    """Pull a tweet id out of a URL or return the id as-is."""
    ref = ref.strip()
    if '/status/' in ref:
        ref = ref.split('/status/')[1]
    return ref.split('?')[0].split('/')[0]


def _x_state_path(persona, name):
    """Per-persona state file path (auto-run cursors, contacted/seen sets)."""
    fn = f'x_{name}_{persona}.json'
    return f'/tmp/{fn}' if IS_VERCEL else os.path.join(BASE_DIR, f'.{fn}')


def _x_load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _x_save_json(path, data):
    try:
        with open(path, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass


def _x_save_history(persona, other_id, entries):
    path = _x_state_path(persona, f'hist_{other_id}')
    _x_save_json(path, entries[-40:])


def _x_username_for(persona, uid):
    """Resolve a sender's @username from their numeric id, cached per persona."""
    if not uid:
        return ''
    cache_path = _x_state_path(persona, 'usercache')
    cache = _x_load_json(cache_path, {})
    if uid in cache:
        return cache[uid]
    name = ''
    try:
        d = _x_call(persona, 'GET', f'/users/{uid}?user.fields=username')
        name = (d.get('data') or {}).get('username', '')
    except Exception:
        pass
    if name:
        cache[uid] = name
        _x_save_json(cache_path, cache)
    return name


X_FOLLOWUP_MAX = 2
X_READ_CAP = 12.0
X_TYPE_CAP = 25.0


def _x_fans(persona):
    """Per-fan DM state (last seen in/out, follow-ups sent). Kept in the settings
    table rather than /tmp so it survives a redeploy."""
    try:
        return json.loads(_get_setting(f'x_fans_{persona}') or '{}')
    except Exception:
        return {}


def _x_save_fans(persona, fans):
    _set_setting(f'x_fans_{persona}', json.dumps(fans))


def _x_touch_fan(persona, uid, username, direction, fans=None):
    """Record that a message went in or out, so follow-ups know who went quiet."""
    own = fans is None
    if own:
        fans = _x_fans(persona)
    fan = fans.get(str(uid)) or {}
    if username:
        fan['name'] = username
    if direction == 'in':
        fan['last_in'] = int(time.time())
        fan['followups'] = 0
    else:
        fan['last_out'] = int(time.time())
    fans[str(uid)] = fan
    if own:
        _x_save_fans(persona, fans)
    return fans


X_SEEN_EVENTS_MAX = 400


def _x_seen_events(persona):
    """DM event ids already handled. A set beats a single high-water mark: it
    does not depend on X returning events in any particular order, so a reply
    can never be skipped because a newer event sorted ahead of it."""
    try:
        raw = json.loads(_get_setting(f'x_seen_events_{persona}') or '[]')
        return set(raw) if isinstance(raw, list) else set()
    except Exception:
        return set()


def _x_mark_seen(persona, event_ids):
    ids = [e for e in event_ids if e]
    if not ids:
        return
    seen = _x_seen_events(persona)
    seen.update(ids)
    _set_setting(f'x_seen_events_{persona}',
                 json.dumps(list(seen)[-X_SEEN_EVENTS_MAX:]))


# Phrases that only make sense if the other person wrote first. An opener
# containing one of these is answering a message that never existed.
_REPLY_TELL_RE = re.compile(
    r"(pop(ped|ping)?\s+up\s+in\s+my|in\s+my\s+(dms|inbox)|"
    r"thanks?\s+(you\s+)?for\s+(the\s+)?(message|dm|reaching|writing|msg)|"
    r"you\s+(just\s+)?(messaged|dm'?d|wrote|reached out)|"
    r"nice\s+to\s+hear\s+from\s+you|good\s+to\s+hear\s+from\s+you|"
    r"glad\s+you\s+(messaged|wrote|reached)|replying\s+to\s+you)", re.I)


def _x_fix_cold_opener(persona, opener, instruction):
    """A cold opener that talks as though the fan messaged first reads as a bot.
    Regenerate once with the mistake named; give up rather than send it."""
    if not opener or not _REPLY_TELL_RE.search(opener):
        return opener
    logger.info('cold opener implied an earlier message, regenerating: %s', opener[:80])
    retry = _persona_text(
        persona,
        instruction + ' Your previous attempt wrongly implied they had already '
                      'messaged you. They have not. Write it again without that.',
        max_tokens=1024, temperature=0.9)
    if retry and not _REPLY_TELL_RE.search(retry):
        return retry
    logger.warning('cold opener still implied an earlier message — not sending')
    return ''


def _x_history(persona, uid, limit=40):
    """Past DMs with one fan, oldest first, in Gemini history shape. Survives
    restarts — unlike the /tmp JSON mirror, which Vercel and Cloud Run wipe."""
    try:
        from db import SessionLocal, list_x_messages
        s = SessionLocal()
        try:
            return [{'role': 'model' if r.direction == 'out' else 'user', 'content': r.text}
                    for r in list_x_messages(s, persona, uid, limit=limit)]
        finally:
            s.close()
    except Exception as exc:
        logger.warning('_x_history failed for %s/%s: %s', persona, uid, exc)
        return []


def _x_messaged_before(persona, uid):
    """True when this account already has DMs on record with the user, so the
    gathering loop never opens a second cold chat with the same person."""
    try:
        from db import SessionLocal, count_x_messages
        s = SessionLocal()
        try:
            return count_x_messages(s, persona, uid) > 0
        finally:
            s.close()
    except Exception:
        return False


def _x_cta_link(persona, uid, cta_url):
    """Route the CTA through our own redirect so X clicks are measurable, the way
    Telegram's are. Falls back to the raw link when no public base URL is known
    (the worker has no request context to borrow one from)."""
    base = (os.getenv('PUBLIC_BASE_URL') or _get_setting('public_base_url') or '').rstrip('/')
    if not base:
        return cta_url
    return f'{base}/go/x/{persona}/{uid}'


@app.route('/go/x/<persona>/<uid>')
def x_cta_click(persona, uid):
    """Tracked CTA redirect for X DMs — records the click, then forwards on."""
    if not re.match(r'^[a-z0-9_-]+$', persona or ''):
        return redirect('/')
    cta = _phases_cta(persona)
    url = (cta.get('cta_url') or (_tg_load_bots().get(persona) or {}).get('cta_url') or '').strip()
    if not url:
        return redirect('/')
    fans = _x_fans(persona)
    fan = fans.get(str(uid))
    if fan is not None and not fan.get('cta_clicked'):
        fan['cta_clicked'] = int(time.time())
        fans[str(uid)] = fan
        _x_save_fans(persona, fans)
    return redirect(url, code=302)


def _x_send_dm(persona, conv_id, text):
    _x_call(persona, 'POST', f'/dm_conversations/{conv_id}/messages', body={'text': text})


def _x_send_human(persona, conv_id, text, incoming='', cfg=None):
    """Send a DM the way a person would: a pause to read, then a delay scaled to
    how long the reply takes to type, split across a burst or two. X has no
    typing indicator, so the delay is the only signal — it just has to feel
    unhurried rather than instant."""
    cfg = cfg or _x_behavior(persona)
    if not cfg.get('humanize', True):
        _x_send_dm(persona, conv_id, text)
        return
    cps = max(2, int(cfg.get('typing_speed') or 14) // 2)
    time.sleep(random.uniform(15, 120))
    time.sleep(min(0.8 + len(incoming) / 90.0, X_READ_CAP) * random.uniform(0.7, 1.3))
    for i, chunk in enumerate(_tg_bursts(text)):
        if not chunk:
            continue
        if i:
            time.sleep(random.uniform(0.6, 1.6))
        time.sleep(min(max(len(chunk) / float(cps), 1.2), X_TYPE_CAP) * random.uniform(0.85, 1.2))
        _x_send_dm(persona, conv_id, chunk)


def _x_dm_reply_round(persona, max_results=20):
    """Reply in-character to new incoming DMs. Returns (replied_count, log_lines)."""
    cfg = _x_behavior(persona)
    if not cfg.get('enabled', True):
        return 0, ['X bot is switched off for this persona (see "How she replies").']
    tokens = _load_x_tokens()
    t = tokens.get(persona) or {}
    user_id = t.get('user_id', '')
    if not t.get('access_token'):
        return 0, [f'No X account connected for "{persona}".']
    if not user_id:
        # Recoverable: the connect flow stores this, but /users/me can fail there.
        try:
            user_id = ((_x_call(persona, 'GET', '/users/me') or {}).get('data') or {}).get('id', '')
        except Exception as e:
            return 0, [f'Could not identify the connected account: {str(e)[:160]}']
        if not user_id:
            return 0, ['The connected X account has no user id stored — reconnect it '
                       'on the X Bot tab so replies can tell your messages from theirs.']
        t['user_id'] = user_id
        tokens[persona] = t
        _save_x_tokens(tokens)
    seen = _x_seen_events(persona)
    path = ('/dm_events?dm_event.fields=id,text,sender_id,created_at,dm_conversation_id'
            f'&event_types=MessageCreate&max_results={max_results}')
    try:
        convs = _x_call(persona, 'GET', path)
    except Exception as e:
        return 0, [f'DM read failed: {str(e)[:200]}']
    events = convs.get('data', []) or []
    replied = 0
    log = []
    if not events:
        return 0, ['No DM events returned by X (nobody has messaged this account yet).']
    # First run on this account: remember what is already there instead of
    # replying to the whole backlog at once.
    if not seen:
        _x_mark_seen(persona, [e.get('id', '') for e in events])
        return 0, [f'First DM check — {len(events)} existing message(s) noted; '
                   'replies start from the next one in.']
    incoming = [e for e in events
                if e.get('sender_id') != user_id and e.get('id') not in seen]
    if not incoming:
        return 0, []
    phases = _phases(persona)
    cta = _phases_cta(persona)
    cta_url = (cta.get('cta_url') or (_tg_load_bots().get(persona) or {}).get('cta_url') or '').strip()
    cta_label = (cta.get('cta_label') or 'come see').strip()
    fans = _x_fans(persona)
    handled = []
    # Oldest first, so a burst of messages is answered in the order it was sent.
    for event in sorted(incoming, key=lambda e: e.get('created_at') or '')[-max_results:]:
        eid = event.get('id', '')
        sender = event.get('sender_id', '')
        text = event.get('text', '').strip()
        handled.append(eid)
        if not text:
            continue
        sender_name = _x_username_for(persona, sender)
        _log_x_event('dm_in', persona=persona, x_username=sender_name, detail=text[:160])
        _log_x_message(persona, sender, sender_name, 'in', text)
        conv_id = event.get('dm_conversation_id') or event.get('conversation_id') or f'dm_{sender}'

        fan = fans.get(str(sender)) or {}
        fan['name'] = sender_name or fan.get('name', '')
        fan['conv_id'] = conv_id
        fan['last_in'] = int(time.time())
        fan['followups'] = 0
        fan['in_count'] = int(fan.get('in_count', 0)) + 1
        if not fan.get('first_in'):
            fan['first_in'] = int(time.time())
        fans[str(sender)] = fan

        phase_idx = _fan_phase(phases, fan)
        is_cta_phase = phase_idx == len(phases) - 1
        cta_asked = _cta_asked(text)
        cta_due = _cta_due(persona, text, fan, is_cta_phase, cta_url)
        # Use DB history (survives restarts) with JSON file as fallback
        db_hist = _x_history(persona, sender)
        if not db_hist:
            hist_path = _x_state_path(persona, f'hist_{sender}')
            db_hist = _x_load_json(hist_path, [])
        try:
            has_history = len(db_hist) > 0
            continuity = (
                "You have the full earlier conversation above — USE it: do NOT "
                "introduce yourself again, do NOT re-state your name/age/location, "
                "do NOT re-ask anything they already told you. Continue naturally "
                "from where you left off. ") if has_history else ''
            ask_rule = question_rule_for(load_persona_config(persona), db_hist)
            if cta_asked and cta_due:
                instruction = (
                    f'Reply in-character to this fan\'s X DM: "{text}". {continuity}'
                    'They are asking where else to find you — answer them directly and '
                    'warmly, say yes, that is where you post the rest. Do NOT deflect, '
                    'do NOT answer with a question, and do NOT paste a link yourself; '
                    'a link is appended after your message. Keep it to one or two '
                    'short sentences.')
            elif cta_due:
                instruction = (
                    f'Reply in-character to this fan\'s X DM: "{text}". {continuity}'
                    'Answer what they actually said first, then tease — in one natural '
                    'sentence — that you post more somewhere more private. Do NOT paste '
                    'a link or a URL, do not hard-sell, and do not name the site; a link '
                    'is appended after your message. ' + ask_rule)
            else:
                instruction = (
                    f'Reply in-character to this fan\'s X DM: "{text}". {continuity}'
                    'Warm and engaging, react to what they just said before anything '
                    'else, reference what they have told you before, and let interest '
                    'build slowly — no selling, no hinting at paid content yet. '
                    + ask_rule)
            _fan_memory_update(persona, f'x:{sender}', text)
            lim = reply_length_limits(load_persona_config(persona))
            instruction = _fan_memory_block(_fan_memory(persona, f'x:{sender}'), persona) + \
                instruction + ' ' + lim['note']
            reply = _fv_trim(_persona_text(persona, instruction, history=db_hist,
                                           max_tokens=lim['tokens'], temperature=0.9),
                             max_sentences=lim['sentences'], hard_cap=lim['cap'])
            if not reply:
                continue
            if cta_due:
                link = _x_cta_link(persona, sender, cta_url)
                reply = f'{reply}\n\n{cta_label} → {link}'
                fan['cta_sent'] = int(time.time())
                fan['cta_count'] = int(fan.get('cta_count', 0)) + 1
                logger.info('CTA SENT [x/%s] fan=%s trigger=%s phase=%d count=%d link=%s',
                            persona, sender_name or sender,
                            'asked' if cta_asked else 'phase', phase_idx,
                            fan['cta_count'], link)
            elif cta_asked and not cta_url:
                logger.warning('CTA asked on X but no cta_url configured [%s] fan=%s',
                               persona, sender_name or sender)
            _x_send_human(persona, conv_id, reply, incoming=text, cfg=cfg)
            fan['last_out'] = int(time.time())
            fans[str(sender)] = fan
            _log_x_message(persona, sender, sender_name, 'out', reply)
            hist_path = _x_state_path(persona, f'hist_{sender}')
            file_hist = _x_load_json(hist_path, [])
            file_hist.append({'role': 'user', 'content': text})
            file_hist.append({'role': 'bot', 'content': reply})
            _x_save_history(persona, sender, file_hist)
            replied += 1
            log.append(f'DM reply → {sender_name or sender} (phase {phase_idx + 1}/{len(phases)})'
                       + (' + link' if cta_due else '') + f': {reply[:60]}')
        except Exception as e:
            log.append(f'DM reply failed: {str(e)[:200]}')
    _x_save_fans(persona, fans)
    _x_mark_seen(persona, handled)
    logger.info('X DM round [%s]: %d events, %d new, %d replied',
                persona, len(events), len(incoming), replied)
    return replied, log


def _x_followup_round(persona):
    """Re-engage fans who went quiet on X, and nudge once on an unclicked link.
    Capped at X_FOLLOWUP_MAX per fan so it never turns into spam."""
    cfg = _x_behavior(persona)
    if not cfg.get('enabled', True) or not cfg.get('followups', True):
        return 0, []
    gap_min = max(5, int(cfg.get('followup_min') or 45))
    fans = _x_fans(persona)
    now = int(time.time())
    sent = 0
    log = []
    for uid, fan in list(fans.items()):
        last = max(int(fan.get('last_in') or 0), int(fan.get('last_out') or 0))
        if not last or int(fan.get('last_in') or 0) > int(fan.get('last_out') or 0):
            continue  # they spoke last — the reply path handles it
        n = int(fan.get('followups', 0))
        if n >= X_FOLLOWUP_MAX:
            continue
        if (now - last) / 60.0 < gap_min * (n + 1):
            continue
        conv_id = fan.get('conv_id')
        if not conv_id:
            continue
        if fan.get('cta_sent') and not fan.get('cta_clicked'):
            instruction = (
                'This fan went quiet after you sent them your link. Write ONE short, '
                'light, in-character nudge — curious whether they had a look, playful, '
                'zero pressure, no link. One or two sentences.')
        else:
            instruction = (
                'This fan went quiet in your X DMs. Write ONE short, in-character '
                'message that reopens the conversation — reference something they '
                'mentioned before if you can, and ask them something easy to answer.')
        try:
            hist = _x_history(persona, uid)
            lim = persona_length_limits(persona)
            text = _fv_trim(_persona_text(persona, instruction, history=hist,
                                          max_tokens=lim['tokens'], temperature=0.9),
                            max_sentences=lim['sentences'], hard_cap=lim['cap'])
            if not text:
                continue
            _x_send_human(persona, conv_id, text, cfg=cfg)
            _log_x_message(persona, uid, fan.get('name', ''), 'out', text)
            fan['followups'] = n + 1
            fan['last_out'] = int(time.time())
            fans[uid] = fan
            sent += 1
            log.append(f'Follow-up {n + 1}/{X_FOLLOWUP_MAX} → {fan.get("name") or uid}: {text[:60]}')
        except Exception as e:
            log.append(f'Follow-up failed for {fan.get("name") or uid}: {str(e)[:200]}')
    if sent:
        _x_save_fans(persona, fans)
    return sent, log


def _x_comment_round(persona, post, limit, preview=False, skip_seen=False):
    """Reply in-character to comments under a post. Returns list of result dicts.
    With skip_seen, comment ids already replied to (per persona) are skipped and
    new ones recorded — used by the autonomous loop to avoid double replies."""
    tweet_id = _x_extract_tweet_id(post)
    me_id = _x_me_id(persona)
    q = urllib.parse.quote(f'conversation_id:{tweet_id}')
    path = (f'/tweets/search/recent?query={q}'
            f'&max_results={max(10, limit)}'
            f'&tweet.fields=author_id,text,conversation_id&expansions=author_id'
            f'&user.fields=username')
    res = _x_call(persona, 'GET', path)
    tweets = res.get('data', []) or []
    users = {u['id']: u for u in (res.get('includes', {}).get('users', []) or [])}

    seen_path = _x_state_path(persona, 'comments_seen')
    seen = set(_x_load_json(seen_path, [])) if skip_seen else set()

    results = []
    for tw in tweets:
        if len(results) >= limit:
            break
        tid = tw.get('id')
        if tid == tweet_id or tw.get('author_id') == me_id:
            continue
        if skip_seen and tid in seen:
            continue
        comment_text = tw.get('text', '').strip()
        if not comment_text:
            continue
        author = users.get(tw.get('author_id'), {})
        instruction = (
            "You are replying to a comment someone left on your X post. "
            "Write ONE in-character reply (no hashtags, sound human and "
            "flirty-but-natural). It must fit in a single tweet (280 characters). "
            "Their comment: \"" + comment_text + "\"")
        reply = _persona_text(persona, instruction, max_tokens=400, temperature=0.95)
        if not reply:
            continue
        item = {'to': '@' + author.get('username', '?'), 'comment': comment_text, 'reply': reply}
        if not preview:
            try:
                _x_call(persona, 'POST', '/tweets',
                        body={'text': reply[:280], 'reply': {'in_reply_to_tweet_id': tid}})
                item['posted'] = True
                if skip_seen:
                    seen.add(tid)
            except Exception as e:
                item['posted'] = False
                item['error'] = str(e)[:120]
        results.append(item)

    if skip_seen and not preview:
        _x_save_json(seen_path, list(seen)[-1000:])
    return results


def _x_generate_post(persona, topic=''):
    """Generate one original in-character tweet for a persona."""
    ctx = f' about: {topic}.' if topic else '.'
    instruction = (
        "Write ONE original X post (tweet) as yourself, in-character" + ctx +
        " Engaging and human, invite replies, at most one hashtag, no @mentions. "
        "It must fit in a single tweet (280 characters). Return only the tweet text.")
    text = _persona_text(persona, instruction, max_tokens=400, temperature=1.0)
    return text.strip().strip('"')[:280]


def _x_my_recent_tweet_ids(persona, n=5):
    """IDs of the persona account's most recent original posts."""
    me_id = _x_me_id(persona)
    res = _x_call(persona, 'GET',
                  f'/users/{me_id}/tweets?max_results={max(5, n)}'
                  f'&tweet.fields=id&exclude=replies,retweets')
    return [t['id'] for t in (res.get('data', []) or [])][:n]


def _x_known_user_ids(persona):
    """Fan ids the persona already has a logged DM conversation with."""
    try:
        from db import SessionLocal, list_x_known_user_ids
        s = SessionLocal()
        try:
            return list_x_known_user_ids(s, persona)
        finally:
            s.close()
    except Exception:
        return set()


def _x_opener_ids(persona):
    """Fan ids the persona has ever sent an opener to (permanent, never pruned)."""
    try:
        from db import SessionLocal, list_x_opener_ids
        s = SessionLocal()
        try:
            return list_x_opener_ids(s, persona)
        finally:
            s.close()
    except Exception:
        return set()


def _x_record_opener(persona, x_user_id):
    """Permanently record that the persona sent this fan an opener. Self-heals."""
    def _do():
        from db import SessionLocal, record_x_opener
        s = SessionLocal()
        try:
            record_x_opener(s, persona, x_user_id)
            s.commit()
        finally:
            s.close()
    try:
        _do()
    except Exception:
        if _ensure_x_tables():
            try:
                _do()
            except Exception:
                pass


X_FEED_POST_AGE_MIN = 30    # only mine posts this fresh
X_FEED_REPLY_AGE_MIN = 15   # only people who replied this recently
X_FEED_POSTS_SCANNED = 12   # posts to open per round, newest first


def _x_rfc3339(dt):
    """X wants RFC3339 to the second, and rejects a start_time that is not
    comfortably in the past."""
    return dt.replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _x_parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None


def _x_fresh_feed_posts(persona, me_id, post_age_min=None):
    """Posts from the accounts she follows, newer than the window, newest first.
    Her own posts are left out — other rounds handle those. Returns [] when the
    home timeline is unavailable, which is not on every API plan."""
    post_age_min = int(post_age_min or X_FEED_POST_AGE_MIN)
    now = datetime.now(timezone.utc)
    posts_since = now - timedelta(minutes=post_age_min)
    try:
        feed = _x_call(persona, 'GET',
                       f'/users/{me_id}/timelines/reverse_chronological'
                       f'?max_results=50&start_time={_x_rfc3339(posts_since)}'
                       '&tweet.fields=created_at,author_id,conversation_id'
                       '&expansions=author_id&user.fields=username,name')
    except Exception as e:
        logger.info('home feed unavailable for %s: %s', persona, str(e)[:160])
        return []
    authors = {u['id']: u for u in (feed.get('includes', {}).get('users', []) or [])}
    posts = []
    for tw in feed.get('data', []) or []:
        if tw.get('author_id') == me_id:
            continue
        at = _x_parse_time(tw.get('created_at'))
        if at and at < posts_since:
            continue
        tw['_author'] = authors.get(tw.get('author_id'), {})
        posts.append((at or now, tw))
    posts.sort(key=lambda p: p[0], reverse=True)
    if not posts:
        logger.info('no feed posts newer than %d min for %s', post_age_min, persona)
    return [tw for _, tw in posts]


def _x_fresh_replies(persona, post, me_id, reply_age_min=None):
    """People replying under a post within the window, newest reply first —
    someone who typed a minute ago is far more likely to still be there."""
    reply_age_min = int(reply_age_min or X_FEED_REPLY_AGE_MIN)
    replies_since = datetime.now(timezone.utc) - timedelta(minutes=reply_age_min)
    cid = post.get('conversation_id') or post.get('id')
    if not cid:
        return []
    q = urllib.parse.quote(f'conversation_id:{cid}')
    try:
        res = _x_call(persona, 'GET',
                      f'/tweets/search/recent?query={q}&max_results=50'
                      f'&start_time={_x_rfc3339(replies_since)}'
                      '&tweet.fields=author_id,text,created_at,conversation_id'
                      '&expansions=author_id&user.fields=username,name')
    except Exception as e:
        logger.info('replies for %s unavailable: %s', cid, str(e)[:120])
        return []
    users = {u['id']: u for u in (res.get('includes', {}).get('users', []) or [])}
    out = []
    for reply in res.get('data', []) or []:
        uid = reply.get('author_id')
        if not uid or uid == me_id or reply.get('id') == cid:
            continue
        at = _x_parse_time(reply.get('created_at'))
        if at and at < replies_since:
            continue
        reply['_author'] = users.get(uid, {})
        reply['_at'] = at or datetime.now(timezone.utc)
        out.append(reply)
    out.sort(key=lambda r: r['_at'], reverse=True)
    return out


def _x_feed_candidates(persona, limit, contacted, me_id,
                       post_age_min=None, reply_age_min=None):
    """People who just replied to someone else's post in her feed.

    Someone mid-conversation on a creator's fresh post is the warmest cold
    audience there is, so the newest replier is tried first. Returns [] when
    the feed is unavailable, letting the caller fall back."""
    posts = _x_fresh_feed_posts(persona, me_id, post_age_min)
    if not posts:
        return []

    found, seen = [], set()
    for tw in posts[:X_FEED_POSTS_SCANNED]:
        for reply in _x_fresh_replies(persona, tw, me_id, reply_age_min=reply_age_min):
            uid = reply.get('author_id')
            if uid in contacted or uid in seen:
                continue
            seen.add(uid)
            u = reply['_author']
            found.append({'id': uid, 'username': u.get('username') or '?',
                          'name': u.get('name', ''), 'tweet': reply.get('text', ''),
                          'at': reply['_at']})

    # Freshest replier first — someone who typed a minute ago is far more
    # likely to still be at their phone than someone from fifteen.
    found.sort(key=lambda c: c['at'], reverse=True)
    logger.info('feed scan [%s]: %d posts, %d fresh repliers', persona,
                len(posts[:X_FEED_POSTS_SCANNED]), len(found))
    for c in found:
        c.pop('at', None)
    return found[:limit]


X_SEEN_TWEETS_MAX = 600

# X returns this when the account may not reply to a stranger's post. It is a
# property of the account, not of the post, so one rejection means every other
# post in the round will be rejected too.
_X_REPLY_BLOCKED_RE = re.compile(
    r'only reply to or quote posts where you are mentioned|'
    r'not permitted to (reply|create)', re.I)


def _x_seen_tweets(persona):
    """Tweet ids already replied to. In the database, not /tmp, so a redeploy
    does not make her answer the same person twice."""
    try:
        raw = json.loads(_get_setting(f'x_seen_tweets_{persona}') or '[]')
        return set(raw) if isinstance(raw, list) else set()
    except Exception:
        return set()


def _x_mark_tweets_seen(persona, ids):
    ids = [i for i in ids if i]
    if not ids:
        return
    seen = _x_seen_tweets(persona)
    seen.update(ids)
    _set_setting(f'x_seen_tweets_{persona}', json.dumps(list(seen)[-X_SEEN_TWEETS_MAX:]))


def _x_like(persona, me_id, tweet_id):
    try:
        _x_call(persona, 'POST', f'/users/{me_id}/likes', body={'tweet_id': tweet_id})
        return True
    except Exception as e:
        logger.info('like %s failed: %s', tweet_id, str(e)[:120])
        return False


def _x_feed_engage_round(persona, post_limit=4, reply_limit=8, post_age_min=None,
                         reply_age_min=None, do_posts=True, do_replies=True,
                         do_likes=True):
    """Work the feed: leave a short comment on fresh posts from the creators she
    follows, then like and answer the people replying under them — newest
    replier first, since they are the ones still holding their phone."""
    me_id = _x_me_id(persona)
    actions = {'post_comments': 0, 'reply_answers': 0, 'likes': 0}
    log = []
    if not me_id:
        return actions, ['No X account connected.']

    posts = _x_fresh_feed_posts(persona, me_id, post_age_min)
    if not posts:
        return actions, [f'No posts in the feed from the last '
                         f'{int(post_age_min or X_FEED_POST_AGE_MIN)} minutes.']

    seen = _x_seen_tweets(persona)
    handled = []
    comment_tries = 0
    for post in posts[:X_FEED_POSTS_SCANNED]:
        author = (post.get('_author') or {}).get('username', '?')
        text = (post.get('text') or '').strip()

        if do_posts and comment_tries < post_limit and post['id'] not in seen:
            comment_tries += 1  # a rejected attempt still costs the budget
            instruction = (
                'Leave a short public comment on this post from another creator. '
                'React to what it actually says — one or two lines, warm and '
                'natural, the kind of thing that makes people look at your '
                'profile. No hashtags, no pitch, no emoji spam. '
                f'Their post: "{text[:400]}"')
            comment = _persona_text(persona, instruction, max_tokens=200, temperature=0.95)
            comment = _strip_placeholders(_fv_trim(comment, max_sentences=2, hard_cap=240))
            if comment:
                try:
                    _x_call(persona, 'POST', '/tweets',
                            body={'text': comment[:280],
                                  'reply': {'in_reply_to_tweet_id': post['id']}})
                    actions['post_comments'] += 1
                    handled.append(post['id'])
                    log.append(f'💬 commented on @{author}: {comment[:60]}')
                except Exception as e:
                    if _X_REPLY_BLOCKED_RE.search(str(e)):
                        # X refuses replies from this account to strangers'
                        # posts. Retrying the next eleven is pointless and
                        # spends a Gemini call each time.
                        do_posts = False
                        log.append('Public commenting is blocked for this account: '
                                   + str(e)[:160]
                                   + ' — skipping comments for the rest of this round.')
                    else:
                        log.append(f'comment on @{author} failed: {str(e)[:140]}')

        if not (do_replies or do_likes):
            continue
        for reply in _x_fresh_replies(persona, post, me_id, reply_age_min=reply_age_min):
            if actions['reply_answers'] >= reply_limit:
                break
            rid = reply.get('id')
            if rid in seen or rid in handled:
                continue
            who = (reply.get('_author') or {}).get('username', '?')
            said = (reply.get('text') or '').strip()
            if not said:
                continue
            if do_likes and _x_like(persona, me_id, rid):
                actions['likes'] += 1
            if not do_replies:
                handled.append(rid)
                continue
            instruction = (
                'Someone replied under a post you are also in the comments of. '
                'Answer THEM, warmly and specifically — pick up on what they '
                'actually said and respond as though you are into it. If they '
                'say "can we go out already" you say something like "I would '
                'love to haha". One short line, in character, no hashtags, no '
                f'pitch. They said: "{said[:300]}"')
            answer = _persona_text(persona, instruction, max_tokens=200, temperature=0.95)
            answer = _strip_placeholders(_fv_trim(answer, max_sentences=2, hard_cap=200))
            if not answer:
                continue
            try:
                _x_call(persona, 'POST', '/tweets',
                        body={'text': answer[:280], 'reply': {'in_reply_to_tweet_id': rid}})
                actions['reply_answers'] += 1
                handled.append(rid)
                log.append(f'↩ @{who} said "{said[:40]}" → {answer[:60]}')
            except Exception as e:
                log.append(f'reply to @{who} failed: {str(e)[:120]}')

    _x_mark_tweets_seen(persona, handled)
    logger.info('feed engage [%s]: %d comments, %d answers, %d likes', persona,
                actions['post_comments'], actions['reply_answers'], actions['likes'])
    return actions, log


def _x_audience_candidates(persona, limit, contacted,
                           post_age_min=None, reply_age_min=None):
    """Find fresh people to DM. Preferred source is live conversation in her
    feed; if that is empty or unavailable, fall back to her own audience —
    recent followers, then people who replied to her own posts."""
    me_id = _x_me_id(persona)
    out, seen = [], set()

    fresh = _x_feed_candidates(persona, limit, contacted, me_id,
                               post_age_min=post_age_min, reply_age_min=reply_age_min)
    for c in fresh:
        seen.add(c['id'])
        out.append(c)
    if len(out) >= limit:
        return out[:limit]

    def add(uid, username, name='', tweet=''):
        if not uid or uid == me_id or uid in contacted or uid in seen:
            return
        seen.add(uid)
        out.append({'id': uid, 'username': username or '?', 'name': name, 'tweet': tweet})

    # Fallback 1: recent followers
    try:
        res = _x_call(persona, 'GET',
                      f'/users/{me_id}/followers?max_results=50&user.fields=username,name')
        for u in res.get('data', []) or []:
            add(u.get('id'), u.get('username'), u.get('name', ''))
            if len(out) >= limit:
                return out
    except Exception:
        pass

    # Fallback 2: people who replied to the persona's recent posts
    try:
        for tid in _x_my_recent_tweet_ids(persona, 3):
            q = urllib.parse.quote(f'conversation_id:{tid}')
            res = _x_call(persona, 'GET',
                          f'/tweets/search/recent?query={q}&max_results=30'
                          f'&tweet.fields=author_id,text&expansions=author_id'
                          f'&user.fields=username,name')
            users = {u['id']: u for u in (res.get('includes', {}).get('users', []) or [])}
            for tw in res.get('data', []) or []:
                u = users.get(tw.get('author_id'), {})
                add(tw.get('author_id'), u.get('username'), u.get('name', ''), tw.get('text', ''))
                if len(out) >= limit:
                    return out
    except Exception:
        pass

    return out


@app.route('/api/x/auth-url', methods=['POST'])
@platform_scoped
def api_x_auth_url():
    """Generate X OAuth 2.0 PKCE authorization URL."""
    data = request.json or {}
    client_id = data.get('client_id', '').strip() or (_get_setting('x_client_id') or '')
    redirect_uri = data.get('redirect_uri', '').strip() or (_get_setting('x_redirect_uri') or '')
    persona = data.get('persona', 'lilly')
    if not client_id or not redirect_uri:
        return jsonify({'ok': False, 'error': 'client_id and redirect_uri are required'}), 400

    # Remember the app credentials so future connects/refreshes don't need them re-entered.
    _set_setting('x_client_id', client_id)
    _set_setting('x_redirect_uri', redirect_uri)
    _log_x_event('connect_start', persona=persona)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = urllib.parse.quote(
        __import__('base64').urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b'=').decode()
    )
    state = secrets.token_urlsafe(16)

    oauth_state = {'code_verifier': code_verifier, 'state': state, 'client_id': client_id,
                   'redirect_uri': redirect_uri, 'persona': persona}
    with open(X_OAUTH_STATE_FILE, 'w') as f:
        json.dump(oauth_state, f)

    params = urllib.parse.urlencode({
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'scope': 'dm.read dm.write tweet.read tweet.write users.read follows.read follows.write like.write offline.access',
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
    })
    return jsonify({'ok': True, 'url': f'https://twitter.com/i/oauth2/authorize?{params}'})


@app.route('/api/x/app-config')
@operator_only
def api_x_app_config():
    """Return the saved X app Client ID + redirect URI so the connect form can
    pre-fill them (so they don't need re-entering each connect/reconnect)."""
    return jsonify({
        'client_id': _get_setting('x_client_id') or '',
        'redirect_uri': _get_setting('x_redirect_uri') or '',
    })


@app.route('/api/x/callback', methods=['POST'])
@platform_scoped
def api_x_callback():
    """Exchange authorization code for access token."""
    data = request.json or {}
    code = data.get('code', '').strip()
    state = data.get('state', '').strip()

    if not os.path.exists(X_OAUTH_STATE_FILE):
        return jsonify({'ok': False, 'error': 'OAuth session expired. Start the flow again.'}), 400
    with open(X_OAUTH_STATE_FILE, 'r') as f:
        saved = json.load(f)

    if state != saved.get('state'):
        return jsonify({'ok': False, 'error': 'State mismatch. Possible CSRF. Start again.'}), 400

    client_id = saved['client_id']
    redirect_uri = saved['redirect_uri']
    code_verifier = saved['code_verifier']
    persona = saved.get('persona', 'lilly')

    body = urllib.parse.urlencode({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'code_verifier': code_verifier,
        'client_id': client_id,
    }).encode()
    req = urllib.request.Request(
        'https://api.twitter.com/2/oauth2/token',
        data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            token_data = json.loads(r.read())
    except url_error.HTTPError as e:
        err = json.loads(e.read()).get('error_description', str(e))
        return jsonify({'ok': False, 'error': f'X token exchange failed: {err}'}), 400

    # Get user info
    try:
        me = _x_api('GET', '/users/me?user.fields=name,username', access_token=token_data['access_token'])
        username = me.get('data', {}).get('username', 'unknown')
        user_id = me.get('data', {}).get('id', '')
    except Exception:
        username, user_id = 'unknown', ''

    tokens = _load_x_tokens()
    tokens[persona] = {
        'access_token': token_data['access_token'],
        'refresh_token': token_data.get('refresh_token', ''),
        'client_id': client_id,
        'username': username,
        'user_id': user_id,
    }
    _save_x_tokens(tokens)
    os.remove(X_OAUTH_STATE_FILE)

    _log_x_event('connect_complete', persona=persona, x_username=username)
    return jsonify({'ok': True, 'username': username, 'persona': persona})


@app.route('/api/x/oauth-redirect', methods=['GET'])
def api_x_oauth_redirect():
    """Landing page X redirects to after authorization. Hands the code/state
    back to the opener window so the connect flow can finish automatically."""
    code = request.args.get('code', '')
    state = request.args.get('state', '')
    error = request.args.get('error', '')
    payload = json.dumps({'type': 'x_oauth', 'code': code, 'state': state, 'error': error})
    html = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>X Authorization</title>
<style>body{background:#0d0d0f;color:#f4f4f5;font-family:system-ui,sans-serif;display:flex;
align-items:center;justify-content:center;min-height:100vh;text-align:center;padding:24px}
.card{max-width:420px}a{color:#818cf8}</style></head><body><div class="card">
<h2>%s</h2><p id="msg">Returning you to the bot…</p>
<p style="font-size:13px;color:#71717a">If this window doesn't close, copy this page's URL and
paste it back into the bot.</p></div>
<script>
var data = %s;
try { if (window.opener) { window.opener.postMessage(data, '*'); document.getElementById('msg').textContent='Connected — you can close this window.'; setTimeout(function(){window.close();}, 1200); } }
catch(e){}
</script></body></html>""" % (
        'Authorization failed' if error else 'Authorized ✓', payload)
    return html, (400 if error else 200), {'Content-Type': 'text/html; charset=utf-8'}


@app.route('/api/x/status', methods=['GET'])
def api_x_status():
    # Connection state for every persona at once, so it carries no slug of its
    # own to scope on: filter the answer instead of guarding the route.
    mine = owned_slugs()
    return jsonify({p: {'username': t.get('username', ''),
                        'connected': bool(t.get('access_token'))}
                    for p, t in _load_x_tokens().items()
                    if mine is None or p in mine})


@app.route('/api/x/disconnect', methods=['POST'])
@platform_scoped
def api_x_disconnect():
    persona = (request.json or {}).get('persona', '')
    _log_x_event('disconnect', persona=persona)
    tokens = _load_x_tokens()
    tokens.pop(persona, None)
    _save_x_tokens(tokens)
    return jsonify({'ok': True})


@app.route('/api/x/dm-debug')
@operator_only
def api_x_dm_debug():
    """Why a DM did or didn't get answered: what X returned, which events count
    as new, and the per-fan state the funnel runs on."""
    persona = (request.args.get('persona') or '').strip()
    t = (_load_x_tokens() or {}).get(persona) or {}
    out = {'persona': persona,
           'connected': bool(t.get('access_token')),
           'username': t.get('username', ''),
           'stored_user_id': t.get('user_id', ''),
           'behavior': _x_behavior(persona),
           'cta_url': (_phases_cta(persona) or {}).get('cta_url', ''),
           'seen_event_count': len(_x_seen_events(persona)),
           'fans': _x_fans(persona)}
    if not t.get('access_token'):
        out['verdict'] = 'No X account connected for this persona.'
        return jsonify(out)
    if not out['behavior'].get('enabled', True):
        out['verdict'] = 'Bot is switched off under "How she replies" on the X Bot tab.'
    try:
        raw = _x_call(persona, 'GET',
                      '/dm_events?dm_event.fields=id,text,sender_id,created_at,'
                      f'dm_conversation_id&event_types=MessageCreate&max_results=20')
    except Exception as e:
        out['read_error'] = str(e)[:300]
        out['verdict'] = out.get('verdict') or 'Reading DMs failed — see read_error.'
        return jsonify(out)
    events = raw.get('data', []) or []
    seen = _x_seen_events(persona)
    out['events'] = [{'id': e.get('id'), 'from': e.get('sender_id'),
                      'mine': e.get('sender_id') == t.get('user_id'),
                      'already_handled': e.get('id') in seen,
                      'at': e.get('created_at'), 'text': (e.get('text') or '')[:120]}
                     for e in events]
    out['event_count'] = len(events)
    out['new_incoming'] = sum(1 for e in out['events']
                              if not e['mine'] and not e['already_handled'])
    if not out.get('verdict'):
        if not events:
            out['verdict'] = 'X returned no DM events at all.'
        elif not out['new_incoming']:
            out['verdict'] = ('All returned events are either your own or already '
                              'answered — nothing new to reply to.')
        else:
            out['verdict'] = f"{out['new_incoming']} message(s) are due a reply on the next round."
    return jsonify(out)


@app.route('/api/x/poll', methods=['POST'])
@platform_scoped
def api_x_poll():
    """Read new DMs for a persona's connected X account and reply via Gemini."""
    data = request.json or {}
    persona = data.get('persona', 'lilly')
    _log_x_event('poll_dm', persona=persona)

    tokens = _load_x_tokens()
    t = tokens.get(persona)
    if not t or not t.get('access_token'):
        return jsonify({'ok': False, 'error': f'No X account connected for persona "{persona}". Connect one in the X tab first.'}), 400

    access_token = t['access_token']
    user_id = t.get('user_id', '')
    if not user_id:
        return jsonify({'ok': False, 'error': 'User ID missing. Reconnect the X account.'}), 400

    system_prompt = get_system_prompt(persona)

    dm_state_file = f'/tmp/x_dm_cursor_{persona}.json' if IS_VERCEL else os.path.join(BASE_DIR, f'.x_dm_cursor_{persona}.json')
    cursor_data = {}
    if os.path.exists(dm_state_file):
        with open(dm_state_file, 'r') as f:
            cursor_data = json.load(f)

    replied = 0
    errors = []

    try:
        path = f'/dm_events?dm_event.fields=id,text,sender_id,created_at,dm_conversation_id&event_types=MessageCreate&max_results=10'
        convs = _x_api('GET', path, access_token=access_token)
        events = convs.get('data', [])
        last_seen = cursor_data.get('last_event_id', '')

        new_last = last_seen
        for event in events:
            eid = event.get('id', '')
            sender = event.get('sender_id', '')
            text = event.get('text', '').strip()

            if sender == user_id:
                continue
            if eid == last_seen:
                break
            if not new_last:
                new_last = eid

            sender_name = _x_username_for(persona, sender)
            if text:
                _log_x_event('dm_in', persona=persona, x_username=sender_name, detail=text[:160])
                _log_x_message(persona, sender, sender_name, 'in', text)
            conv_id = event.get('dm_conversation_id') or event.get('conversation_id') or f'dm_{sender}'
            history_key = f'x_hist_{persona}_{sender}'
            hist_file = f'/tmp/{history_key}.json' if IS_VERCEL else os.path.join(BASE_DIR, f'.{history_key}.json')
            history = []
            if os.path.exists(hist_file):
                with open(hist_file, 'r') as f:
                    history = json.load(f)

            contents = [{'role': 'model' if m['role'] == 'bot' else 'user', 'parts': [{'text': m['content']}]} for m in history[-20:]]
            contents.append({'role': 'user', 'parts': [{'text': text}]})

            try:
                response = client.models.generate_content(
                    model=MODEL_NAME,
                    contents=contents,
                    config=_no_thinking(types.GenerateContentConfig(
                        system_instruction=system_prompt, temperature=0.85,
                        max_output_tokens=1024)),
                )
                reply_text = _gemini_text(response)
                if not reply_text:
                    continue

                # Send DM reply
                _x_api('POST', f'/dm_conversations/{conv_id}/messages',
                       access_token=access_token,
                       body={'text': reply_text})
                _log_x_message(persona, sender, sender_name, 'out', reply_text)

                history.append({'role': 'user', 'content': text})
                history.append({'role': 'bot', 'content': reply_text})
                with open(hist_file, 'w') as f:
                    json.dump(history[-40:], f)
                replied += 1
            except Exception as e:
                errors.append(str(e)[:120])

        cursor_data['last_event_id'] = new_last or last_seen
        with open(dm_state_file, 'w') as f:
            json.dump(cursor_data, f)

    except url_error.HTTPError as e:
        code = e.code
        if code == 401:
            return jsonify({'ok': False, 'error': 'X access token expired. Reconnect the account.'}), 400
        return jsonify({'ok': False, 'error': f'X API error {code}'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 400

    return jsonify({'ok': True, 'replied': replied, 'errors': errors})


# ── X.com engagement bot (comment / follow / unfollow / chat-up) ───────────────

def _x_me_id(persona):
    tokens = _load_x_tokens()
    t = tokens.get(persona) or {}
    uid = t.get('user_id')
    if uid:
        return uid
    me = _x_call(persona, 'GET', '/users/me')
    uid = (me.get('data') or {}).get('id', '')
    if uid:
        t['user_id'] = uid
        tokens[persona] = t
        _save_x_tokens(tokens)
    return uid


@app.route('/api/x/follow', methods=['POST'])
@platform_scoped
def api_x_follow():
    data = request.json or {}
    persona = data.get('persona', '')
    target = (data.get('target') or '').strip()
    if not persona or not target:
        return jsonify({'ok': False, 'error': 'persona and target are required'}), 400
    try:
        me_id = _x_me_id(persona)
        user = _x_resolve_user(persona, target)
        _log_x_event('follow', persona=persona, detail=target)
        _x_call(persona, 'POST', f'/users/{me_id}/following', body={'target_user_id': user['id']})
        return jsonify({'ok': True, 'username': user['username']})
    except url_error.HTTPError as e:
        return jsonify({'ok': False, 'error': f'X API error {e.code}: {e.read()[:200].decode(errors="ignore")}'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 400


@app.route('/api/x/unfollow', methods=['POST'])
@platform_scoped
def api_x_unfollow():
    data = request.json or {}
    persona = data.get('persona', '')
    target = (data.get('target') or '').strip()
    if not persona or not target:
        return jsonify({'ok': False, 'error': 'persona and target are required'}), 400
    try:
        me_id = _x_me_id(persona)
        user = _x_resolve_user(persona, target)
        _log_x_event('unfollow', persona=persona, detail=target)
        _x_call(persona, 'DELETE', f'/users/{me_id}/following/{user["id"]}')
        return jsonify({'ok': True, 'username': user['username']})
    except url_error.HTTPError as e:
        return jsonify({'ok': False, 'error': f'X API error {e.code}: {e.read()[:200].decode(errors="ignore")}'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 400


@app.route('/api/x/comment', methods=['POST'])
@platform_scoped
def api_x_comment():
    """Reply in-character to the comments (replies) under a given post.
    Body: {persona, post (url or id), limit, preview}. If preview is true,
    drafts replies without posting them."""
    data = request.json or {}
    persona = data.get('persona', '')
    post = (data.get('post') or '').strip()
    limit = max(1, min(int(data.get('limit', 3)), 10))
    preview = bool(data.get('preview', False))
    if not persona or not post:
        return jsonify({'ok': False, 'error': 'persona and post are required'}), 400

    try:
        _log_x_event('comment', persona=persona, detail=post)
        results = _x_comment_round(persona, post, limit, preview=preview)
        return jsonify({'ok': True, 'count': len(results), 'replies': results, 'preview': preview})
    except url_error.HTTPError as e:
        return jsonify({'ok': False, 'error': f'X API error {e.code}: {e.read()[:200].decode(errors="ignore")}'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 400


@app.route('/api/x/post', methods=['POST'])
@platform_scoped
def api_x_post():
    """Generate and post an original in-character tweet for a persona.
    Body: {persona, topic (optional), text (override), preview}. With preview,
    returns the drafted tweet without posting."""
    data = request.json or {}
    persona = data.get('persona', '')
    topic = (data.get('topic') or '').strip()
    text = (data.get('text') or '').strip()
    preview = bool(data.get('preview', False))
    if not persona:
        return jsonify({'ok': False, 'error': 'persona is required'}), 400
    try:
        if not text:
            text = _x_generate_post(persona, topic)
        if not text:
            return jsonify({'ok': False, 'error': 'Could not generate a post.'}), 400
        posted = False
        if not preview:
            _log_x_event('post', persona=persona, detail=text[:80])
            _x_call(persona, 'POST', '/tweets', body={'text': text})
            posted = True
        return jsonify({'ok': True, 'text': text, 'posted': posted, 'preview': preview})
    except url_error.HTTPError as e:
        return jsonify({'ok': False, 'error': f'X API error {e.code}: {e.read()[:200].decode(errors="ignore")}'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 400


@app.route('/api/x/respond-own', methods=['POST'])
@platform_scoped
def api_x_respond_own():
    """Reply in-character to new comments across the persona's own recent posts.
    Body: {persona, limit, preview}."""
    data = request.json or {}
    persona = data.get('persona', '')
    limit = max(1, min(int(data.get('limit', 5)), 10))
    preview = bool(data.get('preview', False))
    if not persona:
        return jsonify({'ok': False, 'error': 'persona is required'}), 400
    try:
        _log_x_event('respond-own', persona=persona)
        results = []
        remaining = limit
        for tid in _x_my_recent_tweet_ids(persona, 5):
            if remaining <= 0:
                break
            r = _x_comment_round(persona, tid, remaining, preview=preview, skip_seen=not preview)
            results += r
            remaining -= len(r)
        return jsonify({'ok': True, 'count': len(results), 'replies': results, 'preview': preview})
    except url_error.HTTPError as e:
        return jsonify({'ok': False, 'error': f'X API error {e.code}: {e.read()[:200].decode(errors="ignore")}'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 400


@app.route('/api/x/chat-up', methods=['POST'])
@platform_scoped
def api_x_chat_up():
    """Open a DM with a target user using an in-character opener.
    Body: {persona, target, note (optional context), opener (optional override),
    preview}. Returns the opener; sends it unless preview is true."""
    data = request.json or {}
    persona = data.get('persona', '')
    target = (data.get('target') or '').strip()
    note = (data.get('note') or '').strip()
    opener = (data.get('opener') or '').strip()
    preview = bool(data.get('preview', False))
    if not persona or not target:
        return jsonify({'ok': False, 'error': 'persona and target are required'}), 400

    try:
        user = _x_resolve_user(persona, target)
        _log_x_event('chat_up', persona=persona, detail=target)
        if not opener:
            ctx = f' Here is some context about them: {note}.' if note else ''
            instruction = (
                "Write a warm, natural opening DM to start a conversation with a fan "
                "named @" + user['username'] + " on X." + ctx +
                " In-character, curious about them, no hard selling, no hashtags. "
                "Make them want to reply.")
            opener = _persona_text(persona, instruction, max_tokens=1024, temperature=0.95)
        if not opener:
            return jsonify({'ok': False, 'error': 'Could not generate an opener.'}), 400

        sent = False
        if not preview:
            _x_call(persona, 'POST', f'/dm_conversations/with/{user["id"]}/messages',
                    body={'text': opener})
            sent = True
            _x_record_opener(persona, user['id'])
            _log_x_message(persona, user['id'], user['username'], 'out', opener)
            history_key = f'x_hist_{persona}_{user["id"]}'
            hist_file = f'/tmp/{history_key}.json' if IS_VERCEL else os.path.join(BASE_DIR, f'.{history_key}.json')
            try:
                with open(hist_file, 'w') as f:
                    json.dump([{'role': 'bot', 'content': opener}], f)
            except Exception:
                pass
        return jsonify({'ok': True, 'username': user['username'], 'opener': opener, 'sent': sent})
    except url_error.HTTPError as e:
        return jsonify({'ok': False, 'error': f'X API error {e.code}: {e.read()[:200].decode(errors="ignore")}'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 400


@app.route('/api/x/auto-run', methods=['POST'])
@platform_scoped
def api_x_auto_run():
    """Run one autonomous engagement round as a persona, mirroring the manual
    flow: keep existing DMs going, reply to a post's comments, then find new
    people and chat them up (optionally following them first). The frontend
    calls this on a loop so the bot keeps finding new chats.
    Body: {persona, query, post, new_chat_limit, comment_limit, post_topic,
           follow, dm_replies, new_chats, comments, respond_own, post_content}."""
    data = request.json or {}
    persona = data.get('persona', '')
    query = (data.get('query') or '').strip()
    post = (data.get('post') or '').strip()
    post_topic = (data.get('post_topic') or '').strip()
    new_chat_limit = max(0, min(int(data.get('new_chat_limit', 2)), 5))
    comment_limit = max(0, min(int(data.get('comment_limit', 0)), 5))
    do_follow = bool(data.get('follow', False))
    do_dm = bool(data.get('dm_replies', True))
    do_new = bool(data.get('new_chats', True))
    do_comments = bool(data.get('comments', True))
    do_respond_own = bool(data.get('respond_own', False))
    do_post = bool(data.get('post_content', False))
    if not persona:
        return jsonify({'ok': False, 'error': 'persona is required'}), 400

    tokens = _load_x_tokens()
    if not (tokens.get(persona) or {}).get('access_token'):
        return jsonify({'ok': False, 'error': f'No X account connected for persona "{persona}".'}), 400

    _log_x_event('auto-run', persona=persona, detail=query or post)
    actions = {'dm_replies': 0, 'followups': 0, 'new_chats': 0, 'follows': 0,
               'comments': 0, 'posts': 0, 'post_comments': 0, 'reply_answers': 0,
               'likes': 0}
    log = []
    try:
        if do_post:
            try:
                text = _x_generate_post(persona, post_topic)
                if text:
                    _x_call(persona, 'POST', '/tweets', body={'text': text})
                    _log_x_event('post', persona=persona, detail=text[:80])
                    actions['posts'] += 1
                    log.append(f'Posted: {text[:60]}')
            except Exception as e:
                log.append(f'Post failed: {str(e)[:80]}')

        if bool(data.get('feed_engage')):
            fa, flog = _x_feed_engage_round(
                persona,
                post_limit=max(0, min(int(data.get('feed_post_limit', 4)), 15)),
                reply_limit=max(0, min(int(data.get('feed_reply_limit', 8)), 25)),
                post_age_min=data.get('post_age_min'),
                reply_age_min=data.get('reply_age_min'),
                do_posts=bool(data.get('feed_comment_posts', True)),
                do_replies=bool(data.get('feed_answer_replies', True)),
                do_likes=bool(data.get('feed_like_replies', True)))
            actions.update(fa)
            log += flog

        if do_dm:
            replied, dlog = _x_dm_reply_round(persona)
            actions['dm_replies'] = replied
            log += dlog
            nudged, flog = _x_followup_round(persona)
            actions['followups'] = nudged
            log += flog

        if comment_limit and (do_comments and post or do_respond_own):
            targets = []
            if do_comments and post:
                targets.append(post)
            if do_respond_own:
                try:
                    targets += _x_my_recent_tweet_ids(persona, 5)
                except Exception as e:
                    log.append(f'Own-posts lookup failed: {str(e)[:80]}')
            remaining = comment_limit
            for tid in targets:
                if remaining <= 0:
                    break
                try:
                    res = _x_comment_round(persona, tid, remaining, skip_seen=True)
                    posted = [r for r in res if r.get('posted')]
                    actions['comments'] += len(posted)
                    remaining -= len(posted)
                    for r in posted:
                        log.append(f"Comment reply → {r['to']}: {r['reply'][:50]}")
                except Exception as e:
                    log.append(f'Comment round failed: {str(e)[:80]}')

        if do_new and new_chat_limit:
            contacted = set(_x_load_json(_x_state_path(persona, 'contacted'), []))
            contacted |= _x_known_user_ids(persona)
            contacted |= _x_opener_ids(persona)
            try:
                candidates = _x_audience_candidates(
                    persona, new_chat_limit, contacted,
                    post_age_min=data.get('post_age_min'),
                    reply_age_min=data.get('reply_age_min'))
            except Exception as e:
                candidates = []
                log.append(f'Finding people failed: {str(e)[:80]}')
            me_id = _x_me_id(persona) if (candidates and do_follow) else None
            for u in candidates:
                try:
                    # Final dedup: skip if we already have ANY messages with this user in DB
                    if _x_messaged_before(persona, u['id']):
                        continue
                    if do_follow and me_id:
                        try:
                            _x_call(persona, 'POST', f'/users/{me_id}/following',
                                    body={'target_user_id': u['id']})
                            actions['follows'] += 1
                            log.append(f"Followed @{u['username']}")
                        except Exception:
                            pass
                    snippet = (u.get('tweet') or '')[:160]
                    # Spelled out because the model otherwise writes these as
                    # replies — "nice to see you pop up in my dms" to someone who
                    # has never messaged her.
                    cold_rule = (
                        " THIS PERSON HAS NEVER MESSAGED YOU. You are messaging them "
                        "first, out of the blue. Do not thank them for anything, do not "
                        "reference them writing to you, appearing in your DMs, replying, "
                        "or 'popping up' — none of that happened. Do not greet them as if "
                        "you already know each other. No hashtags, no hard sell.")
                    if snippet:
                        instruction = (
                            f"Write the first-ever DM to @{u['username']} on X. They recently "
                            f"posted: \"{snippet}\". Open with something warm and in-character "
                            "that reacts to that post, and ask one question to get them "
                            "talking." + cold_rule)
                    else:
                        instruction = (
                            f"Write the first-ever DM to @{u['username']} on X — they are "
                            "someone you found, not someone who contacted you. Open with "
                            "something warm and in-character, curious about them, and ask "
                            "one question to get them talking." + cold_rule)
                    opener = _persona_text(persona, instruction, max_tokens=1024, temperature=0.95)
                    opener = _x_fix_cold_opener(persona, opener, instruction)
                    if opener:
                        _x_call(persona, 'POST',
                                f'/dm_conversations/with/{u["id"]}/messages',
                                body={'text': opener})
                        actions['new_chats'] += 1
                        _x_record_opener(persona, u['id'])
                        _x_save_history(persona, u['id'], [{'role': 'bot', 'content': opener}])
                        _log_x_message(persona, u['id'], u.get('username', ''), 'out', opener)
                        log.append(f"New chat → @{u['username']}: {opener[:50]}")
                except Exception as e:
                    log.append(f"@{u['username']} failed: {str(e)[:200]}")
                finally:
                    contacted.add(u['id'])
            _x_save_json(_x_state_path(persona, 'contacted'), list(contacted)[-1000:])

        if _last_x_log_error[0]:
            log.append(f'⚠ conversation logging failed: {_last_x_log_error[0]}')
        return jsonify({'ok': True, 'actions': actions, 'log': log,
                        'log_error': _last_x_log_error[0]})
    except url_error.HTTPError as e:
        body = e.read()[:200].decode(errors='ignore')
        if e.code == 401:
            return jsonify({'ok': False, 'error': 'X token expired. Reconnect the account.'}), 400
        return jsonify({'ok': False, 'error': f'X API error {e.code}: {body}'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 400


# ── Fanvue chatbot ────────────────────────────────────────────────────────────
# Fanvue is an OnlyFans-style platform. This mirrors the X bot: connect an
# account via OAuth 2.0 + PKCE, reply to fan DMs in the persona's voice, run the
# conversion funnel, log conversations, and open new-fan chats. One Fanvue app
# (client_id + optional secret) is the platform's own, registered once in the
# Fanvue Builder and supplied through the server environment; a creator only
# picks a persona and authorizes. Each persona then connects its own creator
# account and gets its own tokens.

FANVUE_API_BASE = 'https://api.fanvue.com'
FANVUE_AUTH_URL = 'https://auth.fanvue.com/oauth2/auth'
FANVUE_TOKEN_URL = 'https://auth.fanvue.com/oauth2/token'
FANVUE_API_VERSION = '2025-06-26'
# Everything the bot itself needs; Fanvue rejects the whole authorization with
# invalid_scope if the app registration is not granted one of them, so the
# connect flow can retry with the core set alone.
# Without these the bot cannot read or answer a DM at all, so they are never
# dropped to get past an invalid_scope — a refusal there is reported, not
# retried around.
FANVUE_REQUIRED_SCOPES = 'openid read:self read:chat write:chat'
# Refresh tokens need offline/offline_access, but a login that lasts one access
# token is still better than no connection, so they sit above the floor.
FANVUE_CORE_SCOPES = FANVUE_REQUIRED_SCOPES + ' offline offline_access'
FANVUE_SCOPES = (FANVUE_CORE_SCOPES + ' read:fan read:media write:media '
                 'read:creator read:agency '
                 # /earnings backs the purchase reconciler.
                 'read:insights')


def _fanvue_app():
    """The platform-level OAuth app. The environment wins over the legacy
    settings rows an operator once typed into the connect form, and the redirect
    URI falls back to this deployment's own callback so nothing has to be
    registered per install beyond the Fanvue app itself."""
    redirect = (os.environ.get('FANVUE_REDIRECT_URI', '')
                or _get_setting('fanvue_redirect_uri') or '')
    if not redirect:
        try:
            redirect = _callback_origin() + '/api/fanvue/oauth-redirect'
        except Exception:
            redirect = ''
    return {
        'client_id': os.environ.get('FANVUE_CLIENT_ID', '') or _get_setting('fanvue_client_id') or '',
        'client_secret': os.environ.get('FANVUE_CLIENT_SECRET', '') or _get_setting('fanvue_client_secret') or '',
        'redirect_uri': redirect,
    }


def _fanvue_denied_scopes():
    """Scopes this deployment's Fanvue app registration has been refused. Fanvue
    answers invalid_scope for the whole authorization when one optional scope is
    not granted, so once we learn a name we stop asking for it — otherwise every
    creator pays for the same discovery again."""
    try:
        v = json.loads(_get_setting('fanvue_denied_scopes') or '[]')
    except Exception:
        return []
    return [s for s in v if isinstance(s, str) and s]


def _fanvue_remember_denied(scopes):
    cur = _fanvue_denied_scopes()
    required = set(FANVUE_REQUIRED_SCOPES.split())
    add = [s for s in scopes if s and s not in cur and s not in required]
    if add:
        cur = cur + add
        _set_setting('fanvue_denied_scopes', json.dumps(cur))
    return cur


def _fanvue_scopes(minimal=False, drop=()):
    base = FANVUE_CORE_SCOPES if minimal else (
        os.environ.get('FANVUE_SCOPES', '').strip()
        or (_get_setting('fanvue_scopes') or '').strip()
        or FANVUE_SCOPES)
    skip = set(_fanvue_denied_scopes()) | {s for s in (drop or []) if s}
    skip -= set(FANVUE_REQUIRED_SCOPES.split())
    out = []
    for s in FANVUE_REQUIRED_SCOPES.split() + base.split():
        if s not in out and s not in skip:
            out.append(s)
    return ' '.join(out)


def _fanvue_scope_tiers():
    """Widest first. Each step gives up something the bot can live without:
    the optional read scopes, then refresh tokens."""
    tiers = [_fanvue_scopes(), _fanvue_scopes(minimal=True),
             _fanvue_scopes(minimal=True, drop=['offline', 'offline_access'])]
    seen, out = set(), []
    for t in tiers:
        key = frozenset(t.split())
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def _fanvue_parse_bad_scopes(description, requested):
    """The scope names Fanvue's invalid_scope message blames. Only words that we
    actually asked for count, so nothing in the prose can be mistaken for one."""
    req = [s for s in (requested or '').split() if s]
    out = []
    for tok in re.findall(r'[A-Za-z0-9:._*-]+', description or ''):
        if tok in req and tok not in out:
            out.append(tok)
    return out


_fanvue_lock_guard = threading.Lock()
_fanvue_round_locks = {}
_fanvue_refresh_locks = {}


def _fanvue_persona_lock(store, persona):
    with _fanvue_lock_guard:
        lk = store.get(persona)
        if lk is None:
            lk = store[persona] = threading.Lock()
        return lk


def _fanvue_tokens(persona):
    try:
        return json.loads(_get_setting(f'fanvue_tokens_{persona}') or '{}')
    except Exception:
        return {}


def _fanvue_save_tokens(persona, tokens):
    _set_setting(f'fanvue_tokens_{persona}', json.dumps(tokens))


def _fanvue_creator(persona):
    """The selected creator profile for logins that manage several. Returns
    {'uuid':..., 'handle':...} or {} when acting as the primary account."""
    try:
        return json.loads(_get_setting(f'fanvue_creator_{persona}') or '{}')
    except Exception:
        return {}


def _fanvue_scope(persona):
    """URL prefix that scopes chat/message calls to the selected creator, or ''
    when the login has a single profile (acts as the primary account)."""
    uuid = _fanvue_creator(persona).get('uuid')
    return f'/creators/{uuid}' if uuid else ''


def _fanvue_token_post(params):
    """POST to the Fanvue token endpoint. A confidential app authenticates with
    HTTP Basic (client_secret_basic, the only method Fanvue accepts); an app
    registered with auth method "none" carries no secret at all and is
    authenticated by the PKCE code_verifier, with client_id in the body."""
    a = _fanvue_app()
    headers = {'Content-Type': 'application/x-www-form-urlencoded',
               'Accept': 'application/json'}
    body = dict(params)
    if a['client_secret']:
        creds = f"{a['client_id']}:{a['client_secret']}"
        headers['Authorization'] = 'Basic ' + __import__('base64').b64encode(creds.encode()).decode()
    else:
        body['client_id'] = a['client_id']
    data = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request(
        FANVUE_TOKEN_URL, data=data, method='POST', headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _fanvue_refresh(persona):
    """Refresh a persona's Fanvue access token. Returns the new token or None.

    Serialised per persona: Fanvue rotates the refresh token, so two threads
    redeeming the same one would invalidate the account's connection."""
    lock = _fanvue_persona_lock(_fanvue_refresh_locks, persona)
    before = _fanvue_tokens(persona).get('access_token')
    with lock:
        t = _fanvue_tokens(persona)
        # Another thread refreshed while we waited — reuse its token.
        if t.get('access_token') and t.get('access_token') != before:
            return t['access_token']
        app_creds = _fanvue_app()
        rt = t.get('refresh_token')
        if not rt or not app_creds['client_id'] or not app_creds['client_secret']:
            return None
        try:
            td = _fanvue_token_post({'grant_type': 'refresh_token', 'refresh_token': rt})
        except Exception:
            return None
        t['access_token'] = td.get('access_token', t.get('access_token'))
        if td.get('refresh_token'):
            t['refresh_token'] = td['refresh_token']
        _fanvue_save_tokens(persona, t)
        return t['access_token']


def _fanvue_api(method, path, access_token, body=None):
    url = FANVUE_API_BASE + path
    headers = {'Authorization': f'Bearer {access_token}',
               'X-Fanvue-API-Version': FANVUE_API_VERSION,
               'Content-Type': 'application/json', 'Accept': 'application/json'}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except url_error.HTTPError as e:
        resp_body = ''
        try:
            resp_body = e.read().decode()[:500]
        except Exception:
            pass
        logging.warning('Fanvue API %s %s → %s: %s', method, url, e.code, resp_body)
        raise


def _fanvue_call(persona, method, path, body=None):
    """Call the Fanvue API as a persona, refreshing the token once on 401."""
    t = _fanvue_tokens(persona)
    at = t.get('access_token')
    if not at:
        raise RuntimeError(f'No Fanvue account connected for persona "{persona}".')
    try:
        return _fanvue_api(method, path, at, body=body)
    except url_error.HTTPError as e:
        if e.code == 401:
            new = _fanvue_refresh(persona)
            if new:
                return _fanvue_api(method, path, new, body=body)
        raise


@app.route('/api/fanvue/dbinfo')
@operator_only
def api_fanvue_dbinfo():
    """Report which DB backend is in use so persistence can be verified: an
    external Postgres survives redeploys; the SQLite fallback does not."""
    # Report the URL the app is ACTUALLY using (db.py may build it from the
    # Cloud SQL env vars), not just a raw DATABASE_URL env read.
    try:
        from db import DATABASE_URL as eff
    except Exception:
        eff = os.environ.get('DATABASE_URL', '')
    is_sqlite = eff.startswith('sqlite')
    backend = 'sqlite-ephemeral' if is_sqlite else ('postgres' if 'postgres' in eff else 'external')
    # Live connectivity check.
    db_ok, db_err = False, ''
    try:
        from db import SessionLocal
        from sqlalchemy import text as _text
        s = SessionLocal()
        try:
            s.execute(_text('SELECT 1'))
            db_ok = True
        finally:
            s.close()
    except Exception as e:
        db_err = str(e)[:200]
    persona = (request.args.get('persona') or 'lilly').strip()
    # Mask credentials in the URL before returning it.
    masked = eff
    if '@' in eff and '//' in eff:
        head, tail = eff.split('//', 1)
        if '@' in tail:
            masked = head + '//***@' + tail.split('@', 1)[1]
    return jsonify({
        'backend': backend,
        'persists_across_redeploys': not is_sqlite,
        'effective_url': masked,
        'db_connect_ok': db_ok,
        'db_error': db_err,
        'saw_cloud_sql_env': bool(os.environ.get('CLOUD_SQL_CONNECTION_NAME') or os.environ.get('INSTANCE_CONNECTION_NAME')),
        'saw_database_url_env': bool(os.environ.get('DATABASE_URL')),
        'ppv_setting_present': bool(_get_setting(f'fanvue_ppv_{persona}')),
        'ppv_raw': (_get_setting(f'fanvue_ppv_{persona}') or '')[:500],
    })


@app.route('/api/fanvue/vault-debug')
@operator_only
def api_fanvue_vault_debug():
    """Probe a wide set of Fanvue media/vault endpoints and record the exact
    status + response body of each, so we can pin the working path/scope. Also
    writes the result to a log file for later inspection."""
    persona = (request.args.get('persona') or '').strip()
    scope = _fanvue_scope(persona)
    paths = [
        '/media/folders?page=1&size=100',
        f'{scope}/media/folders?page=1&size=100',
        '/media/folders',
        '/media?size=1',
        '/users/me',
    ]
    results = []
    first_uuid = ''
    for p in paths:
        entry = {'path': p}
        try:
            r = _fanvue_call(persona, 'GET', p)
            body = json.dumps(r) if not isinstance(r, str) else r
            entry.update(status=200, body=body[:900])
            if p.startswith('/media?'):
                lst = _fv_list(r)
                if lst:
                    first_uuid = _fv_first(lst[0], 'uuid', 'id', default='')
        except url_error.HTTPError as e:
            try:
                rb = e.read()[:400].decode(errors='ignore')
            except Exception:
                rb = ''
            entry.update(status=e.code, body=rb)
        except Exception as e:
            entry.update(status='ERR', body=str(e)[:200])
        results.append(entry)
    # Full single-media item to see whether it carries url/variants for preview.
    if first_uuid:
        entry = {'path': f'/media/{first_uuid}'}
        try:
            r = _fanvue_call(persona, 'GET', f'/media/{first_uuid}')
            entry.update(status=200, body=json.dumps(r)[:1500])
        except url_error.HTTPError as e:
            try:
                rb = e.read()[:400].decode(errors='ignore')
            except Exception:
                rb = ''
            entry.update(status=e.code, body=rb)
        except Exception as e:
            entry.update(status='ERR', body=str(e)[:200])
        results.append(entry)
    out = {'persona': persona, 'scope': scope, 'results': results}
    try:
        with open('/tmp/fanvue_vault_debug.json', 'w') as f:
            json.dump(out, f, indent=2)
    except Exception:
        pass
    return jsonify(out)


@app.route('/api/fanvue/config')
def api_fanvue_config():
    """Whether this deployment has a Fanvue app registered, plus the redirect URI
    to register for it. Never returns the client secret value. Not operator_only:
    a creator's connect flow reads this to know it can start, and it carries
    nothing more sensitive than has_secret."""
    if not _current_user():
        return jsonify({'error': 'Unauthorized'}), 401
    a = _fanvue_app()
    return jsonify({'configured': bool(a['client_id']), 'redirect_uri': a['redirect_uri'],
                    'has_secret': bool(a['client_secret']),
                    'scopes': _fanvue_scopes(), 'denied_scopes': _fanvue_denied_scopes()})


@app.route('/api/fanvue/auth-url', methods=['POST'])
@platform_scoped
def api_fanvue_auth_url():
    """Build the Fanvue OAuth 2.0 + PKCE authorization URL for a persona. The
    app credentials come from the server, so the caller sends only a persona."""
    data = request.json or {}
    persona = (data.get('persona') or '').strip()
    if not persona:
        return jsonify({'ok': False, 'error': 'persona is required'}), 400
    a = _fanvue_app()
    if not a['client_id']:
        return jsonify({'ok': False, 'error': 'No Fanvue app is configured on this server. '
                        'Set FANVUE_CLIENT_ID (and FANVUE_CLIENT_SECRET) in the environment.'}), 400
    redirect_uri = a['redirect_uri']
    if not redirect_uri:
        return jsonify({'ok': False, 'error': 'No redirect URI could be determined. '
                        'Set PUBLIC_BASE_URL or FANVUE_REDIRECT_URI in the environment.'}), 400
    drop = data.get('drop') or []
    if isinstance(drop, str):
        drop = drop.split()
    scope = _fanvue_scopes(minimal=bool(data.get('minimal')), drop=drop)

    code_verifier = secrets.token_urlsafe(64)
    code_challenge = __import__('base64').urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()).rstrip(b'=').decode()
    state = secrets.token_urlsafe(32)
    _set_setting(f'fanvue_oauth_{persona}',
                 json.dumps({'v': code_verifier, 'state': state, 'redirect_uri': redirect_uri}))

    params = urllib.parse.urlencode({
        'response_type': 'code', 'client_id': a['client_id'], 'redirect_uri': redirect_uri,
        'scope': scope, 'state': state,
        'code_challenge': code_challenge, 'code_challenge_method': 'S256',
        'prompt': 'login',
    })
    return jsonify({'ok': True, 'url': f'{FANVUE_AUTH_URL}?{params}',
                    'redirect_uri': redirect_uri, 'scope': scope})


@app.route('/api/fanvue/scope-error', methods=['POST'])
@platform_scoped
def api_fanvue_scope_error():
    """Turn an invalid_scope refusal into the next authorization to try.

    Fanvue rejects the whole authorization when the app registration is not
    granted one optional scope, and names it in error_description. We drop the
    named scope for good, or — when nothing is named — step down to the next
    narrower tier. A refusal naming a scope the bot cannot work without is
    reported instead, because retrying it would just loop."""
    data = request.json or {}
    requested = (data.get('requested') or '').strip() or _fanvue_scopes()
    detail = ' '.join(str(data.get(k) or '') for k in
                      ('error_description', 'error_hint', 'error_debug'))
    bad = _fanvue_parse_bad_scopes(detail, requested)
    required = set(FANVUE_REQUIRED_SCOPES.split())
    blocked = [s for s in bad if s in required]
    if blocked:
        return jsonify({'ok': False, 'retry': False, 'blocked': blocked,
                        'error': 'Fanvue refused permissions the bot cannot work without ('
                                 + ', '.join(blocked) + '). Ask Fanvue to grant them to this '
                                 'app registration.'}), 400
    if bad:
        _fanvue_remember_denied(bad)
        nxt = _fanvue_scopes()
        return jsonify({'ok': True, 'retry': set(nxt.split()) != set(requested.split()),
                        'dropped': bad, 'scope': nxt})

    asked = set(requested.split())
    for tier in _fanvue_scope_tiers():
        got = set(tier.split())
        if got < asked:
            return jsonify({'ok': True, 'retry': True, 'scope': tier,
                            'dropped': sorted(asked - got),
                            'drop': sorted(asked - got)})
    return jsonify({'ok': True, 'retry': False, 'scope': requested, 'dropped': [],
                    'error': 'Fanvue refused the minimum set of permissions this bot needs.'})


@app.route('/api/fanvue/scope-reset', methods=['POST'])
@platform_scoped
def api_fanvue_scope_reset():
    """Forget the learned refusals, so a registration that has since been
    granted more scopes is asked for the full set again."""
    _set_setting('fanvue_denied_scopes', '[]')
    return jsonify({'ok': True, 'scope': _fanvue_scopes()})


@app.route('/api/fanvue/oauth-redirect')
def api_fanvue_oauth_redirect():
    code = request.args.get('code', '')
    state = request.args.get('state', '')
    error = request.args.get('error', '')
    # invalid_scope only says which scope it means in the description, and the
    # connect flow needs that name to drop it and retry.
    detail = ' '.join(request.args.get(k, '') for k in
                      ('error_description', 'error_hint', 'error_debug')).strip()
    payload = json.dumps({'type': 'fanvue_oauth', 'code': code, 'state': state,
                          'error': error, 'error_description': detail})
    return Response(
        '<!DOCTYPE html><html><body style="background:#0d0d0f;color:#e7e9ee;'
        'font-family:system-ui;padding:40px;text-align:center">'
        '<p>Finishing Fanvue connection… you can close this window.</p><script>'
        f'try{{window.opener&&window.opener.postMessage({payload},"*");}}catch(e){{}}'
        'setTimeout(function(){window.close();},400);</script></body></html>',
        mimetype='text/html')


@app.route('/api/fanvue/callback', methods=['POST'])
@platform_scoped
def api_fanvue_callback():
    data = request.json or {}
    persona = (data.get('persona') or '').strip()
    code = (data.get('code') or '').strip()
    state = (data.get('state') or '').strip()
    if not (persona and code and state):
        return jsonify({'ok': False, 'error': 'persona, code and state are required'}), 400
    st = {}
    try:
        st = json.loads(_get_setting(f'fanvue_oauth_{persona}') or '{}')
    except Exception:
        pass
    if not st or state != st.get('state'):
        return jsonify({'ok': False, 'error': 'State mismatch — restart the connection.'}), 400
    a = _fanvue_app()
    try:
        td = _fanvue_token_post({
            'grant_type': 'authorization_code', 'code': code,
            'redirect_uri': st.get('redirect_uri', a['redirect_uri']),
            'code_verifier': st.get('v', ''),
        })
    except url_error.HTTPError as e:
        return jsonify({'ok': False, 'error': f'Token exchange failed {e.code}: {e.read()[:200].decode(errors="ignore")}'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 400

    tokens = {'access_token': td.get('access_token', ''), 'refresh_token': td.get('refresh_token', ''),
              'scope': td.get('scope', '')}
    _fanvue_save_tokens(persona, tokens)
    username = ''
    try:
        me = _fanvue_call(persona, 'GET', '/users/me')
        username = me.get('handle') or me.get('username') or (me.get('data') or {}).get('handle', '')
        if username:
            tokens['username'] = username
            _fanvue_save_tokens(persona, tokens)
    except Exception:
        pass
    _log_x_event('fanvue_connect', persona=persona, x_username=username)
    return jsonify({'ok': True, 'username': username})


@app.route('/api/fanvue/status')
@platform_scoped
def api_fanvue_status():
    persona = (request.args.get('persona') or '').strip()
    t = _fanvue_tokens(persona)
    return jsonify({'connected': bool(t.get('access_token')), 'username': t.get('username', ''),
                    'creator': _fanvue_creator(persona)})


@app.route('/api/fanvue/disconnect', methods=['POST'])
@platform_scoped
def api_fanvue_disconnect():
    persona = (request.json or {}).get('persona', '').strip()
    if persona:
        _set_setting(f'fanvue_tokens_{persona}', '{}')
        _set_setting(f'fanvue_cursor_{persona}', '{}')
        _set_setting(f'fanvue_creator_{persona}', '{}')
    return jsonify({'ok': True})


@app.route('/api/fanvue/creators')
@platform_scoped
def api_fanvue_creators():
    """List creator profiles this login can act as. For a single-profile login
    the agency endpoint 404s, so we fall back to the primary /users/me."""
    persona = (request.args.get('persona') or '').strip()
    creators = []
    agency_err = ''
    try:
        rows = _fv_list(_fanvue_call(persona, 'GET', '/agency/creators?limit=50'))
        for r in rows:
            uid = _fv_first(r, 'uuid', 'id', 'creatorUuid', default='')
            handle = _fv_first(r, 'handle', 'username', 'displayName', default='')
            if uid:
                creators.append({'uuid': uid, 'handle': handle})
    except Exception as e:
        agency_err = str(e)[:120]
    seen = {c['uuid'] for c in creators}
    try:
        me = _fanvue_call(persona, 'GET', '/users/me')
        d = me.get('data', me) if isinstance(me, dict) else {}
        uid = _fv_first(d, 'uuid', 'id', default='')
        handle = _fv_first(d, 'handle', 'username', default='')
        if uid and uid not in seen:
            creators.append({'uuid': uid, 'handle': handle})
    except Exception:
        pass
    # Keep a previously-chosen (manually entered) profile in the list even if the
    # API can't enumerate it, so the picker still shows it as selected.
    sel = _fanvue_creator(persona)
    if sel.get('uuid') and sel['uuid'] not in {c['uuid'] for c in creators}:
        creators.append({'uuid': sel['uuid'], 'handle': sel.get('handle', '')})
    return jsonify({'creators': creators, 'selected': sel,
                    'agency_error': agency_err,
                    'allow_manual': True})


@app.route('/api/fanvue/creator', methods=['POST'])
@platform_scoped
def api_fanvue_set_creator():
    """Select which creator profile the persona acts as; resets the poll cursor
    so the new account starts clean."""
    d = request.json or {}
    persona = (d.get('persona') or '').strip()
    if not persona:
        return jsonify({'ok': False, 'error': 'Missing persona'}), 400
    uuid = (d.get('uuid') or '').strip()
    handle = (d.get('handle') or '').strip()
    _set_setting(f'fanvue_creator_{persona}', json.dumps({'uuid': uuid, 'handle': handle}) if uuid else '{}')
    _set_setting(f'fanvue_cursor_{persona}', '{}')
    return jsonify({'ok': True, 'selected': _fanvue_creator(persona)})


def _fanvue_ppv(persona):
    try:
        return json.loads(_get_setting(f'fanvue_ppv_{persona}') or '{}')
    except Exception:
        return {}


# Three answers, not two. Fanvue has no endpoint that reports whether a given
# fan bought a given item, so payment is known only from what we recorded when
# the webhook, the message poll or a reconciliation sweep told us. That makes
# "we could not find out" a real state, and conflating it with "they did not
# pay" is what silently freezes a fan's ladder forever.
PPV_PAID, PPV_UNPAID, PPV_UNKNOWN = 'paid', 'unpaid', 'unknown'


def _fv_drop_state(persona, drop_id):
    """Whether the recorded drop was paid, and the drop row itself.

    Returns (state, drop). UNKNOWN means the ledger could not be read — hold,
    never advance and never reset on it."""
    if not drop_id:
        return PPV_UNPAID, None
    try:
        from db import SessionLocal, PpvDrop
    except Exception as e:
        logger.warning('PPV ledger unavailable for %s: %s', persona, str(e)[:120])
        return PPV_UNKNOWN, None
    try:
        s = SessionLocal()
    except Exception as e:
        logger.warning('PPV ledger unreachable for %s: %s', persona, str(e)[:120])
        return PPV_UNKNOWN, None
    try:
        d = s.get(PpvDrop, drop_id)
        if d is None:
            return PPV_UNPAID, None
        return (PPV_PAID if d.paid_at else PPV_UNPAID), d
    except Exception as e:
        logger.warning('PPV ledger read failed for %s: %s', persona, str(e)[:120])
        return PPV_UNKNOWN, None
    finally:
        try:
            s.close()
        except Exception:
            pass


def _fv_drop_is_stale(drop, stale_days):
    """An unbought drop stops blocking the ladder after the creator's window, so
    one unappealing photo can't end the funnel for a fan permanently. The row
    stays on file — this changes behaviour, it never forgets the drop."""
    if not drop or not stale_days or drop.paid_at:
        return False
    sent = drop.created_at
    if not sent:
        return False
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - sent) >= timedelta(days=int(stale_days))


def _fv_clean_tier(t):
    """One tier inside a set: the media, its price in cents, and its caption."""
    if not isinstance(t, dict):
        return None
    media = [str(x) for x in (t.get('media_uuids') or t.get('media') or []) if x]
    try:
        price = int(t.get('price') or 0)
    except (TypeError, ValueError):
        price = 0
    if not media or price < 300:
        return None
    return {'media_uuids': media, 'price': price,
            'caption': (t.get('caption') or '').strip()}


def _fv_clean_set(s, index=0):
    """Normalise one PPV content set. A set is a themed bundle — like an outfit
    — holding its own ordered tiers, plus the cues that decide when it fits:
    what she is doing in it, words that hint at it, and the hours it suits."""
    if not isinstance(s, dict):
        return None
    tiers = [t for t in (_fv_clean_tier(x) for x in (s.get('tiers') or [])) if t]
    if not tiers:
        # A set saved in the old flat shape is its own single tier.
        one = _fv_clean_tier(s)
        if not one:
            return None
        tiers = [one]
    kw = s.get('keywords')
    if isinstance(kw, str):
        kw = kw.split(',')
    keywords = [w.strip().lower() for w in (kw or []) if str(w).strip()][:30]

    def _hour(v):
        try:
            h = int(v)
        except (TypeError, ValueError):
            return None
        return h if 0 <= h <= 23 else None

    return {
        'id': str(s.get('id') or '').strip() or f'set{index + 1}',
        'name': (s.get('name') or f'Set {index + 1}').strip()[:60],
        'tiers': tiers,
        'scene': (s.get('scene') or '').strip()[:200],
        'keywords': keywords,
        'hour_from': _hour(s.get('hour_from')),
        'hour_to': _hour(s.get('hour_to')),
    }


def _fanvue_ppv_sets(persona):
    """Every valid PPV content set for a persona, in creator order. An older
    flat `tiers` list is read as one set, so nothing already saved is lost."""
    cfg = _fanvue_ppv(persona)
    raw = cfg.get('sets')
    if isinstance(raw, list):
        out = []
        for i, s in enumerate(raw):
            c = _fv_clean_set(s, i)
            if c:
                out.append(c)
        return out
    legacy = _fanvue_ppv_tiers(persona)
    if not legacy:
        return []
    return [{'id': 'set1', 'name': 'Set 1', 'tiers': legacy, 'scene': '',
             'keywords': [], 'hour_from': None, 'hour_to': None}]


def _fanvue_ppv_tiers(persona):
    """The legacy flat tier list, still read so old configs keep working."""
    cfg = _fanvue_ppv(persona)
    raw = cfg.get('tiers')
    if not isinstance(raw, list):
        raw = [{'media_uuids': cfg.get('media_uuids') or [],
                'price': cfg.get('price') or 0,
                'caption': cfg.get('caption') or ''}] if cfg.get('media_uuids') else []
    return [t for t in (_fv_clean_tier(x) for x in raw) if t]


def _fv_hour_fits(s, hour):
    """True when the set suits this hour. A window that wraps midnight (22→6)
    is read as crossing the night rather than as an empty range."""
    a, b = s.get('hour_from'), s.get('hour_to')
    if a is None or b is None:
        return True
    return a <= hour <= b if a <= b else (hour >= a or hour <= b)


def _fv_hour_score(s, hour):
    """How well the hour suits this set. Tapered at the edges rather than a
    cliff: a "just got in from a night out" set shouldn't die at 05:59."""
    a, b = s.get('hour_from'), s.get('hour_to')
    if a is None or b is None:
        return 0.0
    if _fv_hour_fits(s, hour):
        edge = hour in (a, b)
        return 1.0 if edge else 2.0
    # An hour either side of the window still beats one on the far side.
    for near in ((a - 1) % 24, (b + 1) % 24):
        if hour == near:
            return -1.0
    return -5.0


FV_SYNONYMS = {
    'lingerie': ('lingerie', 'underwear', 'bra', 'panties', 'thong', 'stockings'),
    'bed': ('bed', 'bedroom', 'sheets', 'pillow', 'duvet'),
    'shower': ('shower', 'bath', 'wet', 'soapy', 'towel'),
    'gym': ('gym', 'workout', 'leggings', 'sweaty', 'training'),
    'night': ('night', 'tonight', 'midnight', 'late', 'bedtime'),
}


def _fv_parse_keyword(raw):
    """One trigger word and how it should be matched.

    Plain words still mean what they always did; the extra forms let a creator
    say what they actually mean:
      stockings    whole word              +3
      green dress  phrase, substring       +3
      stocking*    stem                    +3
      bed^5        explicit weight         +5
      !broke       suppress this set      -20
      ~lingerie    synonym group           +2 per member
    """
    k = (raw or '').strip().lower()
    if not k:
        return None
    negative = k.startswith('!')
    if negative:
        k = k[1:].strip()
    syn = k.startswith('~')
    if syn:
        k = k[1:].strip()
    weight = 3.0
    m = re.match(r'^(.*?)\^(\d+(?:\.\d+)?)$', k)
    if m:
        k, weight = m.group(1).strip(), float(m.group(2))
    stem = k.endswith('*')
    if stem:
        k = k[:-1].strip()
    if not k:
        return None
    if negative:
        weight = -20.0
    elif syn:
        weight = 2.0
    return {'word': k, 'weight': weight, 'stem': stem, 'syn': syn,
            'phrase': ' ' in k, 'negative': negative}


def _fv_words(text):
    """Message words. \\w keeps digits and accents, which the old [a-z']+ threw
    away — so a trigger word like 24/7 or café could never once match."""
    return set(re.findall(r"[\w']+", (text or '').casefold(), re.UNICODE))


def _fv_keyword_score(kw, words, lowered):
    if kw['phrase']:
        return kw['weight'] if kw['word'] in lowered else 0.0
    if kw['syn']:
        group = FV_SYNONYMS.get(kw['word'], (kw['word'],))
        hits = sum(1 for g in group if g in words)
        return kw['weight'] * min(hits, 2)
    if kw['stem']:
        return kw['weight'] if any(w.startswith(kw['word']) for w in words) else 0.0
    return kw['weight'] if kw['word'] in words else 0.0


# How much better a rival set must score before it takes a fan off the set they
# are already climbing. Roughly "one real trigger word" — enough that a shared
# scene word or the clock alone can't restart the ladder at tier 1.
FV_SWITCH_MARGIN = 4.0


def _fv_score_set(s, context, hour, active=False):
    """Score one set against the moment, and say why — the 'why' is what the
    builder's simulator shows the creator."""
    lowered = (context or '').casefold()
    words = _fv_words(context)
    parts, score = [], 0.0
    for raw in s['keywords']:
        kw = _fv_parse_keyword(raw)
        if not kw:
            continue
        v = _fv_keyword_score(kw, words, lowered)
        if v:
            score += v
            parts.append((raw, v))
    scene_words = {w for w in _fv_words(s['scene']) if len(w) > 3}
    scene_hits = scene_words & words
    if scene_hits:
        score += 1.5 * len(scene_hits)
        parts.append((f"scene:{'/'.join(sorted(scene_hits)[:3])}", 1.5 * len(scene_hits)))
    h = _fv_hour_score(s, hour)
    if active:
        # A set the fan is already climbing has earned its place; the clock
        # alone shouldn't restart them on a different ladder.
        h = max(h, -1.0)
    if h:
        score += h
        parts.append((f'hour {s.get("hour_from")}-{s.get("hour_to")}', h))
    if active:
        score += FV_SWITCH_MARGIN
        parts.append(('already on this set', FV_SWITCH_MARGIN))
    return score, parts


def _fv_suppressed(sets, context):
    """A negative trigger word says something about the fan right now — broke,
    tired, wanting a refund — not about one set. So it holds back every drop,
    not just the set it was written on."""
    words = _fv_words(context)
    lowered = (context or '').casefold()
    for s in sets:
        for raw in s['keywords']:
            kw = _fv_parse_keyword(raw)
            if kw and kw['negative'] and _fv_keyword_score(
                    dict(kw, weight=1.0), words, lowered):
                return kw['word']
    return ''


def _fv_pick_set(sets, state, context, hour):
    """Choose the set to drop from. The set the fan is already climbing keeps
    winning unless another beats it by a clear margin, so a three-tier set is
    actually delivered as three tiers instead of restarting at tier 1 whenever
    the topic or the clock moves. None when every set is exhausted."""
    left = [s for s in sets if _fv_tiers_left(s, state) > 0]
    if not left:
        return None
    active_id = (state or {}).get('active_set') or ''
    best, best_score = None, float('-inf')
    for i, s in enumerate(left):
        score, _ = _fv_score_set(s, context, hour, active=(s['id'] == active_id))
        score -= i * 0.01          # keep creator order as the tie-break
        if score > best_score:
            best, best_score = s, score
    return best


def _fv_repeat_tier(sets, state):
    """The tier to offer again: the last one actually sent, per the fan's own
    record rather than creator order."""
    by_id = {s['id']: s for s in sets}
    s = by_id.get((state or {}).get('active_set') or '')
    if s is None:
        return None, 0
    n = int((state.get('sets') or {}).get(s['id'], 0) or 0)
    if not n:
        return None, 0
    return s, min(n - 1, len(s['tiers']) - 1)


def _fv_fan_hour(persona, fan_key, creator_offset=0):
    """The hour of day where the fan is, so "at night on bed" fires at their
    night rather than the creator's. Learned from when they actually message;
    the creator's own offset is the fallback until there's enough to go on."""
    off = _fv_fan_tz_offset(persona, fan_key)
    if off is None:
        off = int(creator_offset or 0)
    now = datetime.now(timezone.utc) + timedelta(minutes=off)
    return now.hour


def _fv_fan_tz_offset(persona, fan_key, min_msgs=12):
    """Minutes from UTC, guessed from the fan's own activity: people message
    across their waking day, so the middle of that window sits near their
    mid-afternoon. None until there is enough signal to beat the fallback."""
    try:
        from db import SessionLocal, XMessage
        s = SessionLocal()
        try:
            rows = (s.query(XMessage.created_at)
                    .filter(XMessage.persona == persona,
                            XMessage.x_user_id == str(fan_key),
                            XMessage.direction == 'in')
                    .order_by(XMessage.created_at.desc()).limit(300).all())
        finally:
            s.close()
    except Exception:
        return None
    hours = [r[0].hour for r in rows if r[0]]
    if len(hours) < min_msgs:
        return None
    # Circular mean of the hours they write, in UTC.
    import math
    xs = sum(math.cos(h * math.pi / 12) for h in hours)
    ys = sum(math.sin(h * math.pi / 12) for h in hours)
    if not xs and not ys:
        return None
    mean_utc = (math.atan2(ys, xs) * 12 / math.pi) % 24
    # Put the middle of their activity at ~16:00 local, a fair centre for an
    # evening-weighted chat app.
    return int(round(((16 - mean_utc) % 24) * 60)) - (1440 if ((16 - mean_utc) % 24) > 12 else 0)


# Fanvue rejects anything under $3, so a discounted retry stops here.
FV_PRICE_FLOOR = 300


def _fv_caption(persona, chosen, tier, reply):
    """The line on the paywall. A creator-written caption wins; {scene} and
    {name} are filled in so one caption can serve a whole set.

    When there is none, write one for the content rather than reusing the chat
    reply — a reply is written to continue a conversation, and shipping it as
    the sales line is how a $20 unlock ends up captioned "anyway, what are you
    up to tonight?"."""
    raw = (tier.get('caption') or '').strip()
    if raw:
        try:
            raw = raw.format(scene=chosen.get('scene') or '',
                             name=chosen.get('name') or '')
        except (KeyError, IndexError, ValueError):
            pass
        return raw[:2000]
    scene = (chosen.get('scene') or chosen.get('name') or '').strip()
    if scene:
        try:
            gen = _persona_text(
                persona,
                'Write ONE short caption, in character, for a locked photo set '
                f'you are about to send a fan. The set is: {scene}. Tease what '
                'is inside so they have to see it — hint at one specific thing '
                'without describing it. Do not mention price, buying or '
                'unlocking. One or two sentences, no quotes, no emoji unless '
                'your voice normally uses them.',
                max_tokens=80, temperature=1.0)
            gen = _strip_placeholders(strip_ppv_marker(gen or '')).strip().strip('"')
            if gen:
                return gen[:2000]
        except Exception as e:
            logger.warning('PPV caption generation failed for %s: %s', persona, str(e)[:120])
    return (reply or '').strip()[:2000]


def _fv_record_drop(persona, fan_uuid, chosen, idx, price, media_uuids, message_uuid):
    """Write the sale to the ledger. Returns the row id, or '' if it could not
    be written — the drop still went out, so that is logged loudly rather than
    swallowed: without a row nothing can later confirm the fan paid."""
    try:
        from db import SessionLocal, record_ppv_drop
        s = SessionLocal()
        try:
            d = record_ppv_drop(s, persona, fan_uuid, chosen['id'], chosen['name'],
                                idx, price, media_uuids, message_uuid)
            s.commit()
            return d.id
        finally:
            s.close()
    except Exception as e:
        logger.warning('PPV ledger write failed for %s/%s: %s', persona, fan_uuid, str(e)[:160])
        _fv_trace(persona, 'error',
                  f'PPV sent but not recorded ({str(e)[:120]}) — payment cannot be verified')
        return ''


# Fanvue serves media only through variant URLs, and only when the request asks
# for them by name. blurred is what a locked item may legitimately show.
FV_MEDIA_VARIANTS = 'main,thumbnail,thumbnail_gallery,blurred'


def _fv_media_thumb(m):
    """Best thumbnail URL for a media item from its variants, else its own url."""
    for v in (m.get('variants') or []):
        if isinstance(v, dict) and _fv_first(v, 'variantType', default='').lower() in ('thumbnail', 'preview', 'small'):
            u = v.get('url')
            if u:
                return u
    vs = m.get('variants') or []
    if vs and isinstance(vs[0], dict) and vs[0].get('url'):
        return vs[0]['url']
    return m.get('url') or ''


@app.route('/api/fanvue/folders')
@platform_scoped
def api_fanvue_folders():
    """The creator's vault folders, so the picker can filter by folder without
    the name having to be typed exactly."""
    persona = (request.args.get('persona') or '').strip()
    folders, page = [], 1
    try:
        while page <= 20:
            res = _fanvue_call(persona, 'GET', f'/vault/folders?page={page}&size=50')
            for f in _fv_list(res):
                if f.get('name'):
                    folders.append({'name': f['name'], 'count': f.get('mediaCount')})
            if not ((res or {}).get('pagination') or {}).get('hasMore'):
                break
            page += 1
    except Exception as e:
        return jsonify({'folders': folders, 'error': str(e)[:140]})
    return jsonify({'folders': folders})


@app.route('/api/fanvue/media')
@platform_scoped
def api_fanvue_media():
    """One page of the connected creator's Fanvue media, so the picker can load
    lazily instead of pulling the whole vault. Optional ?type=image|video|audio
    and ?folder= filters; ?page= walks the pages."""
    persona = (request.args.get('persona') or '').strip()
    mtype = (request.args.get('type') or '').strip()
    folder = (request.args.get('folder') or '').strip()
    try:
        page = max(1, int(request.args.get('page') or 1))
    except (TypeError, ValueError):
        page = 1
    # Without `variants` Fanvue returns metadata only — no URL at all, so
    # nothing renders. The URLs live on the variant objects, not the item.
    q = f'size=50&page={page}&variants={FV_MEDIA_VARIANTS}'
    if mtype:
        q += f'&mediaType={mtype}'
    if folder:
        q += f'&folderName={urllib.parse.quote(folder)}'
    items, err, has_more = [], '', False
    try:
        res = _fanvue_call(persona, 'GET', f'/media?{q}')
        has_more = bool(((res or {}).get('pagination') or {}).get('hasMore'))
        for m in _fv_list(res):
            # Fanvue reports FINALISED for a usable item; anything else comes
            # back as uuid + status only, with no variants to show.
            if str(_fv_first(m, 'status', default='') or '').lower() \
                    not in ('finalised', 'finalized', 'ready', ''):
                continue
            desc = _fv_first(m, 'description', default='') or ''
            items.append({
                'uuid': _fv_first(m, 'uuid', 'id', default=''),
                'name': _fv_first(m, 'name', 'caption', default='') or (desc[:40] or '(untitled)'),
                'mediaType': _fv_first(m, 'mediaType', default=''),
                'price': _fv_first(m, 'recommendedPrice', default=None),
                'description': desc,
                'thumb': _fv_media_thumb(m),
                'url': m.get('url') or '',
            })
    except Exception as e:
        err = str(e)[:140]
    return jsonify({'media': [m for m in items if m['uuid']], 'page': page,
                    'has_more': has_more,
                    'selected': _fanvue_ppv(persona), 'error': err})


@app.route('/api/fanvue/media-item')
@platform_scoped
def api_fanvue_media_item():
    """Fetch a single media item for previewing. The owner /media/{uuid} returns
    metadata only (no signed URL); the consumer endpoint returns signed variant
    URLs, so try that too. Falls back to the AI description + tags."""
    persona = (request.args.get('persona') or '').strip()
    uuid = (request.args.get('uuid') or '').strip()
    if not uuid:
        return jsonify({'error': 'Missing uuid'}), 400
    out = {'uuid': uuid, 'mediaType': '', 'url': '', 'description': '', 'tags': []}
    try:
        m = _fanvue_call(persona, 'GET', f'/media/{uuid}?variants={FV_MEDIA_VARIANTS}')
        m = m.get('data', m) if isinstance(m, dict) else {}
        out['mediaType'] = _fv_first(m, 'mediaType', default='')
        out['description'] = _fv_first(m, 'description', 'caption', default='') or ''
        out['url'] = m.get('url') or _fv_media_thumb(m) or ''
        t = m.get('tags')
        if isinstance(t, dict):
            out['tags'] = t.get('tags') or []
    except Exception as e:
        out['error'] = str(e)[:140]
    # No inline URL? Try the consumer endpoint (signed variant URLs).
    if not out['url']:
        try:
            me = _fanvue_me_uuid(persona)
            c = _fanvue_call(persona, 'GET', f'/media/{uuid}/consumer/{me}')
            c = c.get('data', c) if isinstance(c, dict) else {}
            out['url'] = c.get('url') or _fv_media_thumb(c) or ''
            if not out['mediaType']:
                out['mediaType'] = _fv_first(c, 'mediaType', default='')
        except Exception:
            pass
    return jsonify(out)


@app.route('/api/fanvue/media-lookup', methods=['POST'])
@platform_scoped
def api_fanvue_media_lookup():
    """Thumbnails for specific media uuids, so a tier can show its own items
    even when the folder filter is pointing somewhere else."""
    d = request.json or {}
    persona = (d.get('persona') or '').strip()
    uuids = [str(u).strip() for u in (d.get('uuids') or []) if str(u).strip()][:60]
    out = []
    for u in uuids:
        item = {'uuid': u, 'name': '', 'mediaType': '', 'thumb': ''}
        try:
            m = _fanvue_call(persona, 'GET', f'/media/{u}?variants={FV_MEDIA_VARIANTS}')
            m = m.get('data', m) if isinstance(m, dict) else {}
            desc = _fv_first(m, 'description', default='') or ''
            item['name'] = _fv_first(m, 'name', 'caption', default='') or (desc[:40] or u[:8])
            item['mediaType'] = _fv_first(m, 'mediaType', default='')
            item['thumb'] = _fv_media_thumb(m) or m.get('url') or ''
        except Exception:
            pass
        if not item['thumb']:
            # The owner record carries no signed URL for some items; the consumer
            # view of our own media does.
            try:
                me = _fanvue_me_uuid(persona)
                c = _fanvue_call(persona, 'GET', f'/media/{u}/consumer/{me}')
                c = c.get('data', c) if isinstance(c, dict) else {}
                item['thumb'] = _fv_media_thumb(c) or c.get('url') or ''
                item['mediaType'] = item['mediaType'] or _fv_first(c, 'mediaType', default='')
            except Exception:
                pass
        out.append(item)
    return jsonify({'media': out})


@app.route('/api/fanvue/ppv', methods=['GET', 'POST'])
@platform_scoped
def api_fanvue_ppv():
    """Get or set a persona's PPV content sets. A set is a themed bundle with
    its own ordered tiers plus the cues that pick it: scene, keywords and an
    hour window. Stored per connected Fanvue account (persona)."""
    if request.method == 'GET':
        persona = (request.args.get('persona') or '').strip()
        cfg = _fanvue_ppv(persona)
        return jsonify({'sets': _fanvue_ppv_sets(persona),
                        'tz_offset': cfg.get('tz_offset') or 0,
                        'enabled': bool(cfg.get('enabled', True))})
    d = request.json or {}
    persona = (d.get('persona') or '').strip()
    if not persona:
        return jsonify({'ok': False, 'error': 'Missing persona'}), 400
    raw = d.get('sets')
    if not isinstance(raw, list):
        # Older callers still post a flat tier list; treat it as one set.
        raw = [{'name': 'Set 1', 'tiers': d.get('tiers') or []}]
    sets, seen = [], set()
    for i, s in enumerate(raw):
        if not isinstance(s, dict):
            continue
        label = s.get('name') or ('Set ' + str(i + 1))
        # Refuse rather than drop. A tier with media but no price used to error,
        # while a tier with a price but no media vanished silently — so a set
        # that read "3 tiers" in the builder could be one tier at runtime.
        for j, t in enumerate(s.get('tiers') or []):
            if not isinstance(t, dict):
                continue
            media = t.get('media_uuids') or t.get('media') or []
            try:
                price = int(t.get('price') or 0)
            except (TypeError, ValueError):
                price = 0
            if not media:
                return jsonify({'ok': False, 'error':
                                f'"{label}" tier {j + 1} has no media. Add a file '
                                'or remove the tier.'}), 400
            if price < 300:
                return jsonify({'ok': False, 'error':
                                f'"{label}" tier {j + 1} needs a price of at '
                                'least $3'}), 400
        c = _fv_clean_set(s, i)
        if not c:
            return jsonify({'ok': False, 'error':
                            f'"{label}" has no usable tiers'}), 400
        while c['id'] in seen:
            c['id'] += '_'
        seen.add(c['id'])
        sets.append(c)
    try:
        tz = int(d.get('tz_offset') or 0)
    except (TypeError, ValueError):
        tz = 0
    cfg = {'sets': sets, 'tz_offset': max(-840, min(tz, 840)),
           'enabled': bool(d.get('enabled', True)) and bool(sets)}
    _set_setting(f'fanvue_ppv_{persona}', json.dumps(cfg))
    return jsonify({'ok': True, 'sets': sets, 'enabled': cfg['enabled'],
                    'warnings': _fv_set_warnings(sets)})


# --- Purchase signals -------------------------------------------------------
#
# Fanvue has no endpoint that answers "did this fan buy that item", so a sale is
# only ever known from a signal we caught and wrote down. Webhooks are the fast
# signal but never a memory: delivery is six attempts over ~2.5 minutes with no
# replay, and 20 consecutive failures disables the endpoint with no backfill.
# So everything a webhook tells us is also derivable from the API, and the
# reconciler below re-derives it.

FV_WEBHOOK_TOLERANCE = 300      # seconds either side of the signed timestamp
FV_SEEN_EVENTS_MAX = 1000


def _fv_webhook_secret():
    return (os.getenv('FANVUE_WEBHOOK_SECRET') or '').strip()


def _fv_storage_is_ephemeral():
    """True when the store is a temp SQLite file rather than a real database.

    db.py falls back to one silently, so on such a deployment every purchase is
    lost on the next cold start and nothing about the app looks wrong. Worth
    saying out loud wherever a creator might read it."""
    try:
        from db import DATABASE_URL
        return str(DATABASE_URL).startswith('sqlite')
    except Exception:
        return False


def _fv_verify_signature(raw_body, header, secret):
    """Fanvue signs 'X-Fanvue-Signature: t=<unix>,v0=<hex>' as an HMAC-SHA256
    over the exact bytes of "{t}.{body}". Returns (ok, reason)."""
    if not secret:
        return False, 'no signing secret configured'
    parts = {}
    for chunk in (header or '').split(','):
        k, _, v = chunk.strip().partition('=')
        if k:
            parts[k] = v
    ts, sig = parts.get('t'), parts.get('v0')
    if not ts or not sig:
        return False, 'malformed signature header'
    try:
        age = abs(time.time() - int(ts))
    except (TypeError, ValueError):
        return False, 'bad timestamp'
    if age > FV_WEBHOOK_TOLERANCE:
        return False, f'timestamp {int(age)}s outside the {FV_WEBHOOK_TOLERANCE}s window'
    expected = hmac.new(secret.encode('utf-8'),
                        ts.encode('utf-8') + b'.' + raw_body,
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return False, 'signature mismatch'
    return True, ''


def _fv_event_seen(event_id):
    """True if this event was already applied. Fanvue guarantees at-least-once
    delivery, so the same payment can arrive more than once."""
    if not event_id:
        return False
    key = 'fanvue_seen_events'
    try:
        seen = json.loads(_get_setting(key) or '[]')
        if not isinstance(seen, list):
            seen = []
    except Exception:
        seen = []
    if event_id in seen:
        return True
    seen.append(event_id)
    _set_setting(key, json.dumps(seen[-FV_SEEN_EVENTS_MAX:]))
    return False


def _fv_persona_for_creator(creator_uuid):
    """Which connected persona a webhook belongs to. Webhooks arrive on one
    endpoint for every creator the app is authorised on."""
    if not creator_uuid:
        return ''
    for slug in sorted({p.get('slug') for p in db_list_personas()} |
                       set(_fanvue_enabled_list())):
        if not slug:
            continue
        if _fanvue_creator(slug).get('uuid') == creator_uuid:
            return slug
    return ''


def _fv_settle_drop(persona, fan_uuid, amount_cents=None, invoice_id=None,
                    message_uuid=None, source='webhook', when=None):
    """Mark the fan's matching unbought drop as paid.

    Matched by message uuid when we have one, else by the fan's most recent
    unbought drop at that price — Fanvue's payment events name the fan and the
    amount but not the message that generated the charge."""
    try:
        from db import SessionLocal, PpvDrop, mark_ppv_paid
        s = SessionLocal()
    except Exception as e:
        logger.warning('PPV settle failed to open the ledger: %s', str(e)[:120])
        return None
    try:
        q = s.query(PpvDrop).filter(PpvDrop.persona == persona,
                                    PpvDrop.paid_at.is_(None))
        if message_uuid:
            row = q.filter(PpvDrop.message_uuid == str(message_uuid)).first()
        else:
            q = q.filter(PpvDrop.fan_uuid == str(fan_uuid or ''))
            if amount_cents:
                q = q.filter(PpvDrop.price_cents == int(amount_cents))
            row = q.order_by(PpvDrop.created_at.desc()).first()
        if row is None:
            return None
        mark_ppv_paid(s, row.id, invoice_id=invoice_id, source=source, when=when)
        s.commit()
        logger.info('PPV PAID [%s] %s tier %d via %s ($%g)', persona, row.set_name,
                    (row.tier_index or 0) + 1, source, (row.price_cents or 0) / 100)
        _fv_trace(persona, 'ppv',
                  f'\U0001F4B0 bought: {row.set_name} tier {(row.tier_index or 0) + 1} '
                  f'(${(row.price_cents or 0) / 100:g}, via {source})')
        return row.id
    except Exception as e:
        logger.warning('PPV settle failed for %s/%s: %s', persona, fan_uuid, str(e)[:140])
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


def _fv_mark_read(persona, fan_uuid):
    """Note that the fan opened their last unlock without buying it. That is a
    price objection rather than inattention, so the retry goes out cheaper."""
    try:
        from db import SessionLocal, PpvDrop, mark_ppv_read
        s = SessionLocal()
    except Exception:
        return
    try:
        row = (s.query(PpvDrop)
               .filter(PpvDrop.persona == persona, PpvDrop.fan_uuid == str(fan_uuid),
                       PpvDrop.paid_at.is_(None))
               .order_by(PpvDrop.created_at.desc()).first())
        if row is not None:
            mark_ppv_read(s, row.id)
            s.commit()
    except Exception:
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass


@app.route('/webhooks/fanvue', methods=['POST'])
def fanvue_webhook():
    """Fanvue event receiver. Signature-verified and idempotent; anything it
    misses is recovered by the reconciler, because Fanvue never replays."""
    raw = request.get_data()          # before any JSON parsing — the signature
    secret = _fv_webhook_secret()     # covers the exact bytes
    ok, why = _fv_verify_signature(raw, request.headers.get('X-Fanvue-Signature'), secret)
    if not ok:
        logger.warning('Fanvue webhook rejected: %s', why)
        return jsonify({'error': 'invalid signature'}), 401
    try:
        ev = json.loads(raw.decode('utf-8'))
    except Exception:
        return jsonify({'error': 'invalid payload'}), 400

    kind = str(ev.get('type') or '')
    data = ev.get('data') if isinstance(ev.get('data'), dict) else {}
    # message.read is explicitly not idempotent on its event id, so it is the
    # one topic not deduped.
    if kind != 'creator.message.read' and _fv_event_seen(str(ev.get('id') or '')):
        return jsonify({'ok': True, 'duplicate': True})

    creator = (data.get('creator') or {}).get('uuid') if isinstance(data.get('creator'), dict) else None
    fan = (data.get('fan') or {}).get('uuid') if isinstance(data.get('fan'), dict) else None
    persona = _fv_persona_for_creator(creator)
    if not persona:
        logger.info('Fanvue webhook %s for an unknown creator %s', kind, creator)
        return jsonify({'ok': True, 'ignored': 'unknown creator'})

    if kind == 'creator.payment.succeeded':
        amount = _fv_first(data, 'amount', 'gross', 'total', default=None)
        _fv_settle_drop(persona, fan,
                        amount_cents=int(amount) if amount else None,
                        invoice_id=str(_fv_first(data, 'id', 'invoice_id', default='') or '') or None,
                        source='webhook')
    elif kind == 'creator.message.read':
        _fv_mark_read(persona, fan)
    return jsonify({'ok': True})


def _fv_reconcile_purchases(persona, days=45, limit_pages=20):
    """Re-derive purchases from Fanvue's own ledger.

    This is what makes a sale knowable after downtime: webhook deliveries are
    gone for good once their retries are spent, but the invoices are not. Walks
    /earnings newest-first and settles any unbought drop it can match, then
    falls back to reading purchasedAt on the messages of fans still unsettled."""
    sets = _fanvue_ppv_sets(persona)
    if not sets:
        return {'settled': 0, 'checked': 0}
    since = datetime.now(timezone.utc) - timedelta(days=int(days))
    try:
        from db import SessionLocal, open_ppv_drops
        s = SessionLocal()
        try:
            open_rows = [(d.id, d.fan_uuid, d.price_cents, d.message_uuid)
                         for d in open_ppv_drops(s, persona, since=since)]
        finally:
            s.close()
    except Exception as e:
        logger.warning('Reconcile [%s]: ledger unreadable: %s', persona, str(e)[:120])
        return {'settled': 0, 'checked': 0, 'error': str(e)[:120]}
    if not open_rows:
        return {'settled': 0, 'checked': 0}

    settled, cursor = 0, ''
    scope = _fanvue_scope(persona)
    for _ in range(limit_pages):
        try:
            path = f'{scope}/earnings?size=50' + (f'&cursor={urllib.parse.quote(cursor)}' if cursor else '')
            res = _fanvue_call(persona, 'GET', path)
        except Exception as e:
            logger.warning('Reconcile [%s]: /earnings failed: %s', persona, str(e)[:140])
            break
        rows = _fv_list(res)
        if not rows:
            break
        for r in rows:
            if not isinstance(r, dict):
                continue
            fan_u = r.get('fan') if isinstance(r.get('fan'), str) else \
                (r.get('fan') or {}).get('uuid') if isinstance(r.get('fan'), dict) else None
            fan_u = fan_u or _fv_first(r, 'userUuid', 'fanUuid', 'purchaserUuid', default=None)
            amount = _fv_first(r, 'amount', 'gross', 'net', 'total', default=None)
            try:
                amount = int(amount)
            except (TypeError, ValueError):
                amount = None
            if amount is not None and amount <= 0:
                continue          # refunds and chargebacks are negative rows
            if _fv_settle_drop(persona, fan_u, amount_cents=amount,
                               invoice_id=str(_fv_first(r, 'id', 'invoiceNumber',
                                                        default='') or '') or None,
                               source='earnings'):
                settled += 1
        cursor = str(_fv_first(res if isinstance(res, dict) else {},
                               'nextCursor', 'next_cursor', default='') or '')
        if not cursor:
            break

    # Anything /earnings couldn't place, ask the message itself about.
    still_open = {r[1] for r in open_rows}
    for fan_u in list(still_open)[:25]:
        for page in (1, 2):
            try:
                msgs = _fv_list(_fanvue_call(
                    persona, 'GET',
                    f'{scope}/chats/{fan_u}/messages?page={page}&size=50'))
            except Exception:
                break
            if not msgs:
                break
            for m in msgs:
                if not isinstance(m, dict) or not m.get('purchasedAt'):
                    continue
                mu = str(_fv_first(m, 'uuid', 'id', default='') or '')
                if mu and _fv_settle_drop(persona, fan_u, message_uuid=mu,
                                          source='poll'):
                    settled += 1
    logger.info('Reconcile [%s]: %d of %d open drops settled', persona, settled,
                len(open_rows))
    if settled:
        _fv_trace(persona, 'ppv', f'reconciled {settled} purchase(s) from Fanvue')
    return {'settled': settled, 'checked': len(open_rows)}


@app.route('/api/fanvue/ppv-stats')
@platform_scoped
def api_fanvue_ppv_stats():
    """How each tier is actually performing: how many went out, how many were
    bought. Read from the ledger, so it survives everything the settings blobs
    don't."""
    persona = (request.args.get('persona') or '').strip()
    if not persona:
        return jsonify({'ok': False, 'error': 'Missing persona'}), 400
    try:
        from db import SessionLocal, ppv_set_stats, list_ppv_drops
        s = SessionLocal()
        try:
            stats = ppv_set_stats(s, persona)
            recent = [{'fan': d.fan_uuid, 'set': d.set_name,
                       'tier': (d.tier_index or 0) + 1,
                       'price': d.price_cents, 'paid': bool(d.paid_at),
                       'read': bool(d.read_at),
                       'at': d.created_at.isoformat() if d.created_at else '',
                       'via': d.paid_source or ''}
                      for d in list_ppv_drops(s, persona, limit=40)]
        finally:
            s.close()
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 500
    return jsonify({'ok': True, 'stats': stats, 'recent': recent,
                    'storage_ephemeral': _fv_storage_is_ephemeral()})


@app.route('/api/fanvue/ppv-simulate', methods=['POST'])
@platform_scoped
def api_fanvue_ppv_simulate():
    """Score a sample message against the saved sets and say exactly what would
    fire, and why. The answer to "why did it pick that one" without having to
    send anything to a real fan."""
    d = request.json or {}
    persona = (d.get('persona') or '').strip()
    if not persona:
        return jsonify({'ok': False, 'error': 'Missing persona'}), 400
    sets = _fanvue_ppv_sets(persona)
    if not sets:
        return jsonify({'ok': True, 'rows': [], 'chosen': None,
                        'note': 'No PPV sets are saved yet.'})
    text = (d.get('text') or '').strip()
    try:
        hour = int(d.get('hour'))
    except (TypeError, ValueError):
        hour = _fv_fan_hour(persona, '', _fanvue_ppv(persona).get('tz_offset') or 0)
    hour = max(0, min(hour, 23))
    active = (d.get('active_set') or '').strip()
    progress = d.get('progress') if isinstance(d.get('progress'), dict) else {}
    state = {'active_set': active,
             'sets': {str(k): int(v or 0) for k, v in progress.items()}}

    rows = []
    for i, s in enumerate(sets):
        left = _fv_tiers_left(s, state)
        score, parts = _fv_score_set(s, text, hour, active=(s['id'] == active))
        rows.append({'id': s['id'], 'name': s['name'],
                     'score': round(score - i * 0.01, 2),
                     'why': [{'reason': r, 'points': round(p, 2)} for r, p in parts],
                     'tiers_left': left,
                     'eligible': left > 0})
    rows.sort(key=lambda r: (-r['eligible'], -r['score']))

    chosen = _fv_pick_set(sets, state, text, hour)
    out = None
    if chosen:
        idx = int(state['sets'].get(chosen['id'], 0) or 0)
        tier = chosen['tiers'][idx]
        out = {'set': chosen['name'], 'set_id': chosen['id'],
               'tier': idx + 1, 'of': len(chosen['tiers']),
               'price': tier['price'],
               'media': len(tier['media_uuids']),
               'caption': tier.get('caption') or '(one will be written for it)'}
    return jsonify({'ok': True, 'hour': hour, 'rows': rows, 'chosen': out,
                    'warnings': _fv_set_warnings(sets)})


@app.route('/api/fanvue/reconcile', methods=['POST'])
@platform_scoped
def api_fanvue_reconcile():
    """Rebuild purchase state from Fanvue's ledger — run after downtime, after
    a redeploy, or any time the funnel looks stuck."""
    d = request.json or {}
    persona = (d.get('persona') or '').strip()
    if not persona:
        return jsonify({'ok': False, 'error': 'Missing persona'}), 400
    days = max(1, min(int(d.get('days') or 45), 365))
    out = _fv_reconcile_purchases(persona, days=days)
    return jsonify({'ok': True, **out})


@app.route('/api/fanvue/draft', methods=['POST'])
@platform_scoped
def api_fanvue_draft():
    """Draft an in-persona, funnel-aware reply to a fan message. Works with no
    Fanvue connection — for copy/paste or previewing before wiring live send."""
    data = request.json or {}
    persona = data.get('persona', '')
    message = (data.get('message') or '').strip()
    history = data.get('history') or []
    if not persona or not message:
        return jsonify({'ok': False, 'error': 'persona and message are required'}), 400
    try:
        pcfg = load_persona_config(persona)
        lim = reply_length_limits(pcfg)
        instruction = ("Reply to this Fanvue fan message in-character, warm and "
                       "engaging, move the conversation along the rapport → tease → "
                       "offer funnel naturally (never hard-sell). "
                       + question_rule_for(pcfg, history)
                       + lim['note'] + ' ' + NO_PLACEHOLDER_RULE
                       + f" Their message: \"{message}\"")
        reply = _fv_trim(_persona_text(persona, instruction, history=history,
                                       max_tokens=lim['tokens'], temperature=0.9),
                         max_sentences=lim['sentences'], hard_cap=lim['cap'])
        # Same strip as the live path, or the preview would show a placeholder
        # where auto-reply refuses to send one.
        reply = _strip_placeholders(trim_extra_questions(reply, question_allowed(pcfg, history)))
        if not reply:
            return jsonify({'ok': False, 'error': 'Could not generate a reply.'}), 400
        return jsonify({'ok': True, 'reply': reply})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 400


# ── Fanvue live auto-reply (persistent, survives redeploys) ───────────────────
# Response shapes vary; parsing is defensive (tries several field names) so it's
# easy to adjust once we see a real payload.

def _fv_first(d, *keys, default=None):
    for k in keys:
        v = d.get(k) if isinstance(d, dict) else None
        if v not in (None, ''):
            return v
    return default


def _fv_list(res):
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        for k in ('data', 'items', 'chats', 'results', 'messages'):
            if isinstance(res.get(k), list):
                return res[k]
    return []


def _fv_sender(msg):
    """Extract the sender UUID from a Fanvue message, handling both flat fields
    and nested sender/author/user/from objects."""
    if not isinstance(msg, dict):
        return ''
    flat = _fv_first(msg, 'senderUuid', 'authorUuid', 'fromUuid', 'userUuid',
                     'senderId', 'authorId', default='')
    if flat:
        return flat
    for k in ('sender', 'author', 'user', 'from', 'creator', 'owner'):
        v = msg.get(k)
        if isinstance(v, dict):
            uid = _fv_first(v, 'uuid', 'id', default='')
            if uid:
                return uid
        elif isinstance(v, str) and v:
            return v
    return ''


def _fv_direction_of(msg, fan_uuid, me_uuid, recent_out=()):
    """Who sent this message: 'out' (us), 'in' (the fan), or '' when nothing in
    it settles the question.

    Guessing here is what made the persona greet a fan by her own name: one
    unrecognised message shape used to make her whole self-introduction read
    back as something *he* had said. Callers decide what an unknown means —
    the live round can fall back on its own context, the bulk import cannot.
    """
    if not isinstance(msg, dict):
        return ''
    sender = _fv_sender(msg)
    if sender:
        if fan_uuid and sender == fan_uuid:
            return 'in'
        if me_uuid and sender == me_uuid:
            return 'out'
        return ''
    if (msg.get('fromMe') or msg.get('isOwn') or msg.get('isMine')
            or msg.get('isAuthor') or msg.get('direction') == 'out'
            or msg.get('type') == 'sent'):
        return 'out'
    text = _fv_first(msg, 'text', 'content', 'message', 'body', default='').strip()
    if text and text in recent_out:
        return 'out'
    return ''


# A sentence ends at . ! ? — but NOT at an ellipsis, which she uses mid-thought
# ("that's certainly a… specific preference"). Splitting there would cut the
# sentence in half. A following lower-case word means the thought runs on too.
# Requiring a non-lowercase character after the break meant a lowercase persona
# — the default — never split at all: her whole reply counted as one sentence,
# so question trimming and message bursts silently did nothing. A hard line
# break ends a thought too; the ellipsis glue below still keeps "a… specific
# preference" in one piece.
_SENTENCE_END_RE = re.compile(r'(?<=[.!?])\s+')


def _sentences(text):
    """Split into whole sentences, never inside one."""
    t = (text or '').strip()
    if not t:
        return []
    out = []
    for line in re.split(r'\n+', t):
        line = line.strip()
        if not line:
            continue
        start = len(out)
        for part in _SENTENCE_END_RE.split(line):
            part = part.strip()
            if not part:
                continue
            # A piece left hanging on an ellipsis is still mid-thought: glue the
            # next piece onto it rather than treating it as a finished sentence.
            # Only within a line — she habitually trails off on "..." before a
            # break, and gluing across it merged two questions into one.
            if len(out) > start and (out[-1].endswith('…') or out[-1].endswith('...')):
                out[-1] = out[-1] + ' ' + part
            else:
                out.append(part)
    return out


REPLY_LENGTH_LIMITS = {
    'short': {
        'sentences': 2, 'cap': 320, 'tokens': 300,
        'note': ("CRITICAL: Keep it VERY short — one or two sentences MAX, like a "
                 "real text message. No paragraphs, no lists, no walls of text."),
    },
    'medium': {
        'sentences': 4, 'cap': 560, 'tokens': 420,
        'note': ("Keep it to two to four sentences — conversational, the length of "
                 "a real text, never a wall of text."),
    },
    'long': {
        'sentences': 6, 'cap': 900, 'tokens': 600,
        'note': ("A longer reply is fine — up to a short paragraph when you have "
                 "something to say. Never lecture and never write a list."),
    },
}


def reply_length_limits(config):
    """How long a reply may run on the bot channels, from the creator's Reply
    Length setting. The same setting shapes the baked system prompt; repeating it
    per request keeps the channel instruction from contradicting it, and gives
    _fv_trim a cap that matches instead of always cutting at two sentences."""
    key = (config or {}).get('reply_length') or 'short'
    return REPLY_LENGTH_LIMITS.get(key, REPLY_LENGTH_LIMITS['short'])


def persona_length_limits(slug):
    return reply_length_limits(load_persona_config(slug))


NO_QUESTION_RULE = (
    "You have just asked the fan a question — do not ask another one in this "
    "reply. React to what they said and share something of your own instead. "
    "No question mark at the end. ")


def question_rule_for(config, history):
    """The per-request question instruction for a bot channel: ask, or hold off,
    according to the creator's Question Frequency setting."""
    return ('End with ONE question that follows from what they just said — never '
            'a generic "how are you", never a question you have already asked, and '
            'never more than one. ' if question_allowed(config, history)
            else NO_QUESTION_RULE)


def _fv_trim(text, max_sentences=2, hard_cap=320):
    """Trim a reply to at most a couple of whole sentences. Never cuts a
    sentence short — a single over-long one is left intact rather than ending
    on a stub."""
    t = (text or '').strip()
    if not t:
        return t
    parts = _sentences(t)
    if len(parts) > max_sentences:
        parts = parts[:max_sentences]
        t = ' '.join(parts).strip()
    if len(t) > hard_cap and len(parts) > 1:
        kept = []
        for p in parts:
            if kept and len(' '.join(kept + [p])) > hard_cap:
                break
            kept.append(p)
        t = ' '.join(kept).strip() or parts[0]
    return t


# A bracketed placeholder reaching a fan reads as a broken mail-merge, so the
# net is deliberately wide: in a DM a bracketed run of words is never something
# a real person typed. Lives here rather than beside the Telegram sender that
# first needed it — being defined a thousand lines below the Fanvue code is why
# that path never picked it up.
_PLACEHOLDER_RE = re.compile(
    r"[ \t]*,?[ \t]*\["
    r"(?:[^\]]{0,40}(?:name|fan|user|city|town|location|age|job|work|hobby|"
    r"topic|interest|detail|insert|placeholder|here|something)[^\]]{0,40}"
    r"|[a-z][a-z' ]{0,38})"
    r"\]", re.I)

NO_PLACEHOLDER_RULE = (
    'Write the message exactly as it should be sent. Never emit a placeholder '
    "in brackets such as [fan's name] or [name] — if you do not know something, "
    'leave it out entirely rather than marking a gap. ')


def _strip_placeholders(text):
    """Remove a bracketed placeholder the model left in, e.g. "[fan's name]",
    along with the comma that introduced it. A prompt rule reduces these but
    does not eliminate them."""
    if not text or '[' not in text:
        return text
    cleaned = _PLACEHOLDER_RE.sub('', text)
    cleaned = re.sub(r'\s+([?!.,])', r'\1', cleaned)
    return re.sub(r'[ \t]{2,}', ' ', cleaned).strip()


def _has_placeholder(text):
    return bool(text) and '[' in text and bool(_PLACEHOLDER_RE.search(text))


def _fanvue_me_uuid(persona):
    creator_uuid = _fanvue_creator(persona).get('uuid')
    if creator_uuid:
        return creator_uuid
    t = _fanvue_tokens(persona)
    if t.get('uuid'):
        return t['uuid']
    me = _fanvue_call(persona, 'GET', '/users/me')
    uid = _fv_first(me, 'uuid', 'id') or _fv_first(me.get('data', {}) if isinstance(me, dict) else {}, 'uuid', 'id')
    if uid:
        t['uuid'] = uid
        _fanvue_save_tokens(persona, t)
    return uid


def _fv_user_of_chat(chat):
    """Return (fan_uuid, handle, is_creator, chat_uuid) for the fan on the other side of a chat."""
    u = chat.get('user') or chat.get('otherUser') or chat.get('participant') or chat
    uuid = _fv_first(chat, 'userUuid', 'otherUserUuid') or _fv_first(u, 'uuid', 'id')
    handle = _fv_first(u, 'handle', 'username', 'displayName', default='')
    role = str(_fv_first(u, 'role', 'type', default='')).lower()
    is_creator = bool(u.get('isCreator') or u.get('creator') or role == 'creator')
    chat_uuid = _fv_first(chat, 'uuid', 'id', 'chatUuid', default='')
    return uuid, handle, is_creator, chat_uuid


def _fv_chat_online(chat, grace_minutes=5):
    """Whether the fan on this chat is around right now.

    Fanvue puts `online` (bool) and `lastSeenAt` (date|null) on the chat itself,
    not on the nested user. Presence flickers when someone backgrounds the app,
    so anyone seen within the grace window still counts as online. lastSeenAt is
    null when the fan hides it — then only the explicit flag is trusted.
    """
    flag = chat.get('online')
    if flag is True:
        return True
    seen = _fv_first(chat, 'lastSeenAt', 'lastSeen', default='')
    if seen:
        try:
            s = str(seen).replace('Z', '+00:00')
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            mins = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
            if mins <= grace_minutes:
                return True
        except Exception:
            pass
    return False


def _fv_msg_age_minutes(msg):
    """Minutes since a Fanvue message was created, or None if unparseable."""
    raw = _fv_first(msg, 'createdAt', 'sentAt', 'timestamp', default='')
    if not raw:
        return None
    try:
        s = str(raw).replace('Z', '+00:00')
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    except Exception:
        return None


FV_READ_CAP = 12.0
FV_TYPE_CAP = 12.0
# How long she takes to answer. A flat random pause meant a fan mid-conversation
# could wait two minutes for "haha yeah" — so the wait follows the conversation
# instead: quick when he is clearly sat there with his phone, unhurried when the
# chat has gone cold. FV_REPLY_CAP bounds the whole thing for an active fan.
FV_PAUSE_ACTIVE = (12, 40)
FV_PAUSE_COLD = (45, 120)
FV_ACTIVE_MIN = 15
FV_REPLY_CAP = 90.0
_fv_ppv_locks = {}
_fv_reply_pool = [None]
_fv_inflight = [0]
_fv_inflight_lock = threading.Lock()


def _fv_humanize_cfg(persona):
    """Reply pacing for Fanvue, same shape and defaults as Telegram's."""
    opts = _fanvue_auto_settings(persona)
    return {'humanize': bool(opts.get('humanize', True)),
            'typing_speed': max(4, min(int(opts.get('typing_speed') or 14), 40)),
            'react_rate': max(0, min(int(opts.get('react_rate', 25)), 100))}


# Fanvue's API has no reactions, so one is written into the message itself: a
# lone emoji reads as a like.
FV_REACTIONS = (
    (r"\b(ha+|haha|lol|lmao|funny|joke)\b", ["\U0001F602", "\U0001F923", "\U0001F605"]),
    (r"\b(love|miss|beautiful|gorgeous|cute|sweet)\b", ["\U0001F970", "\U0001F60D", "\u2764\ufe0f"]),
    (r"\b(sexy|hot|damn|wow|omg)\b", ["\U0001F60F", "\U0001F525", "\U0001F633"]),
    (r"\b(sad|tired|rough|sorry|stress\w*)\b", ["\U0001F97A", "\U0001F622", "\U0001FAF6"]),
    (r"\b(work|shift|busy|early|late)\b", ["\U0001F634", "\U0001FAE0", "\U0001F629"]),
    (r"\?\s*$", ["\U0001F914", "\U0001F440"]),
)
FV_REACTION_FALLBACK = ["\U0001F60A", "\U0001F604", "\U0001F609", "\U0001F440", "\U0001F648"]


def _fv_reaction_for(text):
    """An emoji answering the tone of what he said, the way a like would."""
    t = (text or '').lower().strip()
    for pattern, emojis in FV_REACTIONS:
        if re.search(pattern, t):
            return random.choice(emojis)
    return random.choice(FV_REACTION_FALLBACK)


_fv_list_cache = {}
FV_LIST_CACHE_SEC = 600


def _fanvue_chat_lists(persona):
    """Fanvue's two kinds of chat list: smart ones (fixed string ids, computed by
    Fanvue) and the creator's own custom ones (UUIDs)."""
    out = []
    try:
        for l in _fv_list(_fanvue_call(persona, 'GET', '/chats/lists/smart')):
            out.append({'kind': 'smart', 'id': l.get('uuid') or '',
                        'name': l.get('name') or l.get('uuid') or '',
                        'count': l.get('count')})
    except Exception as e:
        logger.info('Fanvue smart lists failed for %s: %s', persona, str(e)[:120])
    page = 1
    while page <= 10:
        try:
            res = _fanvue_call(persona, 'GET', f'/chats/lists/custom?page={page}&size=50')
        except Exception as e:
            logger.info('Fanvue custom lists failed for %s: %s', persona, str(e)[:120])
            break
        for l in _fv_list(res):
            out.append({'kind': 'custom', 'id': l.get('uuid') or '',
                        'name': l.get('name') or '', 'count': l.get('membersCount')})
        if not ((res or {}).get('pagination') or {}).get('hasMore'):
            break
        page += 1
    return [l for l in out if l['id']]


def _fanvue_list_members(persona, kind, list_id):
    """Fan UUIDs in one list. Membership is a snapshot Fanvue recomputes, so it
    is cached briefly rather than read on every round."""
    key = (persona, kind, list_id)
    hit = _fv_list_cache.get(key)
    if hit and time.time() - hit[0] < FV_LIST_CACHE_SEC:
        return hit[1]
    members, page = set(), 1
    while page <= 20:
        try:
            res = _fanvue_call(persona, 'GET',
                               f'/chats/lists/{kind}/{list_id}?page={page}&size=50')
        except Exception as e:
            logger.info('Fanvue list %s/%s failed: %s', kind, list_id, str(e)[:120])
            break
        for u in _fv_list(res):
            uid = _fv_first(u, 'uuid', 'id', default='')
            if uid:
                members.add(uid)
        if not ((res or {}).get('pagination') or {}).get('hasMore'):
            break
        page += 1
    _fv_list_cache[key] = (time.time(), members)
    return members


def _fv_clean_lists(value):
    """Normalise a saved list selection: [{kind, id, name}]."""
    out = []
    for l in (value or []):
        if not isinstance(l, dict):
            continue
        kind = 'custom' if l.get('kind') == 'custom' else 'smart'
        lid = str(l.get('id') or '').strip()
        if lid:
            out.append({'kind': kind, 'id': lid, 'name': str(l.get('name') or lid)[:80]})
    return out


def _fv_list_filter(persona, opts):
    """Resolve the include/exclude list settings into (include_uuids, exclude_uuids).
    include is None when no list is selected, meaning everyone is allowed."""
    inc = None
    for l in _fv_clean_lists(opts.get('include_lists')):
        inc = (inc or set()) | _fanvue_list_members(persona, l['kind'], l['id'])
    exc = set()
    for l in _fv_clean_lists(opts.get('exclude_lists')):
        exc |= _fanvue_list_members(persona, l['kind'], l['id'])
    return inc, exc


FV_TRACE_MAX = 200


def _fv_trace(persona, stage, detail=''):
    """Append one line to the persona's Fanvue activity log. Stored in the
    database so it survives a redeploy and can be read from the browser."""
    key = f'fanvue_trace_{persona}'
    try:
        rows = json.loads(_get_setting(key) or '[]')
        if not isinstance(rows, list):
            rows = []
    except Exception:
        rows = []
    rows.append({'at': int(time.time()), 'stage': stage, 'detail': str(detail)[:500]})
    _set_setting(key, json.dumps(rows[-FV_TRACE_MAX:]))
    logger.info('FV[%s] %s: %s', persona, stage, str(detail)[:200])


def _fv_send_text(persona, scope, fan_uuid, text):
    # Everything is stripped upstream; if one still got through, a visible gap
    # beats a broken mail-merge landing in a fan's inbox.
    if _has_placeholder(text):
        _fv_trace(persona, 'error',
                  f'refused to send a message with a placeholder in it: {text[:120]}')
        raise RuntimeError('reply still contained a bracketed placeholder')
    _fanvue_call(persona, 'POST', f'{scope}/chats/{fan_uuid}/message',
                 body={'text': text[:2000]})


def _fv_send_human(persona, scope, fan_uuid, text, incoming='', cfg=None, active=True):
    """Send a Fanvue reply the way a person would: a pause to read, then a delay
    scaled to how long the reply takes to type, split across a burst or two.
    Same pacing as Telegram — Fanvue has no typing indicator, so the delay is
    the only signal that someone is on the other end.

    `active` says the fan is sat in the conversation right now, which buys a
    much shorter wait and a hard ceiling on the whole exchange."""
    cfg = cfg or _fv_humanize_cfg(persona)
    if not cfg['humanize']:
        _fv_send_text(persona, scope, fan_uuid, text)
        return
    cps = max(2, int(cfg['typing_speed']) // 2)
    budget = [FV_REPLY_CAP if active else None]

    def pause(seconds):
        if budget[0] is not None:
            seconds = min(seconds, max(0.0, budget[0]))
            budget[0] -= seconds
        time.sleep(seconds)

    pause(random.uniform(*(FV_PAUSE_ACTIVE if active else FV_PAUSE_COLD)))
    pause(min(0.8 + len(incoming) / 90.0, FV_READ_CAP) * random.uniform(0.7, 1.3))
    # React first, the way someone taps a heart before they start typing.
    if incoming and random.randint(1, 100) <= cfg.get('react_rate', 0):
        try:
            _fv_send_text(persona, scope, fan_uuid, _fv_reaction_for(incoming))
            pause(random.uniform(1.0, 3.0))
        except Exception as e:
            logger.info('Fanvue reaction failed: %s', str(e)[:120])
    for i, chunk in enumerate(_tg_bursts(text)):
        if not chunk:
            continue
        if i:
            pause(random.uniform(0.6, 1.6))
        pause(min(max(len(chunk) / float(cps), 1.2), FV_TYPE_CAP) * random.uniform(0.85, 1.2))
        _fv_send_text(persona, scope, fan_uuid, chunk)


def _ppv_count(v):
    # Legacy value was a bool (one PPV sent); treat True as 1 tier done.
    if isinstance(v, bool):
        return 1 if v else 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _ppv_progress(value, sets):
    """Per-fan progress as {set_id: tiers sent}. A legacy integer counted tiers
    in one flat list, which is exactly the first set here."""
    if isinstance(value, dict):
        return {str(k): int(v or 0) for k, v in value.items()}
    n = _ppv_count(value)
    return {sets[0]['id']: n} if n and sets else {}


def _fv_fan_state(raw, fan_uuid, sets):
    """One fan's PPV record, migrated forward from every earlier shape.

    The ledger table is the authority on what was bought; this is the hot-path
    summary of where the fan is in the ladder, and can be rebuilt from it."""
    st = raw.get(fan_uuid) if isinstance(raw, dict) else None
    if isinstance(st, dict) and 'sets' in st:
        st.setdefault('active_set', '')
        st.setdefault('retries', 0)
        st.setdefault('msgs_at_last_drop', 0)
        st.setdefault('last_drop_id', '')
        st['sets'] = {str(k): int(v or 0) for k, v in (st.get('sets') or {}).items()}
        return st
    # Legacy: {set_id: tiers_sent}, or an int/bool counting one flat list.
    progress = _ppv_progress(st, sets)
    active = ''
    if progress:
        # Whichever set got furthest is the one this fan was climbing.
        active = max(progress.items(), key=lambda kv: kv[1])[0]
    return {'active_set': active, 'sets': progress, 'last_drop_id': '',
            'retries': 0, 'msgs_at_last_drop': 0}


def _fv_tiers_left(s, state):
    return len(s['tiers']) - int((state.get('sets') or {}).get(s['id'], 0) or 0)


def _fv_queued_hint(persona, fan_uuid, fan_key, sets, state_key, context, tz_offset):
    """A line telling the model what unlock is about to follow its reply.

    The set is scored here as well as in _fv_maybe_ppv. That is deliberate: the
    reply has to be written before the drop is sent, and a reply that lands on
    the content converts far better than one written blind. The two can disagree
    if the pick shifts in between, which costs a slightly off tease, not a wrong
    send — _fv_maybe_ppv remains the only thing that decides what goes out."""
    if not sets:
        return ''
    try:
        state_all = json.loads(_get_setting(state_key) or '{}')
    except Exception:
        return ''
    state = _fv_fan_state(state_all, fan_uuid, sets)
    hour = _fv_fan_hour(persona, fan_key, tz_offset)
    chosen = _fv_pick_set(sets, state, context or '', hour)
    if not chosen:
        return ''
    scene = (chosen.get('scene') or chosen.get('name') or '').strip()
    if not scene:
        return ''
    return ('A paid unlock may follow this message: ' + scene + '. '
            'Write your reply so it leads there — build to it and make them '
            'curious. Do NOT name a price, do NOT mention buying, unlocking or '
            'sending anything, and do not describe the content directly. ')


def _fv_set_warnings(sets):
    """Configuration that will quietly misbehave: things worth telling the
    creator about at save time, none of which are worth refusing the save."""
    out = []
    # A trigger word on two sets makes them compete for the same moments, which
    # is what pulls a fan off one ladder onto another's tier 1.
    owners = {}
    for s in sets:
        for raw in s['keywords']:
            kw = _fv_parse_keyword(raw)
            if kw and not kw['negative']:
                owners.setdefault(kw['word'], []).append(s['name'])
    for word, names in owners.items():
        if len(names) > 1:
            out.append(f'"{word}" is a trigger word on {" and ".join(names)} — '
                       'those sets will compete for the same messages')
    # Hours nothing covers: a fan chatting then gets whichever set is least bad.
    windowed = [s for s in sets if s.get('hour_from') is not None
                and s.get('hour_to') is not None]
    if windowed and len(windowed) == len(sets):
        gaps = [h for h in range(24) if not any(_fv_hour_fits(s, h) for s in sets)]
        if gaps:
            spans, start = [], gaps[0]
            for prev, h in zip(gaps, gaps[1:] + [None]):
                if h != prev + 1:
                    spans.append(f'{start:02d}:00–{(prev + 1) % 24:02d}:00')
                    start = h
            out.append(f'No set covers {len(gaps)}h of the day '
                       f'({", ".join(spans)}) — leave one set without hours '
                       'as a fallback')
    for s in sets:
        prices = [t['price'] for t in s['tiers']]
        if len(prices) > 1 and any(b <= a for a, b in zip(prices, prices[1:])):
            out.append(f'"{s["name"]}" prices do not climb '
                       f'({" → ".join("$%g" % (p / 100) for p in prices)})')
        blank = [i + 1 for i, t in enumerate(s['tiers']) if not t.get('caption')]
        if blank:
            many = len(blank) > 1
            out.append(f'"{s["name"]}" tier{"s" if many else ""} '
                       f'{", ".join(map(str, blank))} '
                       f'{"have" if many else "has"} no caption — one will be '
                       f'written {"for them" if many else "for it"}')
    return out


def _fv_migrate_ppv_state(persona, sets):
    """Fold the old parallel dicts into the single per-fan record, once.

    Runs before the new gating goes live so nobody who was already halfway up a
    ladder is knocked back to tier 1 by the upgrade itself. The old keys are
    left in place — this only ever writes the new one."""
    state_key = f'fanvue_ppv_state_{persona}'
    if _get_setting(state_key):
        return
    try:
        old_sent = json.loads(_get_setting(f'fanvue_ppv_sent_{persona}') or '{}')
        old_at = json.loads(_get_setting(f'fanvue_ppv_at_{persona}') or '{}')
        old_retry = json.loads(_get_setting(f'fanvue_ppv_retry_{persona}') or '{}')
    except Exception:
        return
    if not isinstance(old_sent, dict) or not old_sent:
        return
    out = {}
    for fan, progress in old_sent.items():
        st = _fv_fan_state({fan: progress}, fan, sets)
        st['msgs_at_last_drop'] = int((old_at or {}).get(fan, 0) or 0)
        st['retries'] = int((old_retry or {}).get(fan, 0) or 0)
        out[fan] = st
    _set_setting(state_key, json.dumps(out))
    logger.info('Fanvue [%s]: migrated %d fans to the new PPV state', persona, len(out))


def _fv_maybe_ppv(persona, scope, fan_uuid, fan_key, handle, reply, ctx, context=''):
    """Consider the next PPV drop for this fan: hold the set they are already
    climbing unless another clearly fits better, then send its next tier.

    Serialised per persona because paced replies land from several worker
    threads at once and the summary is a read-modify-write on one setting. Every
    send is written to the ppv_drops ledger before the counters move, so the sale
    survives anything that happens to the summary afterwards."""
    sets = ctx['sets']
    if not sets:
        return
    with _fanvue_persona_lock(_fv_ppv_locks, persona):
        try:
            state_all = _json_setting_strict(ctx['state_key'], {})
            test_paid = _json_setting_strict(ctx['paid_key'], {})
        except SettingUnreadable as e:
            # An unreadable store looks exactly like "this fan is brand new",
            # which would re-send every unlock they already bought. Hold.
            logger.warning('Fanvue [%s] PPV state unreadable, holding: %s', persona, e)
            _fv_trace(persona, 'error', f'PPV state unreadable, no drop sent: {str(e)[:120]}')
            return

        state = _fv_fan_state(state_all, fan_uuid, sets)
        total_done = sum(state['sets'].values())
        exchanged = _fanvue_msg_count(persona, fan_key)
        resend = False
        discount = 0.0

        if total_done == 0:
            send_ppv = exchanged >= ctx.get('first_after', 6)
        else:
            enough_chat = (exchanged - int(state.get('msgs_at_last_drop', 0) or 0)) >= ctx['gap']
            drop = None
            if not ctx['require_payment']:
                paid = True
            elif int(test_paid.get(fan_uuid, 0) or 0) >= total_done:
                paid = True
            elif not state.get('last_drop_id'):
                # A fan carried over from before the ledger existed. Their past
                # drops can't be verified either way, so don't punish them for
                # the upgrade — let them carry on and gate from here on.
                paid = True
            else:
                pstate, drop = _fv_drop_state(persona, state.get('last_drop_id'))
                if pstate == PPV_UNKNOWN:
                    _fv_trace(persona, 'error',
                              f'could not read payment state for {handle or fan_uuid} — holding')
                    return
                paid = pstate == PPV_PAID
                if not paid and _fv_drop_is_stale(drop, ctx.get('stale_days')):
                    # Long past unbought: stop letting one photo end the funnel.
                    logger.info('Fanvue [%s] %s: last PPV unbought past the stale '
                                'window — moving to another set', persona, handle or fan_uuid)
                    _fv_trace(persona, 'ppv',
                              f'{handle or fan_uuid}: unbought drop expired, trying another set')
                    state['active_set'] = ''
                    state['retries'] = 0
                    paid = True
            send_ppv = paid and enough_chat
            if not paid:
                logger.info('Fanvue [%s] %s: waiting on payment of the last PPV',
                            persona, handle or fan_uuid)
                # An unbought drop otherwise blocks this fan for good. After
                # enough more chat, offer the same one again a few times — and
                # cheaper if they opened it, since that is a price objection.
                tries = int(state.get('retries', 0) or 0)
                waited = exchanged - int(state.get('msgs_at_last_drop', 0) or 0)
                if (ctx.get('retry_after') and tries < ctx.get('retry_max', 0)
                        and waited >= ctx['retry_after']):
                    resend = True
                    if drop is not None and drop.read_at:
                        discount = float(ctx.get('retry_discount') or 0)
        if not (send_ppv or resend):
            return

        hour = _fv_fan_hour(persona, fan_key, ctx.get('tz_offset'))
        blocked = _fv_suppressed(sets, (context or '') + ' ' + (reply or ''))
        if blocked:
            logger.info('Fanvue [%s] %s: holding the drop, "%s" came up',
                        persona, handle or fan_uuid, blocked)
            _fv_trace(persona, 'ppv',
                      f'held back from {handle or fan_uuid} — "{blocked}" came up')
            return
        if resend:
            # Repeat the tier they did not buy, not the next one.
            chosen, idx = _fv_repeat_tier(sets, state)
            if not chosen:
                return
        else:
            chosen = _fv_pick_set(sets, state, (context or '') + ' ' + (reply or ''), hour)
            if not chosen:
                return
            idx = int(state['sets'].get(chosen['id'], 0) or 0)
        tier = chosen['tiers'][idx]
        price = int(tier['price'])
        if discount:
            price = max(FV_PRICE_FLOOR, int(round(price * (1 - discount))))
        cap = _fv_caption(persona, chosen, tier, reply)
        try:
            sent = _fanvue_call(persona, 'POST', f'{scope}/chats/{fan_uuid}/message',
                                body={'text': cap, 'mediaUuids': tier['media_uuids'],
                                      'price': price})
        except Exception as e:
            logger.warning('Fanvue PPV to %s failed: %s', handle or fan_uuid, str(e)[:120])
            _fv_trace(persona, 'error', f'PPV to {handle or fan_uuid} failed: {str(e)[:200]}')
            return

        msg_uuid = ''
        if isinstance(sent, dict):
            body = sent.get('data') if isinstance(sent.get('data'), dict) else sent
            msg_uuid = str(_fv_first(body, 'uuid', 'id', default='') or '')
        drop_id = _fv_record_drop(persona, fan_uuid, chosen, idx, price,
                                  tier['media_uuids'], msg_uuid)

        if resend:
            state['retries'] = int(state.get('retries', 0) or 0) + 1
        else:
            state['sets'][chosen['id']] = idx + 1
            state['active_set'] = chosen['id']
            state['retries'] = 0
        if drop_id:
            state['last_drop_id'] = drop_id
        state['msgs_at_last_drop'] = exchanged
        state_all[fan_uuid] = state
        _set_setting(ctx['state_key'], json.dumps(state_all))

        again = ' again' if resend else ''
        off = f' (-{int(discount * 100)}%)' if discount else ''
        logger.info('PPV SENT%s [%s] %s tier %d/%d -> %s at %s%s', again, persona,
                    chosen['name'], idx + 1, len(chosen['tiers']),
                    handle or fan_uuid, price, off)
        _fv_trace(persona, 'ppv',
                  f"\U0001F48E {chosen['name']} tier {idx + 1}/{len(chosen['tiers'])}"
                  f"{again} → {handle or fan_uuid} at ${price / 100:g}{off}: {cap}")


def _fv_deliver(persona, scope, fan_uuid, fan_key, handle, reply, incoming, cfg,
                ppv_ctx, active=True):
    """Pace out one reply, then consider the next PPV tier so the paid drop
    always lands after the message it belongs to."""
    # The unlock is minted by _fv_maybe_ppv below, not by the model writing a
    # marker into the caption.
    reply = strip_ppv_marker(reply)
    with app.app_context():
        try:
            _fv_send_human(persona, scope, fan_uuid, reply, incoming=incoming,
                           cfg=cfg, active=active)
        except Exception as e:
            logger.warning('Fanvue send to %s failed: %s', handle or fan_uuid, str(e)[:120])
            _fv_trace(persona, 'error', f'send to {handle or fan_uuid} failed: {str(e)[:200]}')
            return
        _log_x_message(persona, fan_key, handle, 'out', reply)
        _fv_trace(persona, 'sent', f'→ {handle or fan_uuid}: {reply}')
        if ppv_ctx:
            _fv_maybe_ppv(persona, scope, fan_uuid, fan_key, handle, reply, ppv_ctx,
                          context=incoming)


def _fv_workers():
    return max(1, int(os.getenv('FANVUE_REPLY_WORKERS', '24')))


def _fv_pool():
    if _fv_reply_pool[0] is None:
        from concurrent.futures import ThreadPoolExecutor
        _fv_reply_pool[0] = ThreadPoolExecutor(
            max_workers=_fv_workers(), thread_name_prefix='fvreply')
    return _fv_reply_pool[0]


def _fv_submit(persona, *args):
    """Queue one paced reply and report how deep the queue is.

    Each reply holds its worker for the whole of its pause, so the pool is the
    real ceiling on throughput. When it saturates, replies do not just run late,
    they run later and later — and nothing in the log used to say so.
    """
    def run():
        try:
            _fv_deliver(*args)
        finally:
            with _fv_inflight_lock:
                _fv_inflight[0] = max(0, _fv_inflight[0] - 1)

    with _fv_inflight_lock:
        _fv_inflight[0] += 1
        depth = _fv_inflight[0]
    _fv_pool().submit(run)
    _fv_note_backlog(persona, depth)
    return depth


def _fv_note_backlog(persona, depth):
    """Trace the moment the queue starts running late, and nothing after.

    Deduping on the message would log again on every new depth — which is every
    submit — so the flag is the *state*, not the number.
    """
    key = f'fanvue_backlogged_{persona}'
    backlogged = depth > _fv_workers()
    if backlogged == (_get_setting(key) == '1'):
        return
    _set_setting(key, '1' if backlogged else '')
    if backlogged:
        _fv_trace(persona, 'delayed',
                  f'{depth} replies queued but only {_fv_workers()} can be paced at '
                  'once — replies are running late. Raise FANVUE_REPLY_WORKERS.')
    else:
        _fv_trace(persona, 'delayed', 'the reply queue has caught up')


def _fanvue_auto_settings(persona):
    try:
        return json.loads(_get_setting(f'fanvue_auto_{persona}') or '{}')
    except Exception:
        return {}


def _fanvue_enabled_list():
    try:
        return json.loads(_get_setting('fanvue_auto_personas') or '[]')
    except Exception:
        return []


def _fanvue_msg_count(persona, fan_key):
    try:
        from db import SessionLocal, count_x_messages
        s = SessionLocal()
        try:
            return count_x_messages(s, persona, fan_key)
        finally:
            s.close()
    except Exception:
        return 0


def _fanvue_saved_history(persona, fan_key, limit=40):
    """Return the saved conversation as [(direction, text), ...], oldest first."""
    try:
        from db import SessionLocal, list_x_messages
        s = SessionLocal()
        try:
            rows = list_x_messages(s, persona, fan_key, limit=500)
            return [(m.direction, m.text) for m in rows][-limit:]
        finally:
            s.close()
    except Exception:
        return []


def _fanvue_import_history(persona, fan_uuid, handle, me_uuid, cap=200):
    """Pull the full chat history from Fanvue and store it permanently, so the
    persona remembers everything already discussed.

    Every line is classified before any of it is written. A history that
    attributes our own messages to the fan is worse than no history at all — it
    is what makes her greet him by her own name — so one unresolvable message
    aborts the whole import and leaves the chat to start clean.

    Returns (count imported, [what the fan said]).
    """
    if not me_uuid:
        _fv_trace(persona, 'error',
                  f'skipped importing history for {handle or fan_uuid}: '
                  'this account\'s own Fanvue uuid could not be resolved')
        return 0, []
    try:
        msgs = _fv_list(_fanvue_call(persona, 'GET', f'{_fanvue_scope(persona)}/chats/{fan_uuid}/messages?limit={cap}'))
    except Exception:
        return 0, []
    msgs = sorted(msgs, key=lambda m: _fv_first(m, 'createdAt', 'sentAt', 'timestamp', default=''))
    rows = []
    for m in msgs:
        mt = _fv_first(m, 'text', 'content', 'message', 'body', default='')
        if not mt:
            continue
        direction = _fv_direction_of(m, fan_uuid, me_uuid)
        if not direction:
            _fv_trace(persona, 'error',
                      f'skipped importing history for {handle or fan_uuid}: could not '
                      f'tell who sent one message (keys={sorted(m.keys())[:8]}). Starting '
                      'this chat without history rather than mixing up who said what.')
            return 0, []
        rows.append((direction, mt))
    fan_key = 'fv:' + fan_uuid
    for direction, mt in rows:
        _log_x_message(persona, fan_key, handle, direction, mt)
    return len(rows), [t for d, t in rows if d == 'in']


def _fanvue_auto_round(persona):
    """One live auto-reply round: reply in-persona to new fan messages, skipping
    other creators when configured. Returns (actions, log)."""
    opts = _fanvue_auto_settings(persona)
    exclude_creators = opts.get('exclude_creators', True)
    online_only = bool(opts.get('online_only'))
    online_grace = max(0, min(int(opts.get('online_grace', 5)), 120))
    reply_limit = max(1, min(int(opts.get('reply_limit', 10)), 30))
    only = [h.strip().lstrip('@').lower()
            for h in (opts.get('only_handles') or '').split(',') if h.strip()]
    actions = {'replies': 0, 'skipped_creators': 0, 'skipped_offline': 0,
               'skipped_lists': 0}
    log = []
    inc_lists, exc_lists = _fv_list_filter(persona, opts)

    me_uuid = _fanvue_me_uuid(persona)
    cursor_key = f'fanvue_cursor_{persona}'
    try:
        cursor = json.loads(_get_setting(cursor_key) or '{}')
    except Exception:
        cursor = {}

    # Ordered PPV tiers + how many tiers each fan has already received, so tiers
    # go out one at a time, in order, as the conversation deepens.
    ppv_sets = _fanvue_ppv_sets(persona)
    ppv_on = bool(_fanvue_ppv(persona).get('enabled', True)) and bool(ppv_sets)
    ppv_tz = _fanvue_ppv(persona).get('tz_offset') or 0
    ppv_state_key = f'fanvue_ppv_state_{persona}'
    ppv_paid_key = f'fanvue_ppv_testpaid_{persona}'
    if ppv_on:
        _fv_migrate_ppv_state(persona, ppv_sets)
    # A fan typing this marks their own unlock paid, so it must never be live
    # in production — the env flag is the guard, not the empty string.
    ppv_test_phrase = (opts.get('ppv_test_phrase') or '').strip().lower()
    if ppv_test_phrase and (os.getenv('FANVUE_PPV_TEST_PHRASE_ENABLED') or '').strip().lower() \
            not in ('1', 'true', 'yes'):
        ppv_test_phrase = ''
    hcfg = _fv_humanize_cfg(persona)
    # Messages that must pass before the first drop, and between later ones.
    ppv_first_after = max(1, min(int(opts.get('ppv_first_after', 6) or 6), 200))
    ppv_gap = max(1, min(int(opts.get('ppv_gap', 8) or 8), 200))
    # Offer an unbought drop again after this much more chat, at most this often.
    ppv_retry_after = max(0, min(int(opts.get('ppv_retry_after', 0) or 0), 500))
    ppv_retry_max = max(0, min(int(opts.get('ppv_retry_max', 1) or 0), 10))
    # A drop they opened but didn't buy is a price objection, so the retry goes
    # out cheaper. And after this many days an unbought drop stops blocking the
    # ladder — one photo shouldn't end the funnel for a fan permanently.
    ppv_retry_discount = max(0.0, min(float(opts.get('ppv_retry_discount', 0) or 0), 0.8))
    ppv_stale_days = max(0, min(int(opts.get('ppv_stale_days', 14) or 0), 365))
    # When off, tiers advance on chatting alone (payment can't be verified for
    # agency-agent testers). When on, each later tier waits for the prior payment.
    ppv_require_payment = (_get_setting(f'fanvue_ppv_require_payment_{persona}') or '0') == '1'

    # Natural re-engagement: if the fan goes quiet, send up to a couple of
    # gentle follow-ups (spaced out), then wait. Reset when the fan replies.
    followup_key = f'fanvue_followup_{persona}'
    try:
        followups = json.loads(_get_setting(followup_key) or '{}')
    except Exception:
        followups = {}
    FOLLOWUP_MAX = 2          # at most this many nudges per quiet spell
    try:
        FOLLOWUP_BASE_MIN = int(float(_get_setting(f'fanvue_followup_min_{persona}') or 30))
    except Exception:
        FOLLOWUP_BASE_MIN = 30   # first nudge after ~30m silence, then longer

    scope = _fanvue_scope(persona)
    chats = _fv_list(_fanvue_call(persona, 'GET', f'{scope}/chats?limit=30'))
    cr = _fanvue_creator(persona).get('handle')
    log.append(f'{len(chats)} chats found' + (f' (acting as @{cr})' if cr else '') + (f'; only={only}' if only else ''))
    for chat in chats:
        if actions['replies'] >= reply_limit:
            break
        fan_uuid, handle, is_creator, chat_uuid = _fv_user_of_chat(chat)
        if not fan_uuid:
            log.append(f'skip chat: no fan_uuid (keys={list(chat.keys())})')
            continue
        who = handle or fan_uuid[:8]
        if exclude_creators and is_creator:
            actions['skipped_creators'] += 1
            log.append(f'{who}: skipped (is a creator)')
            continue
        if only and (handle or '').lower() not in only:
            log.append(f'{who}: skipped (not in only-list)')
            continue
        if inc_lists is not None and fan_uuid not in inc_lists:
            actions['skipped_lists'] += 1
            log.append(f'{who}: skipped (not in the included list(s))')
            continue
        if fan_uuid in exc_lists:
            actions['skipped_lists'] += 1
            log.append(f'{who}: skipped (in an excluded list)')
            continue
        if online_only and not _fv_chat_online(chat, online_grace):
            actions['skipped_offline'] += 1
            log.append(f'{who}: skipped (offline)')
            continue
        fan_key = 'fv:' + fan_uuid
        try:
            msgs = _fv_list(_fanvue_call(persona, 'GET', f'{scope}/chats/{fan_uuid}/messages?limit=20'))
        except Exception as e:
            log.append(f'read {who} failed: {str(e)[:50]}')
            continue
        if not msgs:
            log.append(f'{who}: no messages in chat')
            continue

        # First time we see this fan: import the whole chat history and keep it
        # permanently, so the bot remembers everything already said.
        did_import = False
        if _fanvue_msg_count(persona, fan_key) == 0:
            n, said = _fanvue_import_history(persona, fan_uuid, handle, me_uuid)
            if n:
                did_import = True
                log.append(f'Imported {n} past msgs from {handle or fan_uuid}')
                # Everything he told us before the account was connected only
                # reached the transcript, never the profile — so she would ask
                # his name again on the first message she ever sent him.
                if said:
                    _fan_memory_update(persona, fan_key, '\n'.join(said[-40:])[-1500:])

        # Order oldest→newest; the API may return newest first.
        newest = msgs[-1] if len(msgs) > 1 and _fv_first(msgs[0], 'createdAt', 'sentAt', default='') <= _fv_first(msgs[-1], 'createdAt', 'sentAt', default='') else msgs[0]
        text = _fv_first(newest, 'text', 'content', 'message', 'body', default='')
        msg_id = _fv_first(newest, 'uuid', 'id', default='')
        if not text:
            log.append(f'{who}: newest has no text (keys={list(newest.keys())})')
            continue
        # Durable rule: ONLY reply when the newest message came from the fan.
        # Our own outbound never resolves to fan_uuid, so this can't loop on
        # itself.
        recent_out = {t.strip() for (d, t) in _fanvue_saved_history(persona, fan_key, limit=12) if d == 'out'}
        direction = _fv_direction_of(newest, fan_uuid, me_uuid, recent_out)
        # An unresolved direction here is not fatal the way it is in the import:
        # this is the newest message in a chat we are already polling, so
        # "nothing says it was ours" is good enough to answer it.
        from_fan = direction == 'in' or (not direction and text.strip() not in recent_out)
        if not from_fan:
            # Fan is quiet (our message is newest). Send a spaced, capped
            # follow-up so chats feel alive without spamming or self-looping.
            sent_n = int(followups.get(fan_uuid, {}).get('n', 0)) if isinstance(followups.get(fan_uuid), dict) else 0
            age = _fv_msg_age_minutes(newest)
            need = FOLLOWUP_BASE_MIN * (sent_n + 1)  # 3h, then 6h, …
            if actions['replies'] >= reply_limit:
                continue
            if sent_n >= FOLLOWUP_MAX or age is None or age < need:
                log.append(f'{who}: quiet {int(age) if age else "?"}m, follow-ups {sent_n}/{FOLLOWUP_MAX} — waiting')
                continue
            hist = [{'role': 'model' if d == 'out' else 'user', 'content': t}
                    for (d, t) in _fanvue_saved_history(persona, fan_key, limit=40)]
            fu_lim = persona_length_limits(persona)
            fu_instr = (
                "This fan went quiet and hasn't replied to your last message. Send "
                "ONE warm, natural follow-up like a real person double-texting "
                "— playful and low-pressure, NOT needy or salesy. Reference something "
                "from earlier if it fits. Do NOT repeat your previous message. "
                + fu_lim['note'] + ' ' + NO_PLACEHOLDER_RULE)
            fu = _strip_placeholders(
                _fv_trim(_persona_text(persona, fu_instr, history=hist,
                                       max_tokens=fu_lim['tokens'], temperature=0.95),
                         max_sentences=fu_lim['sentences'], hard_cap=fu_lim['cap']))
            if not fu:
                continue
            followups[fan_uuid] = {'n': sent_n + 1}
            _set_setting(followup_key, json.dumps(followups))
            actions['replies'] += 1
            if hcfg['humanize']:
                _fv_submit(persona, persona, scope, fan_uuid, fan_key,
                           handle, fu, '', hcfg, None, False)
                log.append(f'↩ follow-up {sent_n + 1}/{FOLLOWUP_MAX} → {who} (typing…): {fu[:40]}')
                _fv_trace(persona, 'follow-up',
                          f'{who} went quiet — nudge {sent_n + 1}/{FOLLOWUP_MAX} on the way')
            else:
                _fv_deliver(persona, scope, fan_uuid, fan_key, handle, fu, '', hcfg,
                            None, False)
                log.append(f'↩ follow-up {sent_n + 1}/{FOLLOWUP_MAX} → {who}: {fu[:40]}')
            continue
        # Fan replied — clear any pending follow-up state for them.
        if followups.pop(fan_uuid, None) is not None:
            _set_setting(followup_key, json.dumps(followups))
        if cursor.get(fan_uuid) == msg_id:
            log.append(f'{who}: already replied to their latest')
            continue  # already handled this latest inbound message

        # Persist the new inbound (import already stored it on first contact).
        if not did_import:
            _log_x_message(persona, fan_key, handle, 'in', text)
        _fv_trace(persona, 'received', f'← {who}: {text}')

        # Testing aid: a phrase the tester sends to stand in for a real unlock,
        # so the next PPV tier can be reached without paying for the last one.
        if ppv_test_phrase and ppv_test_phrase in text.lower():
            try:
                paid_map = json.loads(_get_setting(ppv_paid_key) or '{}')
            except Exception:
                paid_map = {}
            try:
                state_map = json.loads(_get_setting(ppv_state_key) or '{}')
            except Exception:
                state_map = {}
            paid_map[fan_uuid] = sum(
                _fv_fan_state(state_map, fan_uuid, ppv_sets)['sets'].values())
            _set_setting(ppv_paid_key, json.dumps(paid_map))
            _fv_trace(persona, 'ppv', f'{who} said the test phrase — tier '
                                      f'{paid_map[fan_uuid]} counted as paid')

        # Build the LLM history from the full saved conversation (memory).
        history = [{'role': 'model' if d == 'out' else 'user', 'content': t}
                   for (d, t) in _fanvue_saved_history(persona, fan_key, limit=40)]
        _fan_memory_update(persona, fan_key, text)
        has_history = any(d == 'out' for (d, t) in
                          _fanvue_saved_history(persona, fan_key, limit=40))
        intro_rule = (
            "You are ALREADY mid-conversation with this fan. Do NOT introduce "
            "yourself, do NOT state your name/age/where you're from, and do NOT "
            "greet them like it's the first message — just continue naturally. "
            if has_history else
            "This is an early message — a short friendly opener is fine. ")
        pcfg = load_persona_config(persona)
        lim = reply_length_limits(pcfg)
        may_ask = question_allowed(pcfg, history)
        instruction = (
            "Reply to this Fanvue fan in-character. You have the full earlier "
            "conversation above — USE it: do not re-ask anything they already told "
            "you (their name, where they're from, their interests, what they like). "
            + intro_rule +
            "Be warm and engaging, move the rapport → tease → offer funnel naturally "
            "(never hard-sell). "
            + question_rule_for(pcfg, history)
            + lim['note'] + ' ' + NO_PLACEHOLDER_RULE
            # Say what is about to go out, so the reply lands on it. Without
            # this the model writes blind and a caption-less tier ships the
            # conversational reply as the sales line on a paid unlock.
            + _fv_queued_hint(persona, fan_uuid, fan_key, ppv_sets if ppv_on else [],
                              ppv_state_key, text, ppv_tz)
            + "Their latest message: "
            f"\"{text}\"")
        instruction = _fan_memory_block(_fan_memory(persona, fan_key), persona) + instruction
        reply = _persona_text(persona, instruction, history=history,
                              max_tokens=lim['tokens'], temperature=0.9)
        reply = _fv_trim(reply, max_sentences=lim['sentences'], hard_cap=lim['cap'])
        reply = _strip_placeholders(trim_extra_questions(reply, may_ask))
        if not reply:
            _fv_trace(persona, 'error', f'{who}: the model returned nothing — no reply sent')
            continue

        # Mark cursor BEFORE sending to prevent duplicate replies on retry
        cursor[fan_uuid] = msg_id
        _set_setting(cursor_key, json.dumps(cursor))

        ppv_ctx = {'sets': ppv_sets, 'gap': ppv_gap, 'first_after': ppv_first_after,
                   'retry_after': ppv_retry_after, 'retry_max': ppv_retry_max,
                   'retry_discount': ppv_retry_discount, 'stale_days': ppv_stale_days,
                   'state_key': ppv_state_key, 'paid_key': ppv_paid_key,
                   'tz_offset': ppv_tz,
                   'require_payment': ppv_require_payment} if ppv_on else None
        age = _fv_msg_age_minutes(newest)
        active = age is None or age < FV_ACTIVE_MIN
        args = (persona, scope, fan_uuid, fan_key, handle, reply.strip(), text, hcfg,
                ppv_ctx, active)
        actions['replies'] += 1
        if hcfg['humanize']:
            # Pace it on a worker so one fan's pause never delays the next fan.
            _fv_submit(persona, *args)
            log.append(f'Replying → {handle or fan_uuid} (typing…): {reply[:50]}')
            _fv_trace(persona, 'typing', f'{who}: writing a reply…')
        else:
            _fv_deliver(*args)
            log.append(f'Replied → {handle or fan_uuid}: {reply[:50]}')

    _set_setting(cursor_key, json.dumps(cursor))

    # A round that replies to nobody leaves no trace at all, which reads as "the
    # bot is dead" when it is really just skipping every chat. Record why —
    # once per change, so a steady state does not fill the log every 20s.
    if not actions['replies']:
        why = []
        if not chats:
            why.append('no chats')
        if actions['skipped_offline']:
            why.append(f"{actions['skipped_offline']} offline")
        if actions['skipped_lists']:
            why.append(f"{actions['skipped_lists']} filtered by list")
        if actions['skipped_creators']:
            why.append(f"{actions['skipped_creators']} are creators")
        summary = f"{len(chats)} chats, no replies" + (' — ' + ', '.join(why) if why else
                                                       ' — everyone already answered')
        state_key = f'fanvue_lastidle_{persona}'
        if _get_setting(state_key) != summary:
            _set_setting(state_key, summary)
            _fv_trace(persona, 'idle', summary)
    else:
        _set_setting(f'fanvue_lastidle_{persona}', '')

    return actions, log


@app.route('/api/fanvue/auto', methods=['GET', 'POST'])
@platform_scoped
def api_fanvue_auto():
    """Get or set the persistent auto-reply toggle + options for a persona."""
    if request.method == 'POST':
        data = request.json or {}
        persona = (data.get('persona') or '').strip()
        if not persona:
            return jsonify({'ok': False, 'error': 'persona required'}), 400
        opts = _fanvue_auto_settings(persona)
        if 'exclude_creators' in data:
            opts['exclude_creators'] = bool(data['exclude_creators'])
        if 'reply_limit' in data:
            opts['reply_limit'] = int(data['reply_limit'])
        if 'only_handles' in data:
            opts['only_handles'] = (data.get('only_handles') or '').strip()
        if 'online_only' in data:
            opts['online_only'] = bool(data['online_only'])
        if 'online_grace' in data:
            opts['online_grace'] = max(0, min(int(data['online_grace'] or 5), 120))
        if 'humanize' in data:
            opts['humanize'] = bool(data['humanize'])
        if 'typing_speed' in data:
            opts['typing_speed'] = max(4, min(int(data['typing_speed'] or 14), 40))
        if 'react_rate' in data:
            opts['react_rate'] = max(0, min(int(data['react_rate'] or 0), 100))
        followups = bool(user_capabilities(_current_user()).get('scheduled_followups'))
        if 'followup_min' in data and followups:
            try:
                _set_setting(f'fanvue_followup_min_{persona}', str(int(float(data['followup_min']))))
            except (ValueError, TypeError):
                pass
        if 'include_lists' in data:
            opts['include_lists'] = _fv_clean_lists(data['include_lists'])
        if 'exclude_lists' in data:
            opts['exclude_lists'] = _fv_clean_lists(data['exclude_lists'])
        if 'ppv_first_after' in data:
            opts['ppv_first_after'] = max(1, min(int(data['ppv_first_after'] or 6), 200))
        if 'ppv_gap' in data:
            opts['ppv_gap'] = max(1, min(int(data['ppv_gap'] or 8), 200))
        if 'ppv_retry_after' in data:
            opts['ppv_retry_after'] = max(0, min(int(data['ppv_retry_after'] or 0), 500))
        if 'ppv_retry_max' in data:
            opts['ppv_retry_max'] = max(0, min(int(data['ppv_retry_max'] or 0), 10))
        if 'ppv_stale_days' in data:
            opts['ppv_stale_days'] = max(0, min(int(data['ppv_stale_days'] or 0), 365))
        if 'ppv_retry_discount' in data:
            opts['ppv_retry_discount'] = max(0.0, min(
                float(data['ppv_retry_discount'] or 0), 0.8))
        if 'ppv_test_phrase' in data:
            opts['ppv_test_phrase'] = (data.get('ppv_test_phrase') or '').strip()[:80]
        if 'ppv_require_payment' in data:
            _set_setting(f'fanvue_ppv_require_payment_{persona}', '1' if data['ppv_require_payment'] else '0')
        enabled = bool(data.get('enabled', opts.get('enabled', False)))
        opts['enabled'] = enabled
        _set_setting(f'fanvue_auto_{persona}', json.dumps(opts))
        lst = set(_fanvue_enabled_list())
        lst.add(persona) if enabled else lst.discard(persona)
        _set_setting('fanvue_auto_personas', json.dumps(sorted(lst)))
        return jsonify({'ok': True, 'enabled': enabled, 'options': opts,
                        'scheduled_followups': followups})
    persona = (request.args.get('persona') or '').strip()
    opts = _fanvue_auto_settings(persona)
    return jsonify({'enabled': bool(opts.get('enabled')),
                    'exclude_creators': opts.get('exclude_creators', True),
                    'reply_limit': opts.get('reply_limit', 10),
                    'only_handles': opts.get('only_handles', ''),
                    'online_only': bool(opts.get('online_only')),
                    'online_grace': opts.get('online_grace', 5),
                    'humanize': bool(opts.get('humanize', True)),
                    'typing_speed': max(4, min(int(opts.get('typing_speed') or 14), 40)),
                    'react_rate': max(0, min(int(opts.get('react_rate', 25)), 100)),
                    'include_lists': _fv_clean_lists(opts.get('include_lists')),
                    'exclude_lists': _fv_clean_lists(opts.get('exclude_lists')),
                    'ppv_test_phrase': opts.get('ppv_test_phrase', ''),
                    'ppv_first_after': int(opts.get('ppv_first_after', 6) or 6),
                    'ppv_gap': int(opts.get('ppv_gap', 8) or 8),
                    'ppv_retry_after': int(opts.get('ppv_retry_after', 0) or 0),
                    'ppv_retry_max': int(opts.get('ppv_retry_max', 1) or 0),
                    'ppv_stale_days': int(opts.get('ppv_stale_days', 14) or 0),
                    'ppv_retry_discount': float(opts.get('ppv_retry_discount', 0) or 0),
                    'followup_min': int(_get_setting(f'fanvue_followup_min_{persona}') or 30),
                    'scheduled_followups': bool(user_capabilities(_current_user()).get('scheduled_followups')),
                    'ppv_require_payment': (_get_setting(f'fanvue_ppv_require_payment_{persona}') or '0') == '1'})


@app.route('/api/fanvue/ppv-reset', methods=['POST'])
@platform_scoped
def api_fanvue_ppv_reset():
    """Forget what has already been *sent*, so the sets start from the top again.

    Purchases are a different fact and are never touched: the ppv_drops ledger
    is what later confirms a fan paid, and it has to outlive any number of
    progress resets. Resetting everyone needs confirm:true — it used to be one
    unconfirmed POST away."""
    d = request.json or {}
    persona = (d.get('persona') or '').strip()
    if not persona:
        return jsonify({'ok': False, 'error': 'Missing persona'}), 400
    fan = (d.get('fan_uuid') or '').strip()
    if not fan and not d.get('confirm'):
        return jsonify({'ok': False, 'needs_confirm': True, 'error':
                        'This restarts the sets for every fan on this persona. '
                        'Send confirm:true to go ahead.'}), 400
    keys = [f'fanvue_ppv_state_{persona}', f'fanvue_ppv_sent_{persona}',
            f'fanvue_ppv_at_{persona}', f'fanvue_ppv_last_{persona}',
            f'fanvue_ppv_testpaid_{persona}', f'fanvue_ppv_retry_{persona}']
    cleared = 0
    for k in keys:
        if fan:
            try:
                m = json.loads(_get_setting(k) or '{}')
            except Exception:
                m = {}
            if isinstance(m, dict) and m.pop(fan, None) is not None:
                cleared += 1
                _set_setting(k, json.dumps(m))
        else:
            _set_setting(k, '{}')
    who = fan or 'every fan'
    _fv_trace(persona, 'ppv', f'PPV send progress reset for {who} — the sets '
                              'start again (purchase history kept)')
    return jsonify({'ok': True, 'fan_uuid': fan, 'purchases_kept': True,
                    'cleared': cleared if fan else len(keys)})


@app.route('/api/fanvue/lists')
@platform_scoped
def api_fanvue_lists():
    """The persona's Fanvue chat lists — smart segments and custom lists — for
    the include/exclude picker."""
    persona = (request.args.get('persona') or '').strip()
    if not _fanvue_tokens(persona).get('access_token'):
        return jsonify({'lists': [], 'error': 'Fanvue not connected for this persona.'})
    if request.args.get('refresh') == '1':
        for k in [k for k in _fv_list_cache if k[0] == persona]:
            _fv_list_cache.pop(k, None)
    opts = _fanvue_auto_settings(persona)
    return jsonify({'lists': _fanvue_chat_lists(persona),
                    'include_lists': _fv_clean_lists(opts.get('include_lists')),
                    'exclude_lists': _fv_clean_lists(opts.get('exclude_lists'))})


@app.route('/api/fanvue/trace', methods=['GET', 'DELETE'])
@platform_scoped
def api_fanvue_trace():
    """Recent Fanvue chat activity for a persona — what came in, what went out,
    PPV drops and errors — plus a verdict when nothing is happening."""
    # The same resolution the ownership guard used, so the two can never
    # disagree about which persona this request is for.
    persona = request_persona()
    if request.method == 'DELETE':
        _set_setting(f'fanvue_trace_{persona}', '[]')
        return jsonify({'ok': True})
    try:
        rows = json.loads(_get_setting(f'fanvue_trace_{persona}') or '[]')
    except Exception:
        rows = []
    if not isinstance(rows, list):
        rows = []
    opts = _fanvue_auto_settings(persona)
    connected = bool(_fanvue_tokens(persona).get('access_token'))
    lock = _fanvue_round_locks.get(persona)
    problems = []
    if not connected:
        problems.append('Fanvue is not connected for this persona.')
    elif not opts.get('enabled'):
        problems.append('Auto-reply is off — turn it on for her to answer fans.')
    elif not rows:
        problems.append('No activity recorded yet. Rounds run every 20s; a line '
                        'appears here as soon as a fan writes.')
    if opts.get('only_handles'):
        problems.append('Only replying to: ' + opts['only_handles'])
    if opts.get('online_only'):
        problems.append('Only replying to fans who are online right now.')
    if _fv_clean_lists(opts.get('include_lists')):
        problems.append('Only replying to fans in: ' + ', '.join(
            l['name'] for l in _fv_clean_lists(opts['include_lists'])))
    if _fv_clean_lists(opts.get('exclude_lists')):
        problems.append('Never replying to fans in: ' + ', '.join(
            l['name'] for l in _fv_clean_lists(opts['exclude_lists'])))
    if opts.get('ppv_test_phrase'):
        if (os.getenv('FANVUE_PPV_TEST_PHRASE_ENABLED') or '').strip().lower() \
                in ('1', 'true', 'yes'):
            problems.append('PPV test phrase is active: "%s" — a fan saying it '
                            'counts as a paid unlock.' % opts['ppv_test_phrase'])
        else:
            problems.append('PPV test phrase is set but ignored — set '
                            'FANVUE_PPV_TEST_PHRASE_ENABLED=1 to use it in testing.')
    # The single failure that silently loses every purchase: without a real
    # Postgres the store is a temp file, wiped on the next cold start.
    ephemeral = _fv_storage_is_ephemeral()
    if ephemeral:
        problems.append('Purchases will be forgotten on the next redeploy — the '
                        'database is an ephemeral file. Set DATABASE_URL (or the '
                        'Cloud SQL env vars) to keep them.')
    if not _fv_webhook_secret():
        problems.append('No FANVUE_WEBHOOK_SECRET, so payment webhooks are '
                        'rejected. Purchases are still picked up by Reconcile, '
                        'just later.')
    return jsonify({'persona': persona, 'connected': connected,
                    'enabled': bool(opts.get('enabled')),
                    'running': bool(lock and lock.locked()),
                    'queued': _fv_inflight[0], 'workers': _fv_workers(),
                    'storage_ephemeral': ephemeral,
                    'webhook_ready': bool(_fv_webhook_secret()),
                    'problems': problems, 'rows': rows[-FV_TRACE_MAX:]})


@app.route('/api/fanvue/accounts')
def api_fanvue_accounts():
    """Every persona's Fanvue connection at a glance, so several accounts can be
    watched running side by side."""
    mine = owned_slugs()
    enabled = set(_fanvue_enabled_list())
    out = []
    slugs = {p.get('slug') for p in db_list_personas()} | enabled
    if mine is not None:
        slugs &= mine
    for slug in sorted(s for s in slugs if s):
        tok = _fanvue_tokens(slug)
        if not tok.get('access_token') and slug not in enabled:
            continue
        lock = _fanvue_round_locks.get(slug)
        out.append({
            'persona': slug,
            'name': slug,
            'connected': bool(tok.get('access_token')),
            'auto': slug in enabled,
            'running': bool(lock and lock.locked()),
            'creator': _fanvue_creator(slug).get('handle', ''),
        })
    return jsonify({'accounts': out, 'active': sum(1 for a in out if a['auto'] and a['connected'])})


@app.route('/api/fanvue/auto-run', methods=['POST'])
@platform_scoped
def api_fanvue_auto_run():
    """Run one auto-reply round now (also used by the background worker)."""
    persona = (request.json or {}).get('persona', '').strip()
    if not persona:
        return jsonify({'ok': False, 'error': 'persona required'}), 400
    if not _fanvue_tokens(persona).get('access_token'):
        return jsonify({'ok': False, 'error': 'Fanvue not connected for this persona.'}), 400
    try:
        res = _fanvue_round_now(persona, block=True)
        actions, log = res if res else ({}, [])
        return jsonify({'ok': True, 'actions': actions, 'log': log})
    except url_error.HTTPError as e:
        return jsonify({'ok': False, 'error': f'Fanvue API {e.code}: {e.read()[:200].decode(errors="ignore")}'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 400


@app.route('/api/fanvue/debug')
@operator_only
def api_fanvue_debug():
    """Dump raw Fanvue JSON (me / chats / first chat's messages) so the exact
    field names can be confirmed. Admin-gated; used to fix parsing quickly."""
    persona = (request.args.get('persona') or '').strip()
    out = {}
    try:
        out['me'] = _fanvue_call(persona, 'GET', '/users/me')
    except Exception as e:
        out['me_error'] = str(e)[:200]
    try:
        chats = _fanvue_call(persona, 'GET', '/chats?limit=3')
        out['chats'] = chats
        lst = _fv_list(chats)
        if lst:
            uid, handle, is_creator, cuid = _fv_user_of_chat(lst[0])
            out['parsed_first'] = {'uuid': uid, 'handle': handle, 'is_creator': is_creator, 'chat_uuid': cuid}
            out['raw_first_chat_keys'] = list(lst[0].keys()) if isinstance(lst[0], dict) else str(type(lst[0]))
            out['raw_first_chat'] = lst[0]
            try:
                out['first_messages'] = _fanvue_call(persona, 'GET', f'/chats/{uid}/messages?limit=3')
            except Exception as e:
                out['messages_error'] = str(e)[:200]
            if request.args.get('probe') == '1':
                probes = {}
                test_paths = [
                    (f'/chats/{uid}/message', {'text': 'hi'}),
                ]
                for path, body in test_paths:
                    key = f'POST {path} {list(body.keys())}'
                    try:
                        _fanvue_call(persona, 'POST', path, body=body)
                        probes[key] = 'OK'
                    except Exception as e:
                        probes[key] = str(e)[:120]
                out['send_probes'] = probes
    except Exception as e:
        out['chats_error'] = str(e)[:200]
    return jsonify(out)


_fanvue_worker_started = [False]


def _fanvue_round_now(persona, block=False):
    """Run one round for a persona, never letting that persona overlap itself.
    Returns (actions, log) or None when a round was already in flight."""
    lock = _fanvue_persona_lock(_fanvue_round_locks, persona)
    if not lock.acquire(blocking=block):
        return None
    try:
        with app.app_context():
            return _fanvue_auto_round(persona)
    finally:
        lock.release()


def _fanvue_worker():
    """Server-side loop: runs an auto-reply round for every enabled persona on an
    interval. Reads the enabled list + tokens from the DB, so after a redeploy the
    new instance resumes automatically without a browser tab.

    Rounds run in parallel, one thread per connected account, so a slow or
    chatty account never delays the others. Per-persona locks mean an account
    whose round outlives the interval is simply skipped next tick."""
    import time as _t
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=max(1, int(os.getenv('FANVUE_WORKERS', '8'))),
                              thread_name_prefix='fanvue')

    pending = set()

    def _one(persona):
        try:
            _fanvue_round_now(persona)
        except Exception as e:
            logger.exception('fanvue round failed for %s', persona)
            try:
                with app.app_context():
                    _fv_trace(persona, 'error', f'round failed: {str(e)[:200]}')
            except Exception:
                pass
        finally:
            with _fanvue_lock_guard:
                pending.discard(persona)

    while True:
        try:
            live = [p for p in _fanvue_enabled_list()
                    if _fanvue_tokens(p).get('access_token')]
            for persona in live:
                # Don't queue an account that is still running or waiting from
                # an earlier tick — the queue would grow without bound.
                with _fanvue_lock_guard:
                    if persona in pending:
                        continue
                    pending.add(persona)
                pool.submit(_one, persona)
        except Exception:
            logger.exception('fanvue worker tick failed')
        _t.sleep(20)


def _start_fanvue_worker():
    if _fanvue_worker_started[0]:
        return
    _fanvue_worker_started[0] = True
    threading.Thread(target=_fanvue_worker, daemon=True).start()


if _worker_enabled('FANVUE_WORKER'):
    _start_fanvue_worker()


# ── Error handler ─────────────────────────────────────────────────────────────

@app.errorhandler(Exception)
def handle_exception(e):
    # HTTPException covers 404/405/403 and friends. Without this they would be
    # reported as "Internal server error" 500, making a mistyped URL look like
    # a crash — and burying real 500s in the noise.
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        if (request.path or '').startswith('/api/'):
            return jsonify({'error': e.name, 'status': e.code}), e.code
        return e
    error_logger.error(f'Unhandled: {e}', exc_info=True)
    return jsonify({'error': 'Internal server error'}), 500


# ── Threads (Meta) reply-bot ─────────────────────────────────────────────────
# Public-only engagement: auto-reply to comments/replies on the persona's own
# posts and to mentions. No DMs (unsupported by the Threads API). Tokens are
# stored in the DB settings so they survive redeploys (durable with Cloud SQL).

THREADS_GRAPH = 'https://graph.threads.net'
THREADS_API = THREADS_GRAPH + '/v1.0'


def _threads_load_tokens():
    try:
        return json.loads(_get_setting('threads_tokens') or '{}')
    except Exception:
        return {}


def _threads_save_tokens(data):
    _set_setting('threads_tokens', json.dumps(data))


def _threads_api(method, url, body=None):
    headers = {'Content-Type': 'application/json'} if body else {}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def _threads_refresh(persona):
    """Refresh a long-lived token (valid 60d, refreshable after 24h)."""
    tokens = _threads_load_tokens()
    t = tokens.get(persona) or {}
    tok = t.get('access_token')
    if not tok:
        return None
    try:
        url = (f'{THREADS_GRAPH}/refresh_access_token?grant_type=th_refresh_token'
               f'&access_token={urllib.parse.quote(tok)}')
        td = _threads_api('GET', url)
    except Exception:
        return None
    if td.get('access_token'):
        t['access_token'] = td['access_token']
        t['expires_at'] = int(time.time()) + int(td.get('expires_in', 5184000))
        tokens[persona] = t
        _threads_save_tokens(tokens)
    return t.get('access_token')


def _threads_token(persona):
    tokens = _threads_load_tokens()
    t = tokens.get(persona) or {}
    tok = t.get('access_token')
    if not tok:
        raise RuntimeError(f'No Threads account connected for persona "{persona}".')
    # Proactively refresh if within 3 days of expiry.
    if t.get('expires_at') and t['expires_at'] - time.time() < 259200:
        refreshed = _threads_refresh(persona)
        if refreshed:
            return refreshed
    return tok


def _threads_uid(persona):
    return (_threads_load_tokens().get(persona) or {}).get('user_id', '')


def _threads_call(persona, method, path, params=None, body=None):
    """Call the Threads graph API as a persona. `path` is like '/{uid}/threads'."""
    tok = _threads_token(persona)
    q = dict(params or {})
    q['access_token'] = tok
    url = f'{THREADS_API}{path}?{urllib.parse.urlencode(q)}'
    return _threads_api(method, url, body=body)


def _threads_publish(persona, text, reply_to_id=None):
    """Two-step publish: create a text container, then publish it. Returns media id."""
    uid = _threads_uid(persona)
    params = {'media_type': 'TEXT', 'text': text[:500]}
    if reply_to_id:
        params['reply_to_id'] = reply_to_id
    created = _threads_call(persona, 'POST', f'/{uid}/threads', params=params)
    creation_id = created.get('id')
    if not creation_id:
        raise RuntimeError(f'Threads container failed: {created}')
    published = _threads_call(persona, 'POST', f'/{uid}/threads_publish',
                              params={'creation_id': creation_id})
    return published.get('id', '')


def _threads_recent_posts(persona, limit=10):
    uid = _threads_uid(persona)
    res = _threads_call(persona, 'GET', f'/{uid}/threads',
                        params={'fields': 'id,text,timestamp', 'limit': limit})
    return res.get('data', []) or []


def _threads_replies(persona, media_id):
    res = _threads_call(persona, 'GET', f'/{media_id}/replies',
                        params={'fields': 'id,text,username,timestamp'})
    return res.get('data', []) or []


def _threads_mentions(persona, limit=15):
    uid = _threads_uid(persona)
    try:
        res = _threads_call(persona, 'GET', f'/{uid}/mentions',
                            params={'fields': 'id,text,username,timestamp', 'limit': limit})
        return res.get('data', []) or []
    except Exception:
        return []


def _threads_auto_round(persona, reply_comments=True, reply_mentions=True,
                        limit=10, preview=False):
    """One engagement round: reply in-persona to new replies on the persona's own
    posts and to new mentions. Skips own replies + anything already handled."""
    actions = {'comment_replies': 0, 'mention_replies': 0}
    log = []
    replies_out = []
    tokens = _threads_load_tokens()
    t = tokens.get(persona) or {}
    my_username = (t.get('username') or '').lower()
    seen_key = f'threads_seen_{persona}'
    try:
        seen = set(json.loads(_get_setting(seen_key) or '[]'))
    except Exception:
        seen = set()

    targets = []
    if reply_comments:
        for post in _threads_recent_posts(persona, limit=limit):
            for rep in _threads_replies(persona, post.get('id', '')):
                targets.append(('comment', rep))
    if reply_mentions:
        for m in _threads_mentions(persona, limit=limit):
            targets.append(('mention', m))

    for kind, item in targets:
        rid = item.get('id', '')
        text = (item.get('text') or '').strip()
        who = item.get('username', '')
        if not rid or not text or rid in seen:
            continue
        if who and who.lower() == my_username:
            seen.add(rid)
            continue
        th_lim = persona_length_limits(persona)
        instr = (f'A fan {("replied to your post" if kind=="comment" else "mentioned you")} '
                 f'on Threads: "{text}". Write ONE warm, in-character public reply '
                 f'— playful, natural, no hashtags, not salesy. ' + th_lim['note'])
        reply = _fv_trim(_persona_text(persona, instr, max_tokens=th_lim['tokens'],
                                       temperature=0.9),
                         max_sentences=th_lim['sentences'], hard_cap=th_lim['cap'])
        if not reply:
            continue
        posted = False
        if not preview:
            try:
                _threads_publish(persona, reply, reply_to_id=rid)
                posted = True
                seen.add(rid)
                actions['comment_replies' if kind == 'comment' else 'mention_replies'] += 1
                log.append(f'↪ {kind} @{who}: {reply[:50]}')
            except Exception as e:
                log.append(f'reply @{who} failed: {str(e)[:50]}')
        replies_out.append({'kind': kind, 'to': who, 'comment': text,
                            'reply': reply, 'posted': posted})

    if not preview:
        _set_setting(seen_key, json.dumps(list(seen)[-500:]))
    return actions, log, replies_out


@app.route('/api/threads/app-config')
@operator_only
def api_threads_app_config():
    return jsonify({
        'client_id': _get_setting('threads_client_id') or '',
        'redirect_uri': _get_setting('threads_redirect_uri') or '',
        'has_secret': bool(_get_setting('threads_client_secret')),
    })


@app.route('/api/threads/auth-url', methods=['POST'])
@platform_scoped
def api_threads_auth_url():
    data = request.json or {}
    client_id = (data.get('client_id') or '').strip() or (_get_setting('threads_client_id') or '')
    client_secret = (data.get('client_secret') or '').strip() or (_get_setting('threads_client_secret') or '')
    redirect_uri = (data.get('redirect_uri') or '').strip() or (_get_setting('threads_redirect_uri') or '')
    persona = (data.get('persona') or 'lilith').strip()
    if not client_id or not client_secret or not redirect_uri:
        return jsonify({'ok': False, 'error': 'client_id, client_secret and redirect_uri are required'}), 400
    _set_setting('threads_client_id', client_id)
    _set_setting('threads_client_secret', client_secret)
    _set_setting('threads_redirect_uri', redirect_uri)
    state = secrets.token_urlsafe(16)
    _set_setting('threads_oauth_state', json.dumps({'state': state, 'persona': persona}))
    params = urllib.parse.urlencode({
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'scope': 'threads_basic,threads_content_publish,threads_read_replies,threads_manage_replies',
        'response_type': 'code',
        'state': state,
    })
    return jsonify({'ok': True, 'url': f'https://threads.net/oauth/authorize?{params}'})


@app.route('/api/threads/oauth-redirect', methods=['GET'])
def api_threads_oauth_redirect():
    code = request.args.get('code', '')
    state = request.args.get('state', '')
    error = request.args.get('error_description', '') or request.args.get('error', '')
    payload = json.dumps({'type': 'threads_oauth', 'code': code, 'state': state, 'error': error})
    html = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Threads Authorization</title>
<style>body{background:#0d0d0f;color:#f4f4f5;font-family:system-ui,sans-serif;display:flex;
align-items:center;justify-content:center;min-height:100vh;text-align:center;padding:24px}
.card{max-width:420px}</style></head><body><div class="card">
<h2>%s</h2><p id="msg">Returning you to the bot…</p></div>
<script>var data=%s;try{if(window.opener){window.opener.postMessage(data,'*');
document.getElementById('msg').textContent='Connected — you can close this window.';
setTimeout(function(){window.close();},1200);}}catch(e){}</script></body></html>""" % (
        'Authorization failed' if error else 'Authorized ✓', payload)
    return html, (400 if error else 200), {'Content-Type': 'text/html; charset=utf-8'}


@app.route('/api/threads/callback', methods=['POST'])
@platform_scoped
def api_threads_callback():
    data = request.json or {}
    code = (data.get('code') or '').strip()
    state = (data.get('state') or '').strip()
    try:
        saved = json.loads(_get_setting('threads_oauth_state') or '{}')
    except Exception:
        saved = {}
    if not saved or state != saved.get('state'):
        return jsonify({'ok': False, 'error': 'State mismatch or expired. Start again.'}), 400
    persona = saved.get('persona', 'lilith')
    client_id = _get_setting('threads_client_id') or ''
    client_secret = _get_setting('threads_client_secret') or ''
    redirect_uri = _get_setting('threads_redirect_uri') or ''
    # Step 1: short-lived token.
    body = urllib.parse.urlencode({
        'client_id': client_id, 'client_secret': client_secret,
        'grant_type': 'authorization_code', 'redirect_uri': redirect_uri, 'code': code,
    }).encode()
    try:
        req = urllib.request.Request(f'{THREADS_GRAPH}/oauth/access_token', data=body,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
        with urllib.request.urlopen(req, timeout=15) as r:
            short = json.loads(r.read())
    except url_error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get('error_message', str(e))
        except Exception:
            msg = str(e)
        return jsonify({'ok': False, 'error': f'Threads token exchange failed: {msg}'}), 400
    short_tok = short.get('access_token', '')
    user_id = str(short.get('user_id', ''))
    # Step 2: exchange for a long-lived token (60 days).
    access_token, expires_in = short_tok, 3600
    try:
        ll = _threads_api('GET',
            f'{THREADS_GRAPH}/access_token?grant_type=th_exchange_token'
            f'&client_secret={urllib.parse.quote(client_secret)}'
            f'&access_token={urllib.parse.quote(short_tok)}')
        if ll.get('access_token'):
            access_token = ll['access_token']
            expires_in = int(ll.get('expires_in', 5184000))
    except Exception:
        pass
    # Fetch username.
    username = ''
    try:
        me = _threads_api('GET',
            f'{THREADS_API}/me?fields=id,username&access_token={urllib.parse.quote(access_token)}')
        username = me.get('username', '')
        user_id = str(me.get('id', user_id))
    except Exception:
        pass
    tokens = _threads_load_tokens()
    tokens[persona] = {'access_token': access_token, 'user_id': user_id,
                       'username': username, 'expires_at': int(time.time()) + expires_in}
    _threads_save_tokens(tokens)
    _set_setting('threads_oauth_state', '')
    return jsonify({'ok': True, 'username': username, 'persona': persona})


@app.route('/api/threads/status', methods=['GET'])
def api_threads_status():
    mine = owned_slugs()
    return jsonify({p: {'username': t.get('username', ''),
                        'connected': bool(t.get('access_token'))}
                    for p, t in _threads_load_tokens().items()
                    if mine is None or p in mine})


@app.route('/api/threads/disconnect', methods=['POST'])
@platform_scoped
def api_threads_disconnect():
    persona = (request.json or {}).get('persona', '').strip()
    tokens = _threads_load_tokens()
    tokens.pop(persona, None)
    _threads_save_tokens(tokens)
    return jsonify({'ok': True})


@app.route('/api/threads/publish', methods=['POST'])
@platform_scoped
def api_threads_publish():
    data = request.json or {}
    persona = (data.get('persona') or '').strip()
    topic = (data.get('topic') or '').strip()
    preview = bool(data.get('preview'))
    if not persona:
        return jsonify({'ok': False, 'error': 'persona required'}), 400
    instr = (f'Write ONE short, in-character Threads post{" about: " + topic if topic else ""}. '
             f'Natural and casual, 1-2 sentences, at most one emoji, no hashtags.')
    try:
        text = _fv_trim(_persona_text(persona, instr, max_tokens=200, temperature=0.95), hard_cap=480)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    if preview:
        return jsonify({'ok': True, 'text': text, 'posted': False})
    try:
        mid = _threads_publish(persona, text)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    return jsonify({'ok': True, 'text': text, 'posted': True, 'id': mid})


@app.route('/api/threads/auto', methods=['GET', 'POST'])
@platform_scoped
def api_threads_auto():
    if request.method == 'POST':
        data = request.json or {}
        persona = (data.get('persona') or '').strip()
        if not persona:
            return jsonify({'ok': False, 'error': 'persona required'}), 400
        opts = {
            'reply_comments': bool(data.get('reply_comments', True)),
            'reply_mentions': bool(data.get('reply_mentions', True)),
            'enabled': bool(data.get('enabled', False)),
        }
        _set_setting(f'threads_auto_{persona}', json.dumps(opts))
        try:
            lst = set(json.loads(_get_setting('threads_auto_personas') or '[]'))
        except Exception:
            lst = set()
        lst.add(persona) if opts['enabled'] else lst.discard(persona)
        _set_setting('threads_auto_personas', json.dumps(sorted(lst)))
        return jsonify({'ok': True, 'options': opts})
    persona = (request.args.get('persona') or '').strip()
    try:
        opts = json.loads(_get_setting(f'threads_auto_{persona}') or '{}')
    except Exception:
        opts = {}
    return jsonify({'enabled': bool(opts.get('enabled')),
                    'reply_comments': opts.get('reply_comments', True),
                    'reply_mentions': opts.get('reply_mentions', True)})


@app.route('/api/threads/auto-run', methods=['POST'])
@platform_scoped
def api_threads_auto_run():
    data = request.json or {}
    persona = (data.get('persona') or '').strip()
    if not persona:
        return jsonify({'ok': False, 'error': 'persona required'}), 400
    try:
        actions, log, replies = _threads_auto_round(
            persona,
            reply_comments=bool(data.get('reply_comments', True)),
            reply_mentions=bool(data.get('reply_mentions', True)),
            preview=bool(data.get('preview')))
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    return jsonify({'ok': True, 'actions': actions, 'log': log, 'replies': replies})


@app.route('/api/threads/webhook', methods=['GET', 'POST'])
def api_threads_webhook():
    """Meta webhook: GET verifies the subscription challenge; POST receives
    replies/mentions events and runs an engagement round for the account."""
    if request.method == 'GET':
        verify = _get_setting('threads_webhook_verify_token') or 'threads-verify'
        if (request.args.get('hub.mode') == 'subscribe'
                and request.args.get('hub.verify_token') == verify):
            return request.args.get('hub.challenge', ''), 200
        return 'forbidden', 403
    # POST: fire a round for every enabled persona (cheap; seen-set dedupes).
    try:
        personas = json.loads(_get_setting('threads_auto_personas') or '[]')
    except Exception:
        personas = []
    for persona in personas:
        try:
            opts = json.loads(_get_setting(f'threads_auto_{persona}') or '{}')
            _threads_auto_round(persona,
                                reply_comments=opts.get('reply_comments', True),
                                reply_mentions=opts.get('reply_mentions', True))
        except Exception:
            continue
    return jsonify({'ok': True}), 200


# ── Telegram DM bot ───────────────────────────────────────────────────────────
# Each persona connects its own BotFather bot, so this is multi-tenant from the
# start: the webhook URL carries an unguessable path id that identifies which
# persona a message belongs to, and Telegram echoes a per-bot secret header that
# is checked before anything is generated. The bot chats in-persona and funnels
# fans to the creator's own platform (OnlyFans/Fanvue/…) through a tracked link
# so clicks can be counted and followed up on. No PPV here.

TELEGRAM_API = 'https://api.telegram.org'
TG_CTA_AFTER_DEFAULT = 6        # earliest fan message the CTA may appear on
TG_FOLLOWUP_MIN_DEFAULT = 45    # minutes of silence before a nudge
TG_FOLLOWUP_MAX = 2
TG_TYPING_CPS = 14              # characters "typed" per second
TG_READ_CAP = 4.5               # longest pause before she starts typing
TG_TYPE_CAP = 22.0              # longest single typing burst


def _tg_load_bots():
    try:
        return json.loads(_get_setting('telegram_bots') or '{}')
    except Exception:
        return {}


def _tg_save_bots(data):
    _set_setting('telegram_bots', json.dumps(data))


def _tg_platform():
    """The one platform-owned bot every subscriber can share, so a creator never
    has to touch BotFather. Set up once by the operator."""
    try:
        return json.loads(_get_setting('telegram_platform') or '{}')
    except Exception:
        return {}


def _tg_save_platform(data):
    _set_setting('telegram_platform', json.dumps(data))


def _tg_bot(persona):
    """Connection record for a persona. Hosted personas borrow the platform
    bot's token and identity but keep their own code, CTA and fan state."""
    bot = dict(_tg_load_bots().get(persona) or {})
    if bot.get('mode') == 'hosted':
        plat = _tg_platform()
        if not plat.get('bot_token'):
            raise RuntimeError('The platform Telegram bot is not configured yet.')
        bot['bot_token'] = plat['bot_token']
        bot['username'] = plat.get('username', '')
        bot['base_url'] = plat.get('base_url') or bot.get('base_url', '')
    if not bot.get('bot_token'):
        raise RuntimeError(f'No Telegram bot connected for persona "{persona}".')
    return bot


def _tg_persona_for_path(path_id):
    for persona, bot in _tg_load_bots().items():
        if bot.get('path_id') == path_id:
            return persona, bot
    return None, None


def _tg_persona_for_code(code):
    for persona, bot in _tg_load_bots().items():
        if bot.get('code') and bot['code'] == code:
            return persona
    return None


def _tg_routes():
    """chat_id → persona, so a fan on the shared bot keeps talking to the same
    creator after the first deep-linked /start."""
    try:
        return json.loads(_get_setting('telegram_routes') or '{}')
    except Exception:
        return {}


def _tg_save_routes(routes):
    _set_setting('telegram_routes', json.dumps(routes))


def _tg_api(token, method, payload=None):
    url = f'{TELEGRAM_API}/bot{token}/{method}'
    data = json.dumps(payload).encode() if payload else None
    headers = {'Content-Type': 'application/json'} if payload else {}
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method='POST' if payload else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            res = json.loads(r.read() or b'{}')
    except url_error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get('description', str(e))
        except Exception:
            msg = str(e)
        raise RuntimeError(f'Telegram {method} failed: {msg}')
    if not res.get('ok'):
        raise RuntimeError(f'Telegram {method} failed: {res.get("description", res)}')
    return res.get('result', {})


def _tg_send(persona, chat_id, text):
    bot = _tg_bot(persona)
    return _tg_api(bot['bot_token'], 'sendMessage',
                   {'chat_id': chat_id, 'text': text[:4000],
                    'disable_web_page_preview': False})


def _tg_send_photo(persona, chat_id, image_data, caption=''):
    """Send a photo via the Telegram Bot API. image_data is a base64 data URL."""
    import base64 as b64mod
    bot = _tg_bot(persona)
    token = bot['bot_token']
    try:
        header, b64 = image_data.split(',', 1)
        raw = b64mod.b64decode(b64)
    except Exception:
        return None
    ext = 'jpg'
    if 'png' in header:
        ext = 'png'
    url = f'{TELEGRAM_API}/bot{token}/sendPhoto'
    boundary = '----TgMedia' + str(int(time.time()))
    body = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
    )
    if caption:
        body += (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption[:1024]}\r\n'
        )
    body = body.encode()
    body += (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="photo"; filename="photo.{ext}"\r\n'
        f'Content-Type: image/{ext}\r\n\r\n'
    ).encode()
    body += raw
    body += f'\r\n--{boundary}--\r\n'.encode()
    req = urllib.request.Request(url, data=body, method='POST',
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b'{}')
    except Exception:
        return None


def _tg_typing(persona, chat_id, seconds):
    """Hold the "typing…" indicator for a while. Telegram clears it after ~5s,
    so it has to be re-sent to span a longer burst."""
    bot = _tg_bot(persona)
    end = time.time() + seconds
    while True:
        try:
            _tg_api(bot['bot_token'], 'sendChatAction',
                    {'chat_id': chat_id, 'action': 'typing'})
        except Exception:
            return
        left = end - time.time()
        if left <= 0:
            return
        time.sleep(min(4.0, left))


def _tg_bursts(text):
    """Split a reply the way a person types it: the first sentence, then the
    rest as a second message. Never breaks inside a sentence, so a trailing-off
    "a… specific preference" stays in one piece."""
    parts = _sentences(text)
    if len(parts) < 2:
        return [(text or '').strip()]
    return [parts[0], ' '.join(parts[1:]).strip()]


def _tg_send_human(persona, chat_id, text, incoming='', photo_data=None):
    """Send a reply the way a person would: a pause to read, then visible typing
    scaled to the length of what she's writing, split across a burst or two.
    If photo_data (base64 data URL) is provided, sends a photo after the text."""
    cfg = _tg_settings(persona)
    if not cfg['humanize']:
        _tg_send(persona, chat_id, text)
        if photo_data:
            _tg_send_photo(persona, chat_id, photo_data)
        return
    cps = max(2, cfg['typing_speed'] // 2)
    time.sleep(random.uniform(15, 120))
    time.sleep(min(0.8 + len(incoming) / 90.0, TG_READ_CAP) * random.uniform(0.7, 1.3))
    for i, chunk in enumerate(_tg_bursts(text)):
        if not chunk:
            continue
        if i:
            time.sleep(random.uniform(0.6, 1.6))
        dur = min(max(len(chunk) / float(cps), 1.2), TG_TYPE_CAP) * random.uniform(0.85, 1.2)
        _tg_typing(persona, chat_id, dur)
        _tg_send(persona, chat_id, chunk)
    if photo_data:
        time.sleep(random.uniform(0.8, 2.0))
        _tg_typing(persona, chat_id, random.uniform(1.0, 2.5))
        _tg_send_photo(persona, chat_id, photo_data)


TG_TRACE_MAX = 120


def _tg_trace(persona, stage, detail=''):
    """Append one line to the persona's Telegram trace. Kept in the database so
    it survives a redeploy and can be read from the browser — Cloud Run's logs
    are awkward to reach when a bot has simply gone quiet."""
    key = f'tg_trace_{persona or "platform"}'
    try:
        rows = json.loads(_get_setting(key) or '[]')
        if not isinstance(rows, list):
            rows = []
    except Exception:
        rows = []
    rows.append({'at': int(time.time()), 'stage': stage, 'detail': str(detail)[:500]})
    _set_setting(key, json.dumps(rows[-TG_TRACE_MAX:]))
    logger.info('TG[%s] %s: %s', persona, stage, str(detail)[:200])


def _tg_clean_only_fans(value):
    """Normalise the fan picker's value: a list of chat ids and/or @usernames.
    Empty means the bot talks to everyone, which is the default."""
    if isinstance(value, str):
        value = value.split(',')
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        v = str(v).strip().lstrip('@')
        if v and v not in out:
            out.append(v)
    return out[:200]


def _tg_fan_matches(entries, chat_id, name=''):
    """True when a chat id / @username list names this fan."""
    wanted = {str(e).strip().lstrip('@').lower() for e in (entries or []) if str(e).strip()}
    return str(chat_id) in wanted or (name or '').strip().lstrip('@').lower() in wanted


def _tg_fan_allowed(cfg, chat_id, name=''):
    """True when this fan is one the bot is allowed to talk to. An exclude entry
    always wins, so a blocked fan stays blocked even inside the only-list."""
    if _tg_fan_matches(cfg.get('exclude_fans'), chat_id, name):
        return False
    only = cfg.get('only_fans') or []
    return not only or _tg_fan_matches(only, chat_id, name)


def _tg_settings(persona):
    try:
        s = json.loads(_get_setting(f'telegram_auto_{persona}') or '{}')
    except Exception:
        s = {}
    only = s.get('only_fans')
    return {
        'enabled': bool(s.get('enabled')),
        'cta_after': int(s.get('cta_after') or TG_CTA_AFTER_DEFAULT),
        'followup_min': int(s.get('followup_min') or TG_FOLLOWUP_MIN_DEFAULT),
        'followups': bool(s.get('followups', True)),
        'humanize': bool(s.get('humanize', True)),
        'typing_speed': int(s.get('typing_speed') or TG_TYPING_CPS),
        # Empty list = talk to everyone. Entries are chat ids and/or @usernames.
        'only_fans': [str(x).strip() for x in only if str(x).strip()] if isinstance(only, list) else [],
        'exclude_fans': _tg_clean_only_fans(s.get('exclude_fans')),
    }


def _tg_enabled_list():
    return [p for p, b in _tg_load_bots().items()
            if b.get('bot_token') and _tg_settings(p)['enabled']]


def _tg_fans(persona):
    try:
        return json.loads(_get_setting(f'telegram_fans_{persona}') or '{}')
    except Exception:
        return {}


def _tg_save_fans(persona, fans):
    _set_setting(f'telegram_fans_{persona}', json.dumps(fans))


def _tg_fan_key(chat_id):
    return f'tg:{chat_id}'


def _tg_cta_link(bot, chat_id):
    """Tracked redirect through our own server so clicks are measurable. Keyed on
    the persona's own code, which is unique even when the bot itself is shared."""
    base = (bot.get('base_url') or '').rstrip('/')
    return f'{base}/go/{bot.get("code") or bot.get("path_id")}/{chat_id}'


def _tg_share_link(bot):
    """The link a creator puts in their bio. The payload routes the fan to them."""
    if not (bot.get('username') and bot.get('code')):
        return ''
    return f'https://t.me/{bot["username"]}?start={bot["code"]}'


def _tg_history(persona, chat_id, limit=30):
    return [{'role': 'model' if d == 'out' else 'user', 'content': t}
            for d, t in _fanvue_saved_history(persona, _tg_fan_key(chat_id), limit=limit)]


def _tg_generate(persona, chat_id, instruction):
    if client is None:
        return local_fallback_reply(instruction)
    history = _tg_history(persona, chat_id)
    instruction = _fan_memory_block(_fan_memory(persona, _tg_fan_key(chat_id)), persona) + instruction
    lim = persona_length_limits(persona)
    return _strip_placeholders(
        _fv_trim(_persona_text(persona, instruction, history=history,
                               max_tokens=lim['tokens'], temperature=0.9),
                 max_sentences=lim['sentences'], hard_cap=lim['cap']))


def _tg_media_catalog(persona):
    """Describe the persona's outfits for prompt injection, so she picks a look
    that fits the moment rather than a loose bag of tags."""
    try:
        from db import SessionLocal, list_persona_media
        s = SessionLocal()
        try:
            rows = list_persona_media(s, persona)
        finally:
            s.close()
    except Exception as exc:
        logger.warning('_tg_media_catalog failed for %s: %s', persona, exc)
        return '', [], []
    if not rows:
        logger.info('No media rows for persona %s', persona)
        return '', [], []
    logger.info('Found %d media rows for persona %s', len(rows), persona)
    outfits = _outfits(persona)
    used = set()
    for r in rows:
        try:
            used.add(int(r.outfit))
        except (TypeError, ValueError):
            continue
    lines = []
    for o in outfits:
        if o['n'] not in used:
            continue
        bits = [b for b in (o['clothing'], o['location'], o['lighting']) if b]
        label = o['name'] or f'Outfit {o["n"]}'
        lines.append(f'Outfit {o["n"]} ({label})' + (f' — {", ".join(bits)}' if bits else ''))
    if not lines:
        return '', [], []
    purposes = sorted(set(r.purpose for r in rows if r.purpose))
    catalog = 'Your photo sets: ' + '; '.join(lines) + '.'
    if purposes:
        catalog += f' Purposes available: {", ".join(purposes)}.'
    return catalog, rows, outfits


def _tg_parse_photo_tag(reply):
    """Extract [SEND_PHOTO:key=value,...] from the reply text, returning
    (clean_text, tag_dict). Example: [SEND_PHOTO:purpose=tease,lighting=night]"""
    m = re.search(r'\[SEND_PHOTO:([^\]]+)\]', reply)
    if not m:
        return reply, {}
    tags = {}
    for pair in m.group(1).split(','):
        if '=' in pair:
            k, v = pair.split('=', 1)
            tags[k.strip().lower()] = v.strip()
    clean = reply[:m.start()].rstrip() + reply[m.end():]
    return clean.strip(), tags


def _tg_handle_update(persona, update):
    """Generate and send one in-persona reply. Runs off the webhook thread."""
    msg = update.get('message') or update.get('edited_message') or {}
    chat = msg.get('chat') or {}
    chat_id = chat.get('id')
    text = (msg.get('text') or '').strip()
    if not chat_id or not text or chat.get('type') != 'private':
        return
    frm = msg.get('from') or {}
    who = frm.get('username') or frm.get('first_name') or str(chat_id)

    bot = _tg_bot(persona)
    cfg = _tg_settings(persona)
    if not cfg['enabled']:
        _tg_trace(persona, 'skipped',
                  f'{who} ({chat_id}) — bot is switched off under "How she replies"')
        return
    if not _tg_fan_allowed(cfg, chat_id, who):
        _tg_trace(persona, 'skipped',
                  f"{who} ({chat_id}) — not in the {len(cfg['only_fans'])} selected fan(s)")
        return
    fans = _tg_fans(persona)
    fan = fans.get(str(chat_id)) or {}
    fan['name'] = who
    fan['last_in'] = int(time.time())
    if not fan.get('first_in'):
        fan['first_in'] = int(time.time())
    fan['followups'] = 0
    fan['in_count'] = int(fan.get('in_count', 0)) + (0 if text == '/start' else 1)

    _log_x_message(persona, _tg_fan_key(chat_id), who, 'in', text)
    _tg_trace(persona, 'received', f'← {who}: {text}')
    _fan_memory_update(persona, _tg_fan_key(chat_id), text)

    phases = _phases(persona)
    phase_idx = _fan_phase(phases, fan)
    current_phase = phases[phase_idx] if phase_idx < len(phases) else phases[-1]
    photo_rate = current_phase.get('photo_rate', 20)

    cta = _phases_cta(persona)
    cta_url = (cta.get('cta_url') or bot.get('cta_url') or '').strip()
    is_cta_phase = phase_idx == len(phases) - 1
    cta_asked = _cta_asked(text)
    cta_due = _cta_due(persona, text, fan, is_cta_phase, cta_url)

    catalog, media_rows, media_outfits = _tg_media_catalog(persona)
    photo_rule = ''
    if catalog:
        photo_rule = (
            f'\n\nYou have photos you can share. {catalog} '
            'Each outfit is one consistent look — same clothes, same place — so '
            'stay within a single outfit and pick the one that fits where you are '
            'and what you are doing right now. Share photos generously — when a '
            'fan asks to see you, when you mention what you are doing, when you '
            'want to flirt or tease, or just to keep things visual and fun. Add '
            'the tag [SEND_PHOTO:outfit=N,purpose=X] at the very end of your '
            'message. Never mention the tag to the fan.')

    ask_rule = question_rule_for(load_persona_config(persona),
                                 _tg_history(persona, chat_id))
    if text == '/start':
        instruction = (
            f'A new fan just opened a chat with you on Telegram (they go by "{who}"). '
            'Write ONE short, warm, in-character opener that introduces you without '
            'sounding scripted. ' + ask_rule + photo_rule)
    elif cta_asked and cta_due:
        instruction = (
            f'Reply in-character to this fan on Telegram: "{text}". They are asking '
            'where else to find you — answer them directly and warmly, say yes, that '
            'is where you post the rest. Do NOT deflect, do NOT answer with a question, '
            'and do NOT paste a link yourself; a link is appended after your message. '
            'Keep it to one or two short sentences. ' + photo_rule)
    elif cta_due:
        instruction = (
            f'Reply in-character to this fan on Telegram: "{text}". Answer what they '
            'actually said first, then tease — in one natural sentence — that you post '
            'more somewhere more private. Do NOT paste a link or a URL, do not '
            'hard-sell, and do not name the site; a link is appended after your '
            'message. ' + ask_rule + photo_rule)
    else:
        instruction = (
            f'Reply in-character to this fan on Telegram: "{text}". Warm and engaging, '
            'react to what they just said before anything else, reference what they '
            'have told you before, and let interest build slowly — no selling, no '
            'hinting at paid content yet. ' + ask_rule + photo_rule)

    reply = _tg_generate(persona, chat_id, instruction)
    if not reply:
        _tg_trace(persona, 'error', f'{who}: the model returned nothing — no reply sent')
        return

    photo_data = None
    picked_media_id = None
    reply, photo_tags = _tg_parse_photo_tag(reply)
    reply = strip_ppv_marker(reply)
    sent_ids = _fan_sent_photos(persona, chat_id) if media_rows else set()
    locked_outfit, _ = _fan_outfit_lock(persona, chat_id)
    roll = random.randint(1, 100) if media_rows else 0
    roll_hit = media_rows and roll <= photo_rate
    logger.info('Photo decision: %d media, rate=%d, roll=%d, hit=%s, tags=%s, sent=%d, outfit_lock=%s',
                len(media_rows), photo_rate, roll, roll_hit, bool(photo_tags), len(sent_ids), locked_outfit)
    if photo_tags and media_rows:
        safe = {k: v for k, v in photo_tags.items()
                if k in ('purpose', 'lighting', 'location', 'outfit')}
        if locked_outfit is not None:
            safe['outfit'] = str(locked_outfit)
        picked = _pick_media(media_rows, outfits=media_outfits, **safe)
        if picked and picked.id not in sent_ids:
            photo_data = picked.image_data
            picked_media_id = picked.id
    if not photo_data and roll_hit:
        picked = _pick_phase_photo(media_rows, media_outfits, sent_ids, locked_outfit=locked_outfit)
        if picked:
            photo_data = picked.image_data
            picked_media_id = picked.id

    if cta_due:
        label = (cta.get('cta_label') or bot.get('cta_label') or 'come see').strip()
        link = _tg_cta_link(bot, chat_id)
        reply = f'{reply}\n\n{label} → {link}'
        fan['cta_sent'] = int(time.time())
        fan['cta_count'] = int(fan.get('cta_count', 0)) + 1
        logger.info('CTA SENT [%s] fan=%s trigger=%s phase=%d count=%d link=%s',
                    persona, chat_id, 'asked' if cta_asked else 'phase',
                    phase_idx, fan['cta_count'], link)
    elif cta_asked and not cta_url:
        logger.warning('CTA asked but no cta_url configured [%s] fan=%s', persona, chat_id)

    try:
        _tg_send_human(persona, chat_id, reply, incoming=text, photo_data=photo_data)
        _tg_trace(persona, 'sent', f'→ {who}: {reply}')
    except Exception as e:
        _tg_trace(persona, 'error', f'send to {who} failed: {str(e)[:200]}')
        raise
    if picked_media_id:
        _fan_record_sent_photo(persona, chat_id, picked_media_id)
        outfit_num = _safe_outfit_num(
            next((r for r in media_rows if r.id == picked_media_id), None))
        if outfit_num is not None:
            _fan_set_outfit_lock(persona, chat_id, outfit_num)
    _log_x_message(persona, _tg_fan_key(chat_id), who, 'out', reply)
    fan['last_out'] = int(time.time())
    fans[str(chat_id)] = fan
    _tg_save_fans(persona, fans)


def _tg_followup_round(persona):
    """Re-engage fans who went quiet, and nudge once on an unclicked CTA.
    Capped at TG_FOLLOWUP_MAX per fan so it never turns into spam."""
    cfg = _tg_settings(persona)
    if not cfg['followups']:
        return 0
    bot = _tg_load_bots().get(persona) or {}
    fans = _tg_fans(persona)
    now = int(time.time())
    sent = 0
    for chat_id, fan in list(fans.items()):
        if not _tg_fan_allowed(cfg, chat_id, fan.get('name', '')):
            continue
        last = max(int(fan.get('last_in') or 0), int(fan.get('last_out') or 0))
        if not last or fan.get('last_in', 0) > fan.get('last_out', 0):
            continue  # they spoke last — the reply path handles it
        n = int(fan.get('followups', 0))
        if n >= TG_FOLLOWUP_MAX:
            continue
        if (now - last) / 60.0 < cfg['followup_min'] * (n + 1):
            continue
        cta_pending = bool(fan.get('cta_sent')) and not fan.get('cta_clicked')
        if cta_pending:
            instruction = (
                'This fan went quiet after you sent them your link. Write ONE short, '
                'light, in-character nudge — curious whether they had a look, playful, '
                'zero pressure, no link. One or two sentences.')
        else:
            instruction = (
                'This fan went quiet on Telegram. Write ONE short, in-character message '
                'that reopens the conversation — reference something they mentioned '
                'before if you can, and ask them something easy to answer.')
        try:
            text = _tg_generate(persona, chat_id, instruction)
            if not text:
                continue
            _tg_send(persona, chat_id, text)
            _log_x_message(persona, _tg_fan_key(chat_id), fan.get('name', ''), 'out', text)
            fan['followups'] = n + 1
            fan['last_out'] = now
            fans[chat_id] = fan
            sent += 1
        except Exception:
            continue
    if sent:
        _tg_save_fans(persona, fans)
    return sent


def _tg_handle_platform_update(update):
    """Shared-bot dispatch. A fan arriving through a creator's deep link sends
    `/start <code>`; that binds the chat to the creator for every later message."""
    msg = update.get('message') or {}
    chat = msg.get('chat') or {}
    chat_id = str(chat.get('id') or '')
    text = (msg.get('text') or '').strip()
    if not chat_id or not text:
        return
    plat = _tg_platform()
    routes = _tg_routes()
    persona = routes.get(chat_id)

    parts = text.split(None, 1)
    if parts and parts[0] in ('/start', '/connect'):
        payload = (parts[1] if len(parts) > 1 else '').strip()
        if payload:
            matched = _tg_persona_for_code(payload)
            if not matched:
                _tg_api(plat['bot_token'], 'sendMessage',
                        {'chat_id': chat_id, 'text': "That link doesn't look right — "
                                                     'ask for a fresh one.'})
                return
            persona = matched
            routes[chat_id] = persona
            _tg_save_routes(routes)
        # Let the opener branch in _tg_handle_update see a bare /start.
        msg['text'] = '/start'
    if not persona:
        _tg_trace(None, 'skipped',
                  f'chat {chat_id} is not bound to a creator — asked them for their link')
        _tg_api(plat['bot_token'], 'sendMessage',
                {'chat_id': chat_id, 'text': 'Open this chat from the link you were '
                                             'given so I know who you came for.'})
        return
    _tg_trace(None, 'routed', f'chat {chat_id} → {persona}')
    _tg_handle_update(persona, update)


@app.route('/api/telegram/webhook/<path_id>', methods=['POST'])
def api_telegram_webhook(path_id):
    """Telegram pushes every update here. Ack within milliseconds — Gemini runs
    on a worker thread, because a slow 200 makes Telegram redeliver the update."""
    plat = _tg_platform()
    if plat.get('path_id') and plat['path_id'] == path_id:
        persona, secret = None, plat.get('secret', '')
    else:
        persona, bot = _tg_persona_for_path(path_id)
        if not persona:
            return '', 404
        secret = bot.get('secret', '')
    if request.headers.get('X-Telegram-Bot-Api-Secret-Token', '') != secret:
        return '', 401
    update = request.get_json(silent=True) or {}
    uid = update.get('update_id')
    seen_key = f'telegram_seen_{persona or "platform"}'
    try:
        seen = json.loads(_get_setting(seen_key) or '[]')
    except Exception:
        seen = []
    if uid in seen:
        return '', 200
    _set_setting(seen_key, json.dumps((seen + [uid])[-300:]))

    def work():
        with app.app_context():
            msg = update.get('message') or update.get('edited_message') or {}
            _tg_trace(persona, 'webhook',
                      f"update {uid} from {(msg.get('from') or {}).get('username') or (msg.get('chat') or {}).get('id')}: "
                      f"{(msg.get('text') or '')[:80]}")
            try:
                if persona:
                    _tg_handle_update(persona, update)
                else:
                    _tg_handle_platform_update(update)
            except Exception as e:
                _tg_trace(persona, 'error', f'update failed: {str(e)[:200]}')
                error_logger.error('telegram update failed', exc_info=True)

    threading.Thread(target=work, daemon=True).start()
    return '', 200


@app.route('/go/<ref>/<chat_id>')
def telegram_cta_click(ref, chat_id):
    """Tracked CTA redirect — records the click, then forwards to the creator's page."""
    persona = _tg_persona_for_code(ref)
    bot = (_tg_load_bots().get(persona) or {}) if persona else {}
    if not persona:
        persona, bot = _tg_persona_for_path(ref)
    if not persona or not ((bot or {}).get('cta_url') or '').strip():
        return redirect('/')
    fans = _tg_fans(persona)
    fan = fans.get(str(chat_id))
    if fan is not None and not fan.get('cta_clicked'):
        fan['cta_clicked'] = int(time.time())
        fans[str(chat_id)] = fan
        _tg_save_fans(persona, fans)
    return redirect(bot['cta_url'], code=302)


@app.route('/api/telegram/connect', methods=['POST'])
@platform_scoped
def api_telegram_connect():
    data = request.json or {}
    persona = (data.get('persona') or '').strip()
    token = (data.get('bot_token') or '').strip()
    if not persona or not token:
        return jsonify({'ok': False, 'error': 'persona and bot_token are required'}), 400
    base = (data.get('base_url') or request.url_root).strip().rstrip('/')
    if base.startswith('http://') and 'localhost' not in base and '127.0.0.1' not in base:
        base = 'https://' + base[len('http://'):]
    try:
        me = _tg_api(token, 'getMe')
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400

    bots = _tg_load_bots()
    existing = bots.get(persona) or {}
    bot = {
        'mode': 'own',
        'bot_token': token,
        'bot_id': me.get('id'),
        'username': me.get('username', ''),
        'code': existing.get('code') or secrets.token_urlsafe(9),
        'path_id': existing.get('path_id') or secrets.token_urlsafe(24),
        'secret': existing.get('secret') or secrets.token_urlsafe(24),
        'base_url': base,
        'cta_url': (data.get('cta_url') or existing.get('cta_url') or '').strip(),
        'cta_label': (data.get('cta_label') or existing.get('cta_label') or '').strip(),
        'connected_at': int(time.time()),
    }
    if base.startswith('https://'):
        try:
            _tg_api(token, 'setWebhook', {
                'url': f'{base}/api/telegram/webhook/{bot["path_id"]}',
                'secret_token': bot['secret'],
                'allowed_updates': ['message'],
                'drop_pending_updates': True,
            })
            bot['webhook_set'] = True
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 400
    else:
        bot['webhook_set'] = False
    bots[persona] = bot
    _tg_save_bots(bots)
    return jsonify({'ok': True, 'username': bot['username'],
                    'webhook_set': bot['webhook_set'],
                    'webhook_url': f'{base}/api/telegram/webhook/{bot["path_id"]}'})


@app.route('/api/telegram/platform', methods=['GET', 'POST'])
@operator_only
def api_telegram_platform():
    """Operator-side: register the single shared bot once. Every subscriber then
    connects with one click — no BotFather, no token of their own."""
    plat = _tg_platform()
    if request.method == 'GET':
        return jsonify({'configured': bool(plat.get('bot_token')),
                        'username': plat.get('username', ''),
                        'webhook_set': bool(plat.get('webhook_set')),
                        'base_url': plat.get('base_url', '')})
    data = request.json or {}
    token = (data.get('bot_token') or '').strip() or plat.get('bot_token', '')
    if not token:
        return jsonify({'ok': False, 'error': 'bot_token is required'}), 400
    base = (data.get('base_url') or plat.get('base_url') or request.url_root).strip().rstrip('/')
    if base.startswith('http://') and 'localhost' not in base and '127.0.0.1' not in base:
        base = 'https://' + base[len('http://'):]
    try:
        me = _tg_api(token, 'getMe')
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    try:
        return _tg_platform_save(plat, token, base, me)
    except Exception as e:
        logging.exception('telegram platform save failed')
        return jsonify({'ok': False,
                        'error': f'{e.__class__.__name__}: {str(e)[:300]}'}), 500


def _tg_platform_save(plat, token, base, me):
    plat = {
        'bot_token': token,
        'bot_id': me.get('id'),
        'username': me.get('username', ''),
        'path_id': plat.get('path_id') or secrets.token_urlsafe(24),
        'secret': plat.get('secret') or secrets.token_urlsafe(24),
        'base_url': base,
        'webhook_set': False,
    }
    if base.startswith('https://'):
        try:
            _tg_api(token, 'setWebhook', {
                'url': f'{base}/api/telegram/webhook/{plat["path_id"]}',
                'secret_token': plat['secret'],
                'allowed_updates': ['message'],
                'drop_pending_updates': True,
            })
            plat['webhook_set'] = True
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 400
    _tg_save_platform(plat)
    if not _tg_platform().get('bot_token'):
        return jsonify({'ok': False, 'error': 'Saved nothing — the settings database '
                                              'is not writable on this deployment, so '
                                              'the bot cannot stay connected.'}), 500
    return jsonify({'ok': True, 'username': plat['username'],
                    'webhook_set': plat['webhook_set']})


@app.route('/api/telegram/diag')
@operator_only
def api_telegram_diag():
    """Why is this deployment failing? Reports whether settings actually persist."""
    probe = f'diag-{int(time.time())}'
    _set_setting('telegram_diag', probe)
    plat = _tg_platform()
    wh_info = None
    if plat.get('bot_token'):
        try:
            wh_info = _tg_api(plat['bot_token'], 'getWebhookInfo', {})
        except Exception as e:
            wh_info = {'error': str(e)[:200]}
    out = {'settings_writable': _get_setting('telegram_diag') == probe,
           'platform_configured': bool(plat.get('bot_token')),
           'platform_username': plat.get('username', ''),
           'webhook_url': (wh_info or {}).get('url', ''),
           'webhook_pending': (wh_info or {}).get('pending_update_count', 0),
           'webhook_last_error': (wh_info or {}).get('last_error_message', ''),
           'personas_connected': len(_tg_load_bots()),
           'last_db_error': _last_x_log_error[0]}
    try:
        from db import engine
        out['db_url'] = str(engine.url).split('@')[-1]
    except Exception as e:
        out['db_url'] = f'unavailable: {str(e)[:120]}'
    return jsonify(out)


@app.route('/api/telegram/hosted', methods=['POST'])
@platform_scoped
def api_telegram_hosted():
    """Subscriber-side one-click connect: mint this persona's code on the shared
    bot and hand back the link they put in their bio."""
    data = request.json or {}
    persona = (data.get('persona') or '').strip()
    if not persona:
        return jsonify({'ok': False, 'error': 'persona required'}), 400
    try:
        plat = _tg_platform()
        if not plat.get('bot_token'):
            return jsonify({'ok': False, 'error': 'The platform Telegram bot is not '
                                                  'configured yet — set it up under '
                                                  '"Platform bot" below.'}), 400
        bots = _tg_load_bots()
        existing = bots.get(persona) or {}
        bot = dict(existing)
        bot.update({
            'mode': 'hosted',
            'code': existing.get('code') or secrets.token_urlsafe(9),
            'base_url': plat.get('base_url', ''),
            'connected_at': int(time.time()),
        })
        # Carry the CTA through: connecting used to drop whatever was typed in
        # the funnel fields, which silently left the CTA disabled.
        for key in ('cta_url', 'cta_label'):
            if data.get(key) is not None:
                bot[key] = (data.get(key) or '').strip() or bot.get(key, '')
        bot.pop('bot_token', None)
        bot.pop('path_id', None)
        bots[persona] = bot
        _tg_save_bots(bots)
        # _set_setting swallows write errors, so confirm it actually landed —
        # otherwise the UI would report success on a read-only database.
        if not (_tg_load_bots().get(persona) or {}).get('code'):
            return jsonify({'ok': False, 'error': 'Could not save the connection — '
                                                  'the settings database is not '
                                                  'writable on this deployment.'}), 500
    except Exception as e:
        logging.exception('telegram hosted connect failed')
        return jsonify({'ok': False, 'error': str(e)[:300] or e.__class__.__name__}), 500
    return jsonify({'ok': True, 'code': bot['code'],
                    'username': plat.get('username', ''),
                    'share_link': _tg_share_link({**bot, 'username': plat.get('username', '')})})


@app.route('/api/telegram/status')
def api_telegram_status():
    # No persona argument to scope on, so filter the result instead: a creator
    # sees only their own bots, never another persona's code or share link.
    allowed = owned_slugs()
    plat = _tg_platform()
    out = {}
    for persona, bot in _tg_load_bots().items():
        if allowed is not None and persona not in allowed:
            continue
        hosted = bot.get('mode') == 'hosted'
        username = plat.get('username', '') if hosted else bot.get('username', '')
        out[persona] = {
            'connected': bool(bot.get('bot_token')) or (hosted and bool(plat.get('bot_token'))),
            'mode': bot.get('mode', 'own'),
            'username': username,
            'code': bot.get('code', ''),
            'share_link': _tg_share_link({**bot, 'username': username}),
            'webhook_set': bool(plat.get('webhook_set') if hosted else bot.get('webhook_set')),
            'cta_url': bot.get('cta_url', ''),
            'cta_label': bot.get('cta_label', ''),
        }
    return jsonify(out)


@app.route('/api/telegram/disconnect', methods=['POST'])
@platform_scoped
def api_telegram_disconnect():
    persona = ((request.json or {}).get('persona') or '').strip()
    bots = _tg_load_bots()
    bot = bots.pop(persona, None)
    # Only an own-bot record owns its webhook; the shared bot's must stay put.
    if bot and bot.get('mode') != 'hosted' and bot.get('bot_token'):
        try:
            _tg_api(bot['bot_token'], 'deleteWebhook', {'drop_pending_updates': False})
        except Exception:
            pass
    _tg_save_bots(bots)
    routes = _tg_routes()
    trimmed = {k: v for k, v in routes.items() if v != persona}
    if len(trimmed) != len(routes):
        _tg_save_routes(trimmed)
    return jsonify({'ok': True})


@app.route('/api/telegram/settings', methods=['GET', 'POST'])
@platform_scoped
def api_telegram_settings():
    if request.method == 'GET':
        return jsonify(_tg_settings((request.args.get('persona') or '').strip()))
    data = request.json or {}
    persona = (data.get('persona') or '').strip()
    if not persona:
        return jsonify({'ok': False, 'error': 'persona required'}), 400
    # Merge, never rebuild: several panels save different subsets of these
    # settings, and a missing key must mean "leave it alone" rather than
    # "reset to the default" — otherwise saving the fan picker from the
    # Telegram tab would silently switch the bot off.
    opts = _tg_settings(persona)
    if 'enabled' in data:
        opts['enabled'] = bool(data['enabled'])
    if 'cta_after' in data:
        opts['cta_after'] = max(1, int(data['cta_after'] or TG_CTA_AFTER_DEFAULT))
    if 'followup_min' in data:
        opts['followup_min'] = max(5, int(data['followup_min'] or TG_FOLLOWUP_MIN_DEFAULT))
    if 'followups' in data:
        opts['followups'] = bool(data['followups'])
    if 'humanize' in data:
        opts['humanize'] = bool(data['humanize'])
    if 'typing_speed' in data:
        opts['typing_speed'] = max(4, min(int(data['typing_speed'] or TG_TYPING_CPS), 40))
    if 'only_fans' in data:
        opts['only_fans'] = _tg_clean_only_fans(data['only_fans'])
    if 'exclude_fans' in data:
        opts['exclude_fans'] = _tg_clean_only_fans(data['exclude_fans'])
    _set_setting(f'telegram_auto_{persona}', json.dumps(opts))
    bots = _tg_load_bots()
    if persona in bots:
        if 'cta_url' in data:
            bots[persona]['cta_url'] = (data.get('cta_url') or '').strip()
        if 'cta_label' in data:
            bots[persona]['cta_label'] = (data.get('cta_label') or '').strip()
        _tg_save_bots(bots)
    return jsonify({'ok': True, 'settings': opts})


def _x_behavior(persona):
    raw = _get_setting(f'x_behavior_{persona}')
    defaults = {'enabled': True, 'humanize': True, 'followups': True,
                'followup_min': 45, 'typing_speed': 14}
    if raw:
        try:
            return {**defaults, **json.loads(raw)}
        except Exception:
            pass
    return defaults


@app.route('/api/x/settings', methods=['GET', 'POST'])
@platform_scoped
def api_x_settings():
    if request.method == 'GET':
        persona = (request.args.get('persona') or '').strip()
        return jsonify(_x_behavior(persona))
    data = request.json or {}
    persona = (data.get('persona') or '').strip()
    if not persona:
        return jsonify({'ok': False, 'error': 'persona required'}), 400
    # Merge rather than rebuild, for the same reason as the Telegram endpoint:
    # a key the caller left out must keep its saved value.
    opts = _x_behavior(persona)
    if 'enabled' in data:
        opts['enabled'] = bool(data['enabled'])
    if 'followup_min' in data:
        opts['followup_min'] = max(5, int(data['followup_min'] or 45))
    if 'followups' in data:
        opts['followups'] = bool(data['followups'])
    if 'humanize' in data:
        opts['humanize'] = bool(data['humanize'])
    if 'typing_speed' in data:
        opts['typing_speed'] = max(4, min(int(data['typing_speed'] or 14), 40))
    _set_setting(f'x_behavior_{persona}', json.dumps(opts))
    return jsonify({'ok': True, 'settings': opts})


@app.route('/api/telegram/trace')
@operator_only
def api_telegram_trace():
    """Recent Telegram activity for a persona plus a verdict on the setup, so a
    bot that has gone quiet can be diagnosed without digging through logs."""
    persona = (request.args.get('persona') or '').strip()
    cfg = _tg_settings(persona)
    bot = _tg_load_bots().get(persona) or {}
    try:
        own = json.loads(_get_setting(f'tg_trace_{persona}') or '[]')
    except Exception:
        own = []
    try:
        shared = json.loads(_get_setting('tg_trace_platform') or '[]')
    except Exception:
        shared = []
    rows = sorted(own + shared, key=lambda r: r.get('at', 0))[-TG_TRACE_MAX:]

    problems = []
    # The personal account (MTProto) is a separate transport from the bot: no
    # token, no webhook, a long-lived client instead. Report on it in its own
    # terms, and only fall through to bot checks when a bot is also connected.
    acct = _tgu_accounts().get(persona) or {}
    runner = _tgu_runners.get(persona)
    personal = {'connected': bool(acct.get('session')),
                'running': bool(runner and runner.alive()),
                'username': acct.get('username', ''),
                'error': _tgu_errors.get(persona, '')}
    if personal['connected']:
        if not personal['running']:
            problems.append('The personal Telegram account is signed in but NOT running '
                            '— press Start on the Real account panel. It also stops on a '
                            'redeploy, so it has to be started again after one.')
        if personal['error']:
            problems.append('Personal account error: ' + str(personal['error'])[:200])
        if cfg['only_fans']:
            problems.append(f"Only replying to {len(cfg['only_fans'])} selected fan(s): "
                            + ', '.join(cfg['only_fans'][:8]))
        if cfg['exclude_fans']:
            problems.append(f"Never replying to {len(cfg['exclude_fans'])} excluded fan(s): "
                            + ', '.join(cfg['exclude_fans'][:8]))
        if not rows:
            problems.append('No Telegram activity recorded yet — if you have messaged the '
                            'account since this version deployed, the message never '
                            'reached the running client.')
        return jsonify({'persona': persona, 'transport': 'personal account',
                        'enabled': cfg['enabled'], 'only_fans': cfg['only_fans'],
                        'exclude_fans': cfg['exclude_fans'],
                        'connected': True, 'mode': 'personal account',
                        'username': personal['username'], 'bound_chats': None,
                        'personal': personal,
                        'webhook': {'url': '', 'pending': 0, 'last_error': ''},
                        'problems': problems, 'rows': rows})

    # Sessions are keyed by persona. Signing the account in while a different
    # persona was selected leaves it attached to that slug, and this persona
    # silently has no client at all — worth naming, because the account itself
    # looks perfectly fine in Telegram.
    others = [p for p, a in (_tgu_accounts() or {}).items()
              if p != persona and (a or {}).get('session')]
    if others:
        labels = ', '.join(
            f"{p} (@{(_tgu_accounts()[p] or {}).get('username') or '?'})" for p in others[:5])
        problems.append('This persona has no personal Telegram account, but one is '
                        f'signed in under: {labels}. Messages to that account are '
                        'answered as that persona, not this one.')

    # A hosted connection rides the platform bot and deliberately has no token
    # of its own, so both the connection check and the webhook check have to
    # look at the shared bot instead.
    plat = _tg_platform()
    hosted = bot.get('mode') == 'hosted' or (bool(bot.get('code')) and not bot.get('bot_token'))
    token = bot.get('bot_token') or (plat.get('bot_token') if hosted else '')
    handle = bot.get('username') or (plat.get('username', '') if hosted else '')
    if hosted and not plat.get('bot_token'):
        problems.append('This persona uses the shared bot, but no platform bot is '
                        'set up on this server.')
    elif not token:
        problems.append('No Telegram bot is connected for this persona.')
    if hosted:
        mine = [c for c, p in (_tg_routes() or {}).items() if p == persona]
        if not mine:
            problems.append('No Telegram chat is bound to this persona yet — a fan has '
                            'to open your connect link once so the shared bot knows who '
                            'they came for.')
    if not cfg['enabled']:
        problems.append('Bot active is OFF — turn it on under "How she replies" '
                        'at the top of this page.')
    if cfg['only_fans']:
        problems.append(f"Only replying to {len(cfg['only_fans'])} selected fan(s): "
                        + ', '.join(cfg['only_fans'][:8]))
    if cfg['exclude_fans']:
        problems.append(f"Never replying to {len(cfg['exclude_fans'])} excluded fan(s): "
                        + ', '.join(cfg['exclude_fans'][:8]))
    hook = {}
    if token:
        try:
            hook = _tg_api(token, 'getWebhookInfo') or {}
        except Exception as e:
            problems.append(f'Could not reach Telegram: {str(e)[:160]}')
        if hook:
            if not hook.get('url'):
                problems.append('No webhook is registered — Telegram has nowhere to '
                                'deliver messages. Reconnect the bot.')
            if hook.get('last_error_message'):
                problems.append('Telegram could not deliver to us: '
                                + str(hook['last_error_message'])[:160])
            if hook.get('pending_update_count'):
                problems.append(f"{hook['pending_update_count']} update(s) are queued "
                                'and undelivered.')
    if not rows:
        problems.append('No Telegram activity recorded yet — if you have messaged the '
                        'bot since this version deployed, the update never arrived.')
    return jsonify({'persona': persona, 'enabled': cfg['enabled'],
                    'only_fans': cfg['only_fans'], 'exclude_fans': cfg['exclude_fans'],
                    'connected': bool(token),
                    'mode': 'shared bot' if hosted else 'own bot',
                    'username': handle,
                    'bound_chats': len([c for c, p in (_tg_routes() or {}).items()
                                        if p == persona]) if hosted else None,
                    'webhook': {'url': hook.get('url', ''),
                                'pending': hook.get('pending_update_count', 0),
                                'last_error': hook.get('last_error_message', '')},
                    'problems': problems, 'rows': rows})


@app.route('/api/telegram/trace', methods=['DELETE'])
@operator_only
def api_telegram_trace_clear():
    persona = (request.args.get('persona') or '').strip()
    _set_setting(f'tg_trace_{persona}', '[]')
    _set_setting('tg_trace_platform', '[]')
    return jsonify({'ok': True})


@app.route('/api/telegram/stats')
@operator_only
def api_telegram_stats():
    """Funnel numbers for the connected bot: fans, CTAs sent, clicks."""
    persona = (request.args.get('persona') or '').strip()
    fans = _tg_fans(persona)
    sent = sum(1 for f in fans.values() if f.get('cta_sent'))
    clicked = sum(1 for f in fans.values() if f.get('cta_clicked'))
    rows = []
    for chat_id, f in sorted(fans.items(),
                             key=lambda kv: kv[1].get('last_in', 0), reverse=True)[:50]:
        rows.append({
            'chat_id': chat_id, 'name': f.get('name', ''),
            'messages': f.get('in_count', 0), 'last_in': f.get('last_in', 0),
            'cta_sent': bool(f.get('cta_sent')), 'cta_clicked': bool(f.get('cta_clicked')),
            'followups': f.get('followups', 0),
            'selected': _tg_fan_allowed(_tg_settings(persona), chat_id, f.get('name', '')),
        })
    bot = _tg_load_bots().get(persona) or {}
    cfg = _tg_settings(persona)
    ready = [r for r in rows if not r['cta_sent'] and r['messages'] >= cfg['cta_after']]
    warning = ''
    if not (bot.get('cta_url') or '').strip():
        warning = ('No destination URL set, so the CTA is disabled and no link will '
                   'ever be sent' + (f' — {len(ready)} fan(s) are already past the '
                                     f'{cfg["cta_after"]}-message threshold.' if ready else '.'))
    return jsonify({'fans': len(fans), 'cta_sent': sent, 'cta_clicked': clicked,
                    'click_rate': round(100.0 * clicked / sent, 1) if sent else 0.0,
                    'cta_url': bot.get('cta_url', ''), 'cta_after': cfg['cta_after'],
                    'waiting': len(ready), 'warning': warning, 'rows': rows,
                    'only_fans': cfg['only_fans'], 'exclude_fans': cfg['exclude_fans']})


@app.route('/api/telegram/test', methods=['POST'])
@operator_only
def api_telegram_test():
    data = request.json or {}
    persona = (data.get('persona') or '').strip()
    chat_id = (data.get('chat_id') or '').strip()
    text = (data.get('text') or 'Test message from your bot.').strip()
    if not persona or not chat_id:
        return jsonify({'ok': False, 'error': 'persona and chat_id are required'}), 400
    try:
        _tg_send(persona, chat_id, text)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    return jsonify({'ok': True})


def _tg_poll_round(offset):
    """One getUpdates pass for the platform bot. Returns the next offset.
    Polling needs no public HTTPS URL, so the bot is testable from a laptop or
    from behind a host whose previews sit behind an auth wall."""
    plat = _tg_platform()
    token = plat.get('bot_token')
    if not token:
        return offset
    params = {'timeout': 25, 'allowed_updates': ['message']}
    if offset:
        params['offset'] = offset
    try:
        updates = _tg_api(token, 'getUpdates', params)
    except Exception:
        time.sleep(5)
        return offset
    for u in updates or []:
        offset = int(u.get('update_id', 0)) + 1
        try:
            with app.app_context():
                _tg_handle_platform_update(u)
        except Exception:
            error_logger.error('telegram poll update failed', exc_info=True)
    return offset


def _tg_poll_worker():
    """Long-poll loop, enabled with TELEGRAM_POLL=1. Mutually exclusive with the
    webhook, so the webhook is removed first."""
    plat = _tg_platform()
    if plat.get('bot_token'):
        try:
            _tg_api(plat['bot_token'], 'deleteWebhook', {'drop_pending_updates': False})
        except Exception:
            pass
    offset = 0
    while True:
        offset = _tg_poll_round(offset)


if os.getenv('TELEGRAM_POLL', '0') == '1':
    threading.Thread(target=_tg_poll_worker, daemon=True).start()


# ── Telegram user accounts (MTProto) ─────────────────────────────────────────
# A real account rather than a bot: no "bot" label, no /start required, and it
# can open a conversation itself. Same persona, memory, funnel and typing
# behaviour as the bot path — only the transport differs. Needs a long-lived
# process, so Cloud Run must run with min-instances 1.

_tgu_runners = {}
_tgu_errors = {}


def _tgu_accounts():
    try:
        return json.loads(_get_setting('tguser_accounts') or '{}')
    except Exception:
        return {}


def _tgu_save_accounts(data):
    _set_setting('tguser_accounts', json.dumps(data))


def _tgu_app_creds():
    """api_id/api_hash from my.telegram.org — one pair covers every account."""
    return (_get_setting('tguser_api_id') or os.getenv('TELEGRAM_API_ID', ''),
            _get_setting('tguser_api_hash') or os.getenv('TELEGRAM_API_HASH', ''))


def _tgu_fan_key(chat_id):
    return f'tgu:{chat_id}'


def _tgu_plan(persona, chat_id, name, text):
    """Build the reply for one incoming DM: same persona voice, memory, funnel,
    CTA and photo sending as the bot path. Returns the timing plan the runner
    acts on, including optional photo_data for the runner to send."""
    cfg = _tg_settings(persona)
    # Deliberately no `enabled` check here: that toggle has only ever gated the
    # bot path, and the personal account replies independently of it.
    if not _tg_fan_allowed(cfg, chat_id, name):
        _tg_trace(persona, 'skipped',
                  f"{name} ({chat_id}) — not in the {len(cfg['only_fans'])} selected fan(s): "
                  + ', '.join(cfg['only_fans'][:8]))
        return None
    acct = _tgu_accounts().get(persona) or {}
    fans = _tg_fans(persona)
    key = str(chat_id)
    fan = fans.get(key) or {}
    fan['name'] = name
    fan['last_in'] = int(time.time())
    if not fan.get('first_in'):
        fan['first_in'] = int(time.time())
    fan['followups'] = 0
    fan['in_count'] = int(fan.get('in_count', 0)) + 1

    _log_x_message(persona, _tgu_fan_key(chat_id), name, 'in', text)

    phases = _phases(persona)
    phase_idx = _fan_phase(phases, fan)
    current_phase = phases[phase_idx] if phase_idx < len(phases) else phases[-1]
    photo_rate = current_phase.get('photo_rate', 20)

    cta = _phases_cta(persona)
    cta_url = (cta.get('cta_url') or acct.get('cta_url') or '').strip()
    is_cta_phase = phase_idx == len(phases) - 1
    cta_asked = _cta_asked(text)
    cta_due = _cta_due(persona, text, fan, is_cta_phase, cta_url)

    catalog, media_rows, media_outfits = _tg_media_catalog(persona)
    photo_rule = ''
    if catalog:
        photo_rule = (
            f'\n\nYou have photos you can share. {catalog} '
            'Each outfit is one consistent look — same clothes, same place — so '
            'stay within a single outfit and pick the one that fits where you are '
            'and what you are doing right now. Share photos generously — when a '
            'fan asks to see you, when you mention what you are doing, when you '
            'want to flirt or tease, or just to keep things visual and fun. Add '
            'the tag [SEND_PHOTO:outfit=N,purpose=X] at the very end of your '
            'message. Never mention the tag to the fan.')

    ask_rule = question_rule_for(
        load_persona_config(persona),
        [{'role': 'model' if d == 'out' else 'user', 'content': t}
         for d, t in _fanvue_saved_history(persona, _tgu_fan_key(chat_id), limit=30)])
    # The model has been caught sending a literal "[fan's name]" to a fan.
    no_placeholder = NO_PLACEHOLDER_RULE
    if cta_asked and cta_due:
        instruction = (
            f'Reply in-character to this fan on Telegram: "{text}". They are asking '
            'where else to find you — answer them directly and warmly, say yes, that '
            'is where you post the rest. Do NOT deflect, do NOT answer with a question, '
            'and do NOT paste a link yourself; a link is appended after your message. '
            'Keep it to one or two short sentences. ' + no_placeholder + photo_rule)
    elif cta_due:
        instruction = (
            f'Reply in-character to this fan on Telegram: "{text}". Answer what they '
            'actually said first, then tease — in one natural sentence — that you post '
            'more somewhere more private. Do NOT paste a link or a URL and do not name '
            'the site; a link is appended after your message. '
            + ask_rule + no_placeholder + photo_rule)
    else:
        instruction = (
            f'Reply in-character to this fan on Telegram: "{text}". Warm and engaging, '
            'react to what they just said before anything else, reference what they '
            'have told you before, and let interest build slowly — no selling yet. '
            + ask_rule + no_placeholder + photo_rule)

    history = [{'role': 'model' if d == 'out' else 'user', 'content': t}
               for d, t in _fanvue_saved_history(persona, _tgu_fan_key(chat_id), limit=30)]
    _fan_memory_update(persona, _tgu_fan_key(chat_id), text)
    instruction = _fan_memory_block(_fan_memory(persona, _tgu_fan_key(chat_id)), persona) + instruction
    if client is None:
        reply = local_fallback_reply(text)
    else:
        lim = persona_length_limits(persona)
        reply = _strip_placeholders(
            _fv_trim(_persona_text(persona, instruction, history=history,
                                   max_tokens=lim['tokens'], temperature=0.9),
                     max_sentences=lim['sentences'], hard_cap=lim['cap']))
    if not reply:
        return None

    photo_data = None
    picked_media_id = None
    reply, photo_tags = _tg_parse_photo_tag(reply)
    reply = strip_ppv_marker(reply)
    sent_ids = _fan_sent_photos(persona, chat_id) if media_rows else set()
    locked_outfit, _ = _fan_outfit_lock(persona, chat_id)
    roll = random.randint(1, 100) if media_rows else 0
    roll_hit = media_rows and roll <= photo_rate
    logger.info('MTProto photo decision: %d media, rate=%d, roll=%d, hit=%s, tags=%s, outfit_lock=%s',
                len(media_rows), photo_rate, roll, roll_hit, bool(photo_tags), locked_outfit)
    if photo_tags and media_rows:
        safe = {k: v for k, v in photo_tags.items()
                if k in ('purpose', 'lighting', 'location', 'outfit')}
        if locked_outfit is not None:
            safe['outfit'] = str(locked_outfit)
        picked = _pick_media(media_rows, outfits=media_outfits, **safe)
        if picked and picked.id not in sent_ids:
            photo_data = picked.image_data
            picked_media_id = picked.id
    if not photo_data and roll_hit:
        picked = _pick_phase_photo(media_rows, media_outfits, sent_ids, locked_outfit=locked_outfit)
        if picked:
            photo_data = picked.image_data
            picked_media_id = picked.id

    chunks = _tg_bursts(reply) if cfg['humanize'] else [reply]
    if cta_due:
        label = (cta.get('cta_label') or acct.get('cta_label') or 'come see').strip()
        link = cta_url
        chunks[-1] = f'{chunks[-1]}\n\n{label} → {link}'
        fan['cta_sent'] = int(time.time())
        fan['cta_count'] = int(fan.get('cta_count', 0)) + 1
        logger.info('CTA SENT [%s] fan=%s trigger=%s phase=%d count=%d link=%s',
                    persona, chat_id, 'asked' if cta_asked else 'phase',
                    phase_idx, fan['cta_count'], link)
    elif cta_asked and not cta_url:
        logger.warning('CTA asked but no cta_url configured [%s] fan=%s', persona, chat_id)

    if picked_media_id:
        _fan_record_sent_photo(persona, chat_id, picked_media_id)
        outfit_num = _safe_outfit_num(
            next((r for r in media_rows if r.id == picked_media_id), None))
        if outfit_num is not None:
            _fan_set_outfit_lock(persona, chat_id, outfit_num)

    fans[key] = fan
    _tg_save_fans(persona, fans)
    initial_delay = random.uniform(15, 120) if cfg['humanize'] else 0
    read = min(0.8 + len(text) / 90.0, TG_READ_CAP) if cfg['humanize'] else 0
    cps = max(2, int(cfg['typing_speed'] // 2)) if cfg['humanize'] else 999
    return {'read': read, 'cps': cps, 'initial_delay': initial_delay,
            'chunks': chunks, 'photo_data': photo_data}


def _tgu_on_sent(persona, chat_id, name, text):
    _log_x_message(persona, _tgu_fan_key(chat_id), name, 'out', text)
    fans = _tg_fans(persona)
    fan = fans.get(str(chat_id)) or {}
    fan['last_out'] = int(time.time())
    fans[str(chat_id)] = fan
    _tg_save_fans(persona, fans)


def _tgu_import():
    """Import the MTProto layer, turning a missing dependency into a clear
    message rather than an opaque 500."""
    try:
        import tg_user
        return tg_user
    except Exception as e:
        raise RuntimeError(
            f'Telegram user-account support is unavailable ({e.__class__.__name__}: '
            f'{str(e)[:120]}). Install it with: pip install telethon') from e


def _tgu_start(persona):
    """Bring one persona's account online (idempotent)."""
    AccountRunner = _tgu_import().AccountRunner
    acct = _tgu_accounts().get(persona) or {}
    api_id, api_hash = _tgu_app_creds()
    if not (acct.get('session') and api_id and api_hash):
        return False
    r = _tgu_runners.get(persona)
    if r and r.alive():
        return True

    def plan(chat_id, name, text):
        with app.app_context():
            try:
                return _tgu_plan(persona, chat_id, name, text)
            except Exception as e:
                _tgu_errors[persona] = str(e)[:300]
                # Without this the failure only lived in memory, and the log
                # showed a reply that simply never arrived.
                try:
                    _tg_trace(persona, 'error',
                              f'building the reply for {name} failed: {str(e)[:200]}')
                except Exception:
                    pass
                logger.exception('tgu plan failed for %s', persona)
                return None

    def sent(p, chat_id, name, text):
        with app.app_context():
            try:
                _tgu_on_sent(p, chat_id, name, text)
            except Exception:
                pass

    def err(p, msg):
        _tgu_errors[p] = msg
        with app.app_context():
            try:
                _tg_trace(p, 'error', msg)
            except Exception:
                pass

    def trace(p, stage, detail):
        with app.app_context():
            try:
                _tg_trace(p, stage, detail)
            except Exception:
                pass

    runner = AccountRunner(persona, api_id, api_hash, acct['session'],
                           plan, on_sent=sent, on_error=err, on_trace=trace)
    _tgu_runners[persona] = runner
    _tgu_errors.pop(persona, None)
    runner.start()
    return True


def _tgu_stop(persona):
    r = _tgu_runners.pop(persona, None)
    if r:
        r.stop()


@app.route('/api/tguser/config', methods=['GET', 'POST'])
@operator_only
def api_tguser_config():
    api_id, api_hash = _tgu_app_creds()
    if request.method == 'GET':
        return jsonify({'api_id': api_id, 'has_hash': bool(api_hash)})
    data = request.json or {}
    if data.get('api_id'):
        _set_setting('tguser_api_id', str(data['api_id']).strip())
    if data.get('api_hash'):
        _set_setting('tguser_api_hash', str(data['api_hash']).strip())
    return jsonify({'ok': True})


@app.route('/api/tguser/send-code', methods=['POST'])
@platform_scoped
def api_tguser_send_code():
    data = request.json or {}
    persona = (data.get('persona') or '').strip()
    phone = (data.get('phone') or '').strip()
    api_id, api_hash = _tgu_app_creds()
    if not persona or not phone:
        return jsonify({'ok': False, 'error': 'persona and phone are required'}), 400
    if not (api_id and api_hash):
        return jsonify({'ok': False, 'error': 'Set the Telegram api_id and api_hash first.'}), 400
    try:
        session_str, code_hash = _tgu_import().send_code(api_id, api_hash, phone)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'{e.__class__.__name__}: {str(e)[:250]}'}), 400
    _set_setting(f'tguser_login_{persona}',
                 json.dumps({'session': session_str, 'hash': code_hash, 'phone': phone}))
    return jsonify({'ok': True, 'sent_to': phone})


@app.route('/api/tguser/sign-in', methods=['POST'])
@platform_scoped
def api_tguser_sign_in():
    data = request.json or {}
    persona = (data.get('persona') or '').strip()
    code = (data.get('code') or '').strip()
    password = (data.get('password') or '').strip() or None
    api_id, api_hash = _tgu_app_creds()
    try:
        pending = json.loads(_get_setting(f'tguser_login_{persona}') or '{}')
    except Exception:
        pending = {}
    if not pending.get('session'):
        return jsonify({'ok': False, 'error': 'Request a code first.'}), 400
    try:
        session_str, me, needs_pw = _tgu_import().sign_in(
            api_id, api_hash, pending['session'], pending['phone'],
            code, pending['hash'], password=password)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'{e.__class__.__name__}: {str(e)[:250]}'}), 400
    if needs_pw:
        pending['session'] = session_str
        _set_setting(f'tguser_login_{persona}', json.dumps(pending))
        return jsonify({'ok': True, 'needs_password': True})

    accounts = _tgu_accounts()
    existing = accounts.get(persona) or {}
    accounts[persona] = {
        'session': session_str,
        'code': existing.get('code') or secrets.token_urlsafe(9),
        'base_url': (data.get('base_url') or existing.get('base_url')
                     or request.url_root).strip().rstrip('/'),
        'cta_url': existing.get('cta_url', ''),
        'cta_label': existing.get('cta_label', ''),
        'connected_at': int(time.time()),
        **me,
    }
    _tgu_save_accounts(accounts)
    _set_setting(f'tguser_login_{persona}', '')
    _tgu_start(persona)
    return jsonify({'ok': True, 'account': {k: me[k] for k in ('username', 'first_name', 'phone')}})


@app.route('/api/tguser/status')
def api_tguser_status():
    # Filtered rather than scoped, for the same reason as the bot status route:
    # there is no persona argument to key on, and the phone number here is far
    # more sensitive than a share link.
    allowed = owned_slugs()
    out = {}
    for persona, a in _tgu_accounts().items():
        if allowed is not None and persona not in allowed:
            continue
        r = _tgu_runners.get(persona)
        out[persona] = {
            'connected': bool(a.get('session')),
            'running': bool(r and r.alive()),
            'username': a.get('username', ''),
            'first_name': a.get('first_name', ''),
            'phone': a.get('phone', ''),
            'cta_url': a.get('cta_url', ''),
            'cta_label': a.get('cta_label', ''),
            'error': _tgu_errors.get(persona, ''),
        }
    return jsonify(out)


@app.route('/api/tguser/control', methods=['POST'])
@platform_scoped
def api_tguser_control():
    data = request.json or {}
    persona = (data.get('persona') or '').strip()
    action = (data.get('action') or '').strip()
    if action == 'stop':
        _tgu_stop(persona)
        return jsonify({'ok': True, 'running': False})
    if action == 'disconnect':
        _tgu_stop(persona)
        accounts = _tgu_accounts()
        accounts.pop(persona, None)
        _tgu_save_accounts(accounts)
        return jsonify({'ok': True})
    if action == 'cta':
        accounts = _tgu_accounts()
        if persona in accounts:
            accounts[persona]['cta_url'] = (data.get('cta_url') or '').strip()
            accounts[persona]['cta_label'] = (data.get('cta_label') or '').strip()
            if data.get('base_url'):
                accounts[persona]['base_url'] = data['base_url'].strip().rstrip('/')
            _tgu_save_accounts(accounts)
        return jsonify({'ok': True})
    try:
        started = _tgu_start(persona)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:300]}), 400
    r = _tgu_runners.get(persona)
    return jsonify({'ok': started, 'running': bool(r and r.alive()),
                    'error': _tgu_errors.get(persona, ''),
                    'hint': ('' if started else
                             'No stored session or api_id/api_hash — sign in first.')})


@app.route('/api/tguser/send', methods=['POST'])
@platform_scoped
def api_tguser_send():
    """Open a conversation first — the thing a bot account cannot do."""
    data = request.json or {}
    persona = (data.get('persona') or '').strip()
    peer = (data.get('peer') or '').strip()
    text = (data.get('text') or '').strip()
    acct = _tgu_accounts().get(persona) or {}
    api_id, api_hash = _tgu_app_creds()
    if not (acct.get('session') and peer):
        return jsonify({'ok': False, 'error': 'Connect an account and give a @username or phone.'}), 400
    if not text:
        lim = persona_length_limits(persona)
        instr = (f'Write ONE warm, in-character opening message to {peer} on '
                 'Telegram. Natural, curious about them, no hard sell. End with a '
                 'question. ' + lim['note'])
        try:
            text = _fv_trim(_persona_text(persona, instr, max_tokens=lim['tokens'],
                                          temperature=0.95),
                            max_sentences=lim['sentences'], hard_cap=lim['cap'])
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)[:250]}), 400
    try:
        _tgu_import().send_message(api_id, api_hash, acct['session'], peer, text)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'{e.__class__.__name__}: {str(e)[:250]}'}), 400
    return jsonify({'ok': True, 'text': text})


def _tgu_boot():
    """Reconnect every stored account after a restart."""
    with app.app_context():
        for persona in _tgu_accounts():
            try:
                _tgu_start(persona)
            except Exception as e:
                _tgu_errors[persona] = str(e)[:300]


if _worker_enabled('TGUSER_AUTOSTART'):
    threading.Thread(target=_tgu_boot, daemon=True).start()


_tg_worker_started = [False]


def _tg_worker():
    """Server-side follow-up loop. Replies are webhook-driven; this only handles
    re-engagement, so a slow tick is fine."""
    import time as _t
    while True:
        _t.sleep(120)
        try:
            for persona in _tg_enabled_list():
                try:
                    with app.app_context():
                        _tg_followup_round(persona)
                except Exception:
                    pass
        except Exception:
            pass


_x_worker_started = [False]
_x_round_locks = {}


def _x_enabled_list():
    """Personas with a connected X account and the bot switched on."""
    out = []
    for persona, t in (_load_x_tokens() or {}).items():
        if (t or {}).get('access_token') and _x_behavior(persona).get('enabled', True):
            out.append(persona)
    return out


def _x_worker():
    """Server-side loop: keeps DM replies and follow-ups running for every
    connected X account without an open browser tab, in parallel so one busy
    account never holds up the others."""
    import time as _t
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=max(1, int(os.getenv('X_WORKERS', '8'))),
                              thread_name_prefix='xbot')
    pending = set()
    guard = threading.Lock()

    def _one(persona):
        try:
            with app.app_context():
                _x_dm_reply_round(persona)
                _x_followup_round(persona)
        except Exception:
            logger.exception('x round failed for %s', persona)
        finally:
            with guard:
                pending.discard(persona)

    while True:
        _t.sleep(60)
        try:
            for persona in _x_enabled_list():
                with guard:
                    if persona in pending:
                        continue
                    pending.add(persona)
                pool.submit(_one, persona)
        except Exception:
            logger.exception('x worker tick failed')


if _worker_enabled('X_WORKER') and not _x_worker_started[0]:
    _x_worker_started[0] = True
    threading.Thread(target=_x_worker, daemon=True).start()


if _worker_enabled('TELEGRAM_WORKER') and not _tg_worker_started[0]:
    _tg_worker_started[0] = True
    threading.Thread(target=_tg_worker, daemon=True).start()


if __name__ == '__main__':
    print('─' * 50)
    print('  AI Model Chat — Multi-Persona Server')
    print('─' * 50)
    print('  http://localhost:5000/         → Fan chat (Lilith)')
    print('  http://localhost:5000/profile  → Creator landing page')
    print('  http://localhost:5000/admin    → Persona builder')
    print('─' * 50)
    print('  Add GEMINI_API_KEY to .env to enable AI replies')
    print('─' * 50)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
