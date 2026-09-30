from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for, render_template, render_template_string, Response, after_this_request, g
from flask import has_request_context, send_file
import os
import sys
import functools
import platform_pages
import free_tools
import copy
import json
import html as html_mod
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
from contextlib import contextmanager
from dotenv import load_dotenv
from utils import (platform_scoped, operator_only, _is_operator,
                   owned_slugs, request_persona)
import growth
import credits as CR
import imagegen
import storage
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
    """Whether a long-lived background poll loop should start."""
    val = (os.getenv(var) or '').strip()
    if val:
        return val != '0'
    return not IS_VERCEL

logger = logging.getLogger('app')
logger.setLevel(logging.INFO)
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

QUESTION_STARTERS = (
    'what', 'why', 'how', 'when', 'where', 'who', 'which',
    'do you', 'did you', 'are you', 'have you', 'can you',
    'would you', 'could you', 'tell me',
    'wat', 'waarom', 'hoe', 'wanneer', 'waar', 'wie', 'welke', 'welk',
    'ben je', 'ben jij', 'heb je', 'heb jij', 'kun je', 'kun jij',
    'wil je', 'wil jij', 'vind je', 'denk je', 'hou je', 'zou je',
    'vertel me', 'vertel eens',
    'was', 'warum', 'wieso', 'wie', 'wann', 'wo', 'wer',
    'welche', 'welcher', 'welches', 'bist du', 'hast du', 'kannst du',
    'willst du', 'magst du', 'würdest du', 'erzähl mir',
    'pourquoi', 'comment', 'quand', 'où', 'qui', 'quel', 'quelle',
    'est-ce que', 'es-tu', 'as-tu', 'peux-tu', 'veux-tu', 'dis-moi',
    'qué', 'por qué', 'cómo', 'cuándo', 'dónde',
    'quién', 'cuál', 'eres', 'tienes', 'puedes', 'quieres', 'dime',
    'o que', 'por que', 'quando', 'onde', 'quem', 'qual', 'me diz',
    'cosa', 'perché', 'quale', 'sei tu', 'hai', 'puoi', 'vuoi', 'dimmi',
)

_CLAUSE_SPLIT_RE = re.compile(r'(?:\.{3}|…|,)\s*')
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
    legacy = os.path.join(BASE_DIR, f'grok-{slug}-prompt.txt')
    if os.path.exists(legacy):
        return _read_file(legacy)
    return None

def load_persona_config(slug):
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
    legacy = os.path.join(BASE_DIR, 'profile_data.json')
    if os.path.exists(legacy):
        with open(legacy, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def _is_premade(slug):
    return os.path.exists(os.path.join(PERSONAS_DIR, f'{slug}.config.json')) \
        or os.path.exists(os.path.join(PERSONAS_DIR, f'{slug}.txt'))

LANDING_PERSONA = 'nova'
HOUSE_PERSONAS = {LANDING_PERSONA}

def _is_house_persona(slug):
    return slug in HOUSE_PERSONAS

def db_get_persona(slug):
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
                'owner_id': sp.owner_id,
                'created_at': sp.created_at.isoformat() if sp.created_at else None,
                'updated_at': sp.updated_at.isoformat() if sp.updated_at else None,
            })
        return out
    except Exception:
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

def _persona_live_blocked(slug):
    ws_id = _persona_owner(slug)
    if not ws_id:
        return False
    viewer = _current_user()
    if viewer and (viewer.get('is_admin') or _workspace_id(viewer) == ws_id):
        return False
    try:
        from db import User, Workspace
        s = _db_session()
        try:
            ws = s.get(Workspace, ws_id)
            owner = s.get(User, ws.owner_id if ws else ws_id)
            return bool(owner and owner.tier == FREE_TIER_KEY
                        and owner.role not in ('admin', 'super_admin'))
        finally:
            s.close()
    except Exception:
        error_logger.error('Live check failed for %s', slug, exc_info=True)
        return False

_NOT_LIVE = {'error': "This character isn't live yet.", 'not_live': True}

def _can_edit_persona(slug, user):
    if not user:
        return False
    if user.get('is_admin'):
        return True
    if _is_house_persona(slug):
        return False
    owner = _persona_owner(slug)
    if owner:
        return owner == (user.get('workspace_id') or user['id'])
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
    base = re.sub(r'[^a-z0-9]+', '-', (name or 'persona').lower()).strip('-') or 'persona'
    slug = base
    i = 2
    existing_saved = {p['slug'] for p in db_list_personas()}
    while _is_premade(slug) or slug in existing_saved:
        slug = f'{base}-{i}'
        i += 1
    return slug

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
    loc = (location or '').lower()
    for keys, lang in _LANGUAGE_BY_PLACE:
        if any(k in loc for k in keys):
            return lang
    return ('English', None)

def _language_block(location, mirror_location):
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
    r'naakt\w*|(laat|mag ik)\s+(me\s+|het\s+)?zien|'
    r'(stuur|heb je|mag ik)\s+(me\s+|mij\s+|een\s+|je\s+)*(foto|fotos|foto\'s|filmpje|video)\w*|'
    r'nackt\w*|zeig\s+mir|(schick|hast du)\s+(mir\s+)?(ein\s+|deine\s+)?(bild|bilder|foto)\w*|'
    r'\bnue?s?\b|montre[-\s]moi|(envoie|as-tu)\s+(moi\s+)?(une\s+|des\s+|tes\s+)?photos?|'
    r'desnuda\w*|enséñame|muéstrame|(mándame|manda)\s+(una\s+|unas\s+|tus\s+)?fotos?|'
    r'pelada\w*|me\s+mostra|'
    r'nuda\w*|mostrami|(mandami|hai)\s+(una\s+|delle\s+|le tue\s+)?foto'
    r')\b', re.I)

def _spicy_asked(text):
    return bool(_SPICY_ASK_RE.search(text or ''))

_PPV_MARKER_RE = re.compile(r'\s*\[\s*ppv\s*\]\s*', re.I)

def strip_ppv_marker(text):
    return _PPV_MARKER_RE.sub(' ', text or '').strip()

def build_system_prompt(config):
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

    if not (location or '').strip():
        mirror_location = True
        location = ''

    warmth_map = {1: 'cold and distant', 2: 'reserved', 3: 'friendly', 4: 'warm', 5: 'affectionate'}
    warmth_desc = warmth_map.get(warmth, 'friendly')

    if warmth <= 2:
        rapport_note = ('Earn her interest slowly — pay attention and remember what the fan says, '
                        'but stay measured. Do not gush, and do not act closer than you feel.')
    else:
        rapport_note = 'Build genuine warmth — the fan must like you as a person before anything else happens.'

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

client = None
auth_mode = None

PROJECT_ID = os.getenv('GOOGLE_CLOUD_PROJECT', '793708886252')
LOCATION = os.getenv('GOOGLE_CLOUD_LOCATION', 'us-central1')
ENV_PATH = os.path.join(BASE_DIR, '.env')

def update_env_var(key, value):
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
DEFAULT_PERSONA = 'lilly'
_prompt_cache = {}

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
    return _prompt_cache[slug] + _persona_avoid_block(slug)

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
        return secrets.token_hex(32)

_GZIP_TYPES = ('text/', 'application/json', 'application/javascript', 'image/svg+xml')
_GZIP_MAX = 4 * 1024 * 1024

# ── Cloud Media Upload Route ──────────────────────────────────────────────────
@app.route('/api/media/upload', methods=['POST'])
def api_media_upload():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Sign in required'}), 401

    persona_slug = request.form.get('persona')
    if not persona_slug:
        return jsonify({'error': 'Persona slug required'}), 400

    if not _can_edit_persona(persona_slug, user):
        return jsonify({'error': 'Access denied'}), 403

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    try:
        mime = file.mimetype or 'image/jpeg'
        file_bytes = file.read()
        
        # 1. Store directly into kept prefix via storage.py
        cloud_path = storage.put(persona_slug, file_bytes, mime, prefix=storage.KEPT_PREFIX)

        from db import SessionLocal, PersonaMedia
        s = SessionLocal()
        try:
            # 2. Insert into the PersonaMedia table
            new_media = PersonaMedia(
                slug=persona_slug,
                kind='image',
                mime=mime,
                gcs_path=cloud_path,
                source='uploaded',
                approved=True,
                approved_for_training=True
            )
            s.add(new_media)
            s.commit()
            
            media_id = new_media.id
            media_url = f"/api/personas/{persona_slug}/media/{media_id}/image"

            return jsonify({
                'ok': True,
                'media': {
                    'id': media_id,
                    'url': media_url,
                    'thumb': media_url,
                    'kind': 'image',
                    'source': 'uploaded'
                }
            })
        finally:
            s.close()

    except Exception as e:
        error_logger.error('Media upload failed', exc_info=True)
        return jsonify({'error': 'Upload failed on server'}), 500

@app.after_request
def _gzip_response(resp):
    try:
        if (resp.status_code != 200 or resp.headers.get('Content-Encoding')
                or 'gzip' not in request.headers.get('Accept-Encoding', '')
                or not (resp.mimetype or '').startswith(_GZIP_TYPES)
                or resp.is_streamed and not resp.direct_passthrough):
            return resp
        resp.direct_passthrough = False
        data = resp.get_data()
        if len(data) < 1024 or len(data) > _GZIP_MAX:
            return resp
        import gzip
        resp.set_data(gzip.compress(data, compresslevel=6, mtime=0))
        resp.headers['Content-Encoding'] = 'gzip'
        resp.headers.add('Vary', 'Accept-Encoding')
        if resp.headers.get('ETag') and not resp.headers['ETag'].startswith('W/'):
            resp.headers['ETag'] = 'W/' + resp.headers['ETag']
    except Exception:
        pass
    return resp

try:
    from db import init_db
    init_db()
except Exception as _db_err:
    print(f'DB init skipped: {_db_err}')

def _claim_house_personas():
    try:
        from db import SessionLocal, first_admin, reassign_persona_owner
    except Exception:
        return
    try:
        s = SessionLocal()
    except Exception:
        return
    try:
        admin = first_admin(s)
        if admin is None:
            return
        for slug in HOUSE_PERSONAS:
            if reassign_persona_owner(s, slug, admin.id):
                logger.info('HOUSE PERSONA %s moved to admin %s', slug, admin.email)
        s.commit()
    except Exception:
        error_logger.error('House personas not claimed', exc_info=True)
    finally:
        s.close()

_claim_house_personas()

app.secret_key = _session_secret()
app.permanent_session_lifetime = timedelta(days=30)

def _persistence_warnings():
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
    if not (os.getenv('PUBLIC_BASE_URL') or '').strip():
        warns.append(
            'PUBLIC_BASE_URL is unset, so callback and webhook URLs are built '
            'from whichever host serves the request. Set it to this '
            "environment's own public origin.")
    return warns

for _w in _persistence_warnings():
    print(f'CONFIG WARNING: {_w}')

_BLOCKED_SUFFIXES = ('.py', '.pyc', '.pyo', '.db', '.sqlite', '.sqlite3', '.db-journal',
                     '.log', '.env', '.pem', '.key', '.cfg', '.ini', '.toml', '.lock',
                     '.txt', '.md', '.yml', '.yaml')
_BLOCKED_DIRS = ('personas/', 'character_looks/', 'logs/', '__pycache__/', 'templates/', '.git/')
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
    if path.endswith(_BLOCKED_SUFFIXES):
        return ('Not found', 404)
    return None

_BLOG_SLUGS = ['from-fan-to-buyer', 'writing-a-voice', 'phases-that-convert',
               'photos-that-earn']

_PUBLIC_PAGES = [('/', '1.0', 'weekly'),
                 ('/pricing', '0.9', 'weekly'),
                 ('/register', '0.6', 'monthly'),
                 ('/login', '0.3', 'monthly'),
                 ('/blog', '0.7', 'weekly'),
                 ('/privacy', '0.2', 'yearly'),
                 ('/tos', '0.2', 'yearly')]
_PUBLIC_PAGES += [(f'/blog/{slug}', '0.5', 'monthly') for slug in _BLOG_SLUGS]
_PUBLIC_PAGES += [('/' + t['slug'], '0.8' if t['track'] else '0.6', 'monthly')
                  for t in free_tools.TOOLS]
_PUBLIC_PAGES += [(f'/{slug}', '0.9' if slug == 'ai-image-generator' else '0.8', 'monthly')
                  for slug in platform_pages.PAGES]
_PUBLIC_PATHS = {path for path, _, _ in _PUBLIC_PAGES}

_CRAWL_DISALLOW = ['/dashboard', '/admin', '/account', '/billing', '/tokens', '/api/',
                   '/xbot', '/fanvue', '/onlyfans', '/threads', '/telegram', '/auth/',
                   '/logout', '/go/']

def _request_origin():
    proto = (request.headers.get('X-Forwarded-Proto') or '').split(',')[0].strip()
    origin = request.url_root.rstrip('/')
    if proto == 'https' and origin.startswith('http://'):
        origin = 'https://' + origin[len('http://'):]
    return origin

def _callback_origin():
    explicit = (os.getenv('PUBLIC_BASE_URL') or '').strip().rstrip('/')
    return explicit or _request_origin()

def _site_origin():
    explicit = (os.getenv('SITE_URL') or '').strip().rstrip('/')
    return explicit or _request_origin()

def _seo_noindex_all():
    return (os.getenv('SEO_NOINDEX_ALL') or '').strip() == '1'

@app.route('/robots.txt')
def robots_txt():
    if _seo_noindex_all():
        return Response('User-agent: *\nDisallow: /\n', mimetype='text/plain')
    lines = ['User-agent: *']
    lines += ['Disallow: ' + p for p in _CRAWL_DISALLOW]
    lines += ['Allow: ' + p for p in sorted(_PUBLIC_PATHS)
              if p.startswith(tuple(_CRAWL_DISALLOW))]
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
    want = (os.getenv('GOOGLE_SITE_VERIFICATION') or '').strip()
    want = want[:-5] if want.endswith('.html') else want
    want = want[len('google'):] if want.startswith('google') else want
    if not want or token != want:
        return ('Not found', 404)
    return Response('google-site-verification: google%s.html' % want,
                    mimetype='text/html')

@app.route('/js/analytics.js')
def analytics_js():
    ga = (os.getenv('GA_MEASUREMENT_ID', 'G-3Q0XHZP4XV') or '').strip()
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

_CANONICAL_TAG = re.compile(r'<link[^>]*rel=["\']canonical["\'][^>]*>\s*', re.I)

@app.after_request
def _canonical_public_pages(resp):
    path = request.path or '/'
    if (request.method != 'GET' or resp.status_code != 200
            or not resp.mimetype == 'text/html'
            or path not in _PUBLIC_PATHS and not path.startswith('/blog/')):
        return resp
    resp.direct_passthrough = False
    html = resp.get_data(as_text=True)
    tag = '<link rel="canonical" href="%s%s">' % (_site_origin(), path)
    html = _CANONICAL_TAG.sub('', html)
    html, n = re.subn(r'</head>', tag + '\n</head>', html, count=1, flags=re.I)
    if n:
        resp.set_data(html)
    return resp

_NOINDEX_PATHS = ('/chat',)

@app.after_request
def _noindex_fan_pages(resp):
    if _seo_noindex_all() or (request.path or '/').lower().startswith(_NOINDEX_PATHS):
        resp.headers['X-Robots-Tag'] = 'noindex, nofollow'
    return resp

_REVALIDATE_PREFIXES = ('/js/', '/css/')

@app.after_request
def _revalidate_app_shell(resp):
    if request.method not in ('GET', 'HEAD') or 300 <= resp.status_code < 400:
        return resp
    if ((resp.mimetype or '').startswith('text/html')
            or (request.path or '/').startswith(_REVALIDATE_PREFIXES)):
        resp.headers['Cache-Control'] = 'no-cache, must-revalidate'
    elif (request.path or '/').startswith(_LONG_CACHE_PREFIXES) and resp.status_code == 200:
        resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp

_LONG_CACHE_PREFIXES = ('/img/', '/fonts/')

# (Rest of static page definitions, authentication, admin, and platform controllers as in your original app.py)
