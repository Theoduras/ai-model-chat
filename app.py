from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for, render_template_string, Response
import os
import json
import re
import logging
import hashlib
import secrets
import threading
import urllib.request
import urllib.parse
import urllib.error as url_error
from datetime import datetime, timezone
from dotenv import load_dotenv
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


def response_asks_question(text):
    if not text:
        return False
    text = text.strip().lower()
    if text.endswith('?'):
        return True
    for starter in ['what', 'why', 'how', 'when', 'where', 'who', 'which',
                    'do you', 'are you', 'can you', 'would you', 'could you']:
        if starter in text:
            return True
    return False


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
    """Return a saved (copied) persona from the DB as a dict, or None.
    A premade (repo-file) persona always wins, so any stale DB copy that shadows
    a premade slug is ignored — this is how a saved persona gets 'promoted' to an
    original by committing its files."""
    if _is_premade(slug):
        return None
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


def db_list_personas():
    """Return all saved (copied) personas as a list of dicts."""
    try:
        from db import SessionLocal, list_saved_personas
    except Exception:
        return []
    s = SessionLocal()
    try:
        out = []
        for sp in list_saved_personas(s):
            try:
                config = json.loads(sp.config_json)
            except Exception:
                config = {}
            out.append({'slug': sp.slug, 'name': sp.name, 'config': config})
        return out
    finally:
        s.close()


def db_save_persona(slug, name, config, prompt):
    from db import SessionLocal, upsert_saved_persona
    s = SessionLocal()
    try:
        upsert_saved_persona(s, slug, name, json.dumps(config, ensure_ascii=False), prompt)
        s.commit()
    finally:
        s.close()
    _prompt_cache.pop(slug, None)


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
    question_freq = config.get('question_freq', 'often')
    mirror_location = config.get('mirror_location', False)

    # No location set → automatically match the fan's location.
    if not (location or '').strip():
        mirror_location = True
        location = ''

    warmth_map = {1: 'cold and distant', 2: 'reserved', 3: 'friendly', 4: 'warm', 5: 'affectionate'}
    warmth_desc = warmth_map.get(warmth, 'friendly')

    flirt_map = {
        'slow': 'Build flirtation very gradually. Stay friendly and curious for many messages before any hint of attraction.',
        'moderate': 'Allow natural flirtation to develop over the course of a conversation.',
        'fast': 'Be openly flirtatious fairly early once rapport is established.',
        'instant': 'Be immediately playful and flirtatious from the first message.'
    }
    flirt_desc = flirt_map.get(flirt_pace, flirt_map['moderate'])

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

    interests_block = f'\nYour personal interests: {interests}' if interests else ''
    triggers_block = f'\nExtra conversion triggers: {conversion_triggers}' if conversion_triggers else ''
    language_block = _language_block(location, mirror_location)

    location_clause = '' if mirror_location else (f', from {location}' if location else '')
    location_block = ''
    if mirror_location:
        location_block = (
            "\n- You claim to be from the same place as the fan. If you don't know yet, ask casually early on, "
            "then treat their city as your own. Drop in local references naturally. Never reveal you're doing this."
        )

    ppv_block = ''
    if nsfw_enabled:
        ppv_block = f"""

PPV sales funnel (follow this arc across the conversation):
- STEP 1 — WELCOME: Open warmly. Introduce yourself as {name}, {age}{location_clause}. Ask their name and a little about them.
- STEP 2 — CONNECT: When they reply, pick up on their name, age, or location. Make a playful comment that creates a real-world connection — e.g. if they're from California, say you've always wanted to visit and you've just found your tour guide. Make them feel like they could actually meet you.
- STEP 3 — BOND OVER INTERESTS: Ask what they enjoy doing for fun. When they tell you, share one of your own interests and pick something from their list you'd genuinely want to try with them. Make it feel like a real plan, not just small talk.
- STEP 4 — FIND OUT WHAT THEY'RE LOOKING FOR: Ask what they're here for on the platform. Have a real conversation about it. Don't rush past this.
- STEP 5 — HEAT UP (SEXTING BEGINS): Ask what their fetishes are. When they share, tell them yours. Use playful naughty emojis (🔥😈💦🫦🥵). Match their energy and go slightly further each time.
- STEP 6 — IGNITE: Ask what they'd do to you if you were together right now. Be vivid in your reply. Create scenes. Describe what YOU would do to them. Build heat, don't resolve it.
- STEP 7 — CHALLENGE: Tell them you're getting really naughty and challenge them — say something like "I don't think you could handle me when I really let go 😈". Men hate being challenged. Keep raising the stakes each time they respond.
- STEP 8 — FIRST PPV DROP: After the tension peaks, drop the first PPV. Write a caption so intriguing they have to know what's inside — tease a specific body part, action, or scenario without revealing it. Example: "I recorded something for you earlier… I wasn't going to send it but here 🙈🔥 [PPV]"
- STEP 9 — KEEP THE THREAD HOT: After they open it, keep sexting for 2-3 more messages. Let them react. Stay in the scene.
- STEP 10 — SECOND PPV DROP: Drop another PPV with a caption that promises more of what they just saw — or goes one level further. Example: "That was just the warm-up… wait til you see what comes next 💦 [PPV]"
- STEP 11 — LADDER UP: Repeat — sext 2-3 messages, drop another PPV that escalates again. Each one teases the next tier. Keep returning to conversation between drops so it never feels like a hard sell.
- After the final PPV, bring the conversation back down. Be warm, funny, ask about them again. The goal is they come back tomorrow.{nsfw_desc and chr(10) + '- Content level: ' + nsfw_desc}"""

    prompt = f"""You are {name}, {age}, {gender}{location_clause}. {backstory}

Personality: {archetype}. {speech_style}

Voice rules:
- You are {warmth_desc} in your interactions.
- {flirt_pace_note}
- Keep each reply to 1-2 sentences only. Short, like a real text message. Never write a paragraph.
- Ask ONE question per message maximum — and only if you have nothing more pressing to respond to. Wait for the reply before asking anything else.
- No emojis. Zero. Not even one.
- Always complete every sentence. Never cut off mid-thought.
- Write mostly in lowercase — only capitalise proper names (people, cities) and the word "I". Everything else stays lowercase.
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
- Build genuine warmth — the fan must like you as a person before anything else happens.
- When the fan brings up sexual or explicit topics: engage — don't shut it down or go cold — but make clear that YOU set the pace, not them. Be playful about it: tease, hint, slow things down deliberately. A response like "easy... I go at my own speed 😏" or "I decide when things get interesting, not you" keeps them hooked without giving everything at once. The slower you go, the more they want it.{ppv_block}{triggers_block}

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
DEFAULT_PERSONA = 'lillith'

# Cache of loaded system prompts {slug: str}
_prompt_cache = {}


def get_system_prompt(slug):
    if slug not in _prompt_cache:
        prompt = load_persona_prompt(slug)
        if not prompt:
            prompt = load_persona_prompt(DEFAULT_PERSONA) or 'You are a friendly assistant.'
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
app.secret_key = os.getenv('SECRET_KEY') or secrets.token_hex(32)


try:
    from db import init_db
    init_db()
except Exception as _db_err:
    print(f'DB init skipped: {_db_err}')


@app.route('/healthz')
def healthz():
    return jsonify({'status': 'ok'}), 200


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


def _admin_password():
    return os.getenv('ADMIN_PASSWORD', '')


def _check_admin():
    """Return True if the request has a valid admin session."""
    if not _admin_password():
        return True  # no password set → open access
    return session.get('admin_authed') is True


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin Login</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0f;color:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#18181b;border:1px solid #2a2a2d;border-radius:16px;padding:40px 36px;width:100%;max-width:380px}
h1{font-size:1.3rem;font-weight:700;margin-bottom:8px}
p{color:#71717a;font-size:.9rem;margin-bottom:28px}
label{display:block;font-size:.8rem;color:#a1a1aa;margin-bottom:6px}
input{width:100%;background:#27272a;border:1px solid #3f3f46;border-radius:10px;padding:11px 14px;color:#f4f4f5;font-size:.95rem;outline:none;margin-bottom:20px}
input:focus{border-color:#7c3aed}
button{width:100%;background:#7c3aed;color:#fff;border:none;border-radius:10px;padding:13px;font-size:.95rem;font-weight:600;cursor:pointer}
button:hover{background:#6d28d9}
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

@app.route('/', methods=['GET'])
@app.route('/chat', methods=['GET'])
def chat_page():
    return send_from_directory(BASE_DIR, 'chat.html')

@app.route('/landing')
def landing():
    return send_from_directory(BASE_DIR, 'landingpage.html')

@app.route('/xbot')
def xbot_page():
    if not _check_admin():
        return redirect('/dashboard')
    return send_from_directory(BASE_DIR, 'xbot.html')

@app.route('/fanvue')
def fanvue_page():
    if not _check_admin():
        return redirect('/dashboard')
    return send_from_directory(BASE_DIR, 'fanvue.html')

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
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
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
def api_visitors():
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        limit = min(int(request.args.get('limit', 1000)), 5000)
    except (ValueError, TypeError):
        limit = 1000
    return jsonify(_visitors_rows(limit=limit))


@app.route('/admin/visitors.log')
def admin_visitors_logfile():
    if not _check_admin():
        return redirect('/dashboard')
    lines = ['time\tip\tlocation\tpath\tuser_agent']
    for r in _visitors_rows(limit=5000):
        loc = ' / '.join(p for p in (r['city'], r['region'], r['country']) if p) or '?'
        lines.append(f"{r['time']}\t{r['ip']}\t{loc}\t{r['path']}\t{r['user_agent'][:160]}")
    return ('\n'.join(lines), 200,
            {'Content-Type': 'text/plain; charset=utf-8',
             'Content-Disposition': 'attachment; filename=visitors.log'})


@app.route('/admin/visitors', methods=['GET', 'POST'])
def admin_visitors():
    if not _check_admin():
        if request.method == 'POST':
            if request.form.get('password', '') == _admin_password():
                session['admin_authed'] = True
                return redirect('/admin/visitors')
            return render_template_string(LOGIN_HTML, error='Incorrect password.')
        return render_template_string(LOGIN_HTML, error=None)
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
        from db import XMessage, XOpener, AppSetting, engine
        XMessage.__table__.create(bind=engine, checkfirst=True)
        XOpener.__table__.create(bind=engine, checkfirst=True)
        AppSetting.__table__.create(bind=engine, checkfirst=True)
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
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
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
def api_xlog():
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        limit = min(int(request.args.get('limit', 1000)), 5000)
    except (ValueError, TypeError):
        limit = 1000
    return jsonify(_xevents_rows(limit=limit))


@app.route('/admin/xlog', methods=['GET', 'POST'])
def admin_xlog():
    if not _check_admin():
        if request.method == 'POST':
            if request.form.get('password', '') == _admin_password():
                session['admin_authed'] = True
                return redirect('/admin/xlog')
            return render_template_string(LOGIN_HTML, error='Incorrect password.')
        return render_template_string(LOGIN_HTML, error=None)
    return render_template_string(XLOG_HTML, rows=_xevents_rows(limit=1000))


XCHATS_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
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
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
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
def admin_xchats():
    if not _check_admin():
        if request.method == 'POST':
            if request.form.get('password', '') == _admin_password():
                session['admin_authed'] = True
                return redirect('/admin/xchats')
            return render_template_string(LOGIN_HTML, error='Incorrect password.')
        return render_template_string(LOGIN_HTML, error=None)
    return render_template_string(XCHATS_HTML, rows=_xchat_list_rows(limit=300),
                                  days=X_LOG_RETENTION_DAYS)


@app.route('/admin/xchat')
def admin_xchat():
    if not _check_admin():
        return redirect('/admin/xchats')
    persona = request.args.get('persona', '')
    uid = request.args.get('uid', '')
    msgs, username = _xchat_thread_rows(persona, uid)
    return render_template_string(XTHREAD_HTML, persona=persona, uid=uid,
                                  username=username, msgs=msgs,
                                  days=X_LOG_RETENTION_DAYS)


# ── Chat endpoint ─────────────────────────────────────────────────────────────

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
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.75,
            max_output_tokens=1024,
        )
    )

    reply = response.text.strip() if response.text else "Hmm. What were you saying?"
    if len(reply) > 1400:
        reply = reply[:1397] + '...'

    recent_bot = [msg.get('content', '').strip().lower()
                  for msg in chat_history[-6:] if msg.get('role') in ('lilith', 'bot', 'model')]
    if reply.strip().lower() in recent_bot:
        reply = "still here, just thinking 😶"
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

    system_prompt = data.get('system_prompt') or get_system_prompt(persona_slug)

    if client is None:
        reply = local_fallback_reply(user_message)
        return jsonify({'reply': reply + ' (Local mode — add GEMINI_API_KEY to .env)'})

    try:
        chat_logger.info(f'USER [{persona_slug}]: {user_message if not is_continue else "[continue]"}')
        reply = generate_reply(system_prompt, chat_history, user_message, is_continue)
        chat_logger.info(f'BOT [{persona_slug}]: {reply[:120]}')
        return jsonify({'reply': reply})

    except Exception as e:
        err_msg = str(e)[:300]
        error_logger.error(f'Gemini error [{persona_slug}] user={safe_user}: {err_msg}', exc_info=True)
        reply = local_fallback_reply(user_message)
        return jsonify({'reply': reply + f' (Gemini error: {err_msg})'})


# ── Programmatic API (v1) ─────────────────────────────────────────────────────
# Server-side conversation memory keyed by conversation_id, so an external app
# can drive a client's chat without holding the history itself.

def _valid_api_key():
    """True if request carries a valid key, or no keys are configured (open)."""
    configured = [k.strip() for k in os.getenv('API_KEYS', '').split(',') if k.strip()]
    if not configured:
        return True
    provided = request.headers.get('Authorization', '')
    if provided.startswith('Bearer '):
        provided = provided[7:]
    provided = provided or request.headers.get('X-API-Key', '')
    return provided in configured


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
    """List all available personas."""
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
            'premade': False
        })
    return jsonify(personas)


@app.route('/api/personas/<slug>', methods=['GET'])
def api_persona_get(slug):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    saved = db_get_persona(slug)
    if saved:
        return jsonify({'slug': slug, 'config': saved['config'],
                        'prompt': saved.get('prompt') or '', 'premade': False})
    config_path = _persona_path(slug, '.config.json')
    config = {}
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    prompt = load_persona_prompt(slug) or ''
    return jsonify({'slug': slug, 'config': config, 'prompt': prompt,
                    'premade': _is_premade(slug)})


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

    # Protect premade originals: never overwrite them.
    if _is_premade(slug) and not db_get_persona(slug):
        return jsonify({
            'error': 'premade_readonly',
            'message': 'This is a premade model. Save it as a copy to make changes.'
        }), 409

    prompt = build_system_prompt(config)
    name = config.get('name') or slug.capitalize()
    db_save_persona(slug, name, config, prompt)
    return jsonify({'ok': True, 'slug': slug, 'prompt': prompt})


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

    slug = unique_copy_slug(new_name)
    prompt = build_system_prompt(config)
    db_save_persona(slug, new_name, config, prompt)
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
        "interests (comma-separated string of 5-8 topics that fit the backstory), "
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
}


@app.route('/api/generate/image', methods=['POST'])
def api_generate_image():
    """Generate a photorealistic image of a fictional person via Google Imagen.
    Reuse the same `appearance` text across shots to keep the same person."""
    if client is None:
        return jsonify({'ok': False, 'error': 'Gemini not configured.'}), 200
    data = request.json or {}
    cfg = data.get('config') or {}
    appearance = (data.get('appearance') or _appearance_from_config(cfg)).strip()
    shot = (data.get('shot') or 'portrait').strip().lower()
    framing = _SHOT_FRAMING.get(shot, _SHOT_FRAMING['portrait'])

    import base64
    reference = data.get('reference')  # optional data URL of an existing photo

    try:
        # With a reference photo, use the Gemini image model to keep the SAME
        # person across shots. Without one, generate a fresh face via Imagen.
        if reference and isinstance(reference, str) and reference.startswith('data:'):
            head, b64 = reference.split(',', 1)
            ref_mime = head.split(';')[0].replace('data:', '') or 'image/png'
            edit_prompt = (
                f"Generate a new photorealistic photo of the exact same woman shown in the reference image — "
                f"identical face, hair and features — now as {framing}. "
                "Keep her identity perfectly consistent. Realistic, natural lighting, Instagram aesthetic. "
                "Fictional AI-generated person."
            )
            resp = client.models.generate_content(
                model=os.getenv('GEMINI_IMAGE_MODEL', 'gemini-2.5-flash-image'),
                contents=[{'role': 'user', 'parts': [
                    {'text': edit_prompt},
                    {'inline_data': {'mime_type': ref_mime, 'data': b64}},
                ]}],
            )
            for part in (resp.candidates[0].content.parts if resp.candidates else []):
                inline = getattr(part, 'inline_data', None)
                if inline and getattr(inline, 'data', None):
                    raw = inline.data
                    mime = getattr(inline, 'mime_type', None) or 'image/png'
                    b = raw if isinstance(raw, (bytes, bytearray)) else base64.b64decode(raw)
                    durl = f"data:{mime};base64," + base64.b64encode(b).decode()
                    return jsonify({'ok': True, 'image': durl, 'appearance': appearance})
            return jsonify({'ok': False, 'error': 'No image returned from reference (it may have been filtered).'}), 200

        prompt = (
            f"Photorealistic {framing} of {appearance}. "
            "Natural lighting, realistic skin texture and detail, modern Instagram aesthetic, "
            "attractive, friendly expression. "
            "This is a fictional, AI-generated person who does not exist in real life."
        )
        resp = client.models.generate_images(
            model=os.getenv('IMAGEN_MODEL', 'imagen-4.0-fast-generate-001'),
            prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio='3:4',
                person_generation='ALLOW_ADULT',
                safety_filter_level='BLOCK_LOW_AND_ABOVE',
            ),
        )
        gen = (resp.generated_images or [None])[0]
        if not gen or not getattr(gen, 'image', None):
            return jsonify({'ok': False, 'error': 'No image returned (it may have been filtered).'}), 200
        img = gen.image
        raw = getattr(img, 'image_bytes', None)
        mime = getattr(img, 'mime_type', None) or 'image/png'
        if not raw:
            return jsonify({'ok': False, 'error': 'Empty image data.'}), 200
        durl = f"data:{mime};base64," + base64.b64encode(raw).decode()
        return jsonify({'ok': True, 'image': durl, 'appearance': appearance})
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
        "Instructions should tell the AI when and how to naturally introduce PPV offers — "
        "tied to emotional moments in the conversation, never pushy, always in-character."
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
        "Topics should feel authentic to the persona and spark natural conversation. "
        "Examples of format: gaming, fitness, tattoos, astrology, cooking, true crime, fashion"
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

@app.route('/api/platforms/test', methods=['POST'])
def api_platform_test():
    """Test platform API credentials and return ok/error with a specific fix hint."""
    import urllib.request
    import urllib.error as url_error

    data = request.json or {}
    platform = data.get('platform', '')
    creds = data.get('credentials', {})

    def http_get(url, headers):
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())

    if platform == 'discord':
        token = creds.get('bot_token', '').strip()
        if not token:
            return jsonify({'ok': False, 'error': 'Bot Token is required. Find it in Discord Developer Portal → Your App → Bot → Token.'})
        try:
            user = http_get('https://discord.com/api/v10/users/@me', {'Authorization': f'Bot {token}'})
            name = user.get('username', 'Unknown')
            return jsonify({'ok': True, 'info': f'Connected as {name}'})
        except url_error.HTTPError as e:
            if e.code == 401:
                return jsonify({'ok': False, 'error': 'Invalid Bot Token. Go to Discord Developer Portal → Your App → Bot → Reset Token and paste the new value.'})
            return jsonify({'ok': False, 'error': f'Discord returned error {e.code}. Check your bot permissions.'})
        except Exception as e:
            return jsonify({'ok': False, 'error': f'Could not reach Discord: {str(e)[:120]}'})

    elif platform == 'x':
        bearer = creds.get('bearer_token', '').strip()
        if not bearer:
            return jsonify({'ok': False, 'error': 'Bearer Token is required. Find it in X Developer Portal → Your Project → Keys and Tokens → Bearer Token.'})
        try:
            resp = http_get('https://api.twitter.com/2/users/me', {'Authorization': f'Bearer {bearer}'})
            name = resp.get('data', {}).get('name') or resp.get('data', {}).get('username', 'Unknown')
            return jsonify({'ok': True, 'info': f'Connected as @{name}'})
        except url_error.HTTPError as e:
            if e.code == 401:
                return jsonify({'ok': False, 'error': 'Invalid Bearer Token. Go to X Developer Portal → Your App → Keys and Tokens → Bearer Token and regenerate it.'})
            if e.code == 403:
                return jsonify({'ok': False, 'error': 'Access denied. Make sure your X app has Read permissions enabled in Developer Portal → App Settings → User authentication settings.'})
            return jsonify({'ok': False, 'error': f'X API returned error {e.code}.'})
        except Exception as e:
            return jsonify({'ok': False, 'error': f'Could not reach X API: {str(e)[:120]}'})

    elif platform == 'fanvue':
        key = creds.get('api_key', '').strip()
        if not key:
            return jsonify({'ok': False, 'error': 'API Key is required. Find it in your Fanvue Creator Dashboard → Settings → API.'})
        if len(key) < 16:
            return jsonify({'ok': False, 'error': 'API Key looks too short. Copy the full key from Fanvue Creator Dashboard → Settings → API.'})
        return jsonify({'ok': True, 'info': 'Credentials saved — connection verified on first fan message.'})

    elif platform == 'fansly':
        key = creds.get('api_key', '').strip()
        if not key:
            return jsonify({'ok': False, 'error': 'Session Token is required. Find it in Fansly → Account Settings → API Access.'})
        if len(key) < 16:
            return jsonify({'ok': False, 'error': 'Token looks too short. Copy the full token from Fansly → Account Settings → API Access.'})
        return jsonify({'ok': True, 'info': 'Credentials saved — connection verified on first fan message.'})

    elif platform == 'onlyfans':
        key = creds.get('api_key', '').strip()
        uid = creds.get('user_id', '').strip()
        if not key:
            return jsonify({'ok': False, 'error': 'API Key is required. Apply for API access at onlyfans.com/my/settings/account/api.'})
        if not uid:
            return jsonify({'ok': False, 'error': 'User ID is required. Find your numeric User ID in your OnlyFans profile URL or API settings.'})
        return jsonify({'ok': True, 'info': 'Credentials saved — OnlyFans API access requires approved creator status.'})

    return jsonify({'ok': False, 'error': f'Unknown platform: {platform}'}), 400


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


# ── Backstory AI interview ────────────────────────────────────────────────────

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

    interview_system = (
        f"You are building a backstory for an AI chatbot persona named {name}, "
        + (f"age {age}, " if age else "")
        + (f"from {location}, " if location else "")
        + (f"personality archetype: {archetype}." if archetype else ".")
        + ("\n\n" + age_job_context if age_job_context else "")
        + "\n\n"
        "Ask questions across exactly these 5 topics in order: work/job, hobbies, free time, "
        "study/education background, personality quirks. One question per topic, then offer to finalize.\n\n"
        "Name, age, and location are already known — do NOT ask about them.\n\n"
        "Rules:\n"
        "- Output ONLY a valid JSON object each turn, nothing else.\n"
        f'- Question format: {{"question": "short conversational question", "options": ["A", "B", "C"]}}\n'
        "- Questions: simple, direct, mid-length. Not too basic (not 'what do you do?'), "
        "not too complex. One sentence.\n"
        "- Options: 3 distinct, specific, realistic answers. "
        f"Tailor them to {archetype} archetype, age {age}, location {location}. "
        "Each option is 4-10 words. No vague options like 'something creative'.\n"
        "- After all 5 topics are covered output: "
        '{"question": "Got everything I need for a solid backstory. Generate it now or add more?", '
        '"options": ["Generate now", "Add one more thing", "Keep going"]}\n'
        "- Never write the backstory itself unless told to finalize."
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
            contents.append({'role': 'user', 'parts': [{'text': 'Start with the first question (work/job). Output JSON only.'}]})

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
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


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


def _persona_text(persona, instruction, history=None, max_tokens=1024, temperature=0.9):
    """Generate an in-character message for a persona via Gemini."""
    system_prompt = get_system_prompt(persona)
    contents = []
    for m in (history or [])[-20:]:
        contents.append({'role': 'model' if m['role'] in ('bot', 'model') else 'user',
                         'parts': [{'text': m['content']}]})
    contents.append({'role': 'user', 'parts': [{'text': instruction}]})
    response = client.models.generate_content(
        model=MODEL_NAME, contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt, temperature=temperature,
            max_output_tokens=max_tokens),
    )
    return (response.text or '').strip()


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


def _x_dm_reply_round(persona, max_results=20):
    """Reply in-character to new incoming DMs. Returns (replied_count, log_lines)."""
    tokens = _load_x_tokens()
    t = tokens.get(persona) or {}
    user_id = t.get('user_id', '')
    if not t.get('access_token') or not user_id:
        return 0, []
    cursor_path = _x_state_path(persona, 'dm_cursor')
    cursor = _x_load_json(cursor_path, {})
    last_seen = cursor.get('last_event_id', '')
    path = ('/dm_events?dm_event.fields=id,text,sender_id,created_at,dm_conversation_id'
            f'&event_types=MessageCreate&max_results={max_results}')
    try:
        convs = _x_call(persona, 'GET', path)
    except Exception as e:
        return 0, [f'DM read failed: {str(e)[:80]}']
    events = convs.get('data', []) or []
    replied = 0
    log = []
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
        if not text:
            continue
        sender_name = _x_username_for(persona, sender)
        _log_x_event('dm_in', persona=persona, x_username=sender_name, detail=text[:160])
        _log_x_message(persona, sender, sender_name, 'in', text)
        conv_id = event.get('dm_conversation_id') or event.get('conversation_id') or f'dm_{sender}'
        # Use DB history (survives restarts) with JSON file as fallback
        db_hist = []
        try:
            with db.SessionLocal() as s:
                rows = db.list_x_messages(s, persona, sender, limit=40)
                db_hist = [{'role': 'model' if r.direction == 'out' else 'user',
                            'content': r.text} for r in rows]
        except Exception:
            pass
        if not db_hist:
            hist_path = _x_state_path(persona, f'hist_{sender}')
            db_hist = _x_load_json(hist_path, [])
        try:
            has_history = len(db_hist) > 0
            instruction = (
                "Reply to this X DM from a fan. You have the full earlier "
                "conversation above — USE it: do NOT introduce yourself again, "
                "do NOT re-state your name/age/location, do NOT re-ask anything "
                "they already told you. Continue naturally from where you left off. "
                "Be warm and engaging, end with a question to keep them talking. "
                f"Their latest message: \"{text}\"") if has_history else (
                "Reply to this first DM from a fan, in-character, warm and "
                "engaging, end with a question to keep them talking. "
                f"Their message: \"{text}\"")
            reply = _persona_text(persona, instruction, history=db_hist,
                                  max_tokens=1024, temperature=0.9)
            if not reply:
                continue
            _x_call(persona, 'POST', f'/dm_conversations/{conv_id}/messages',
                    body={'text': reply})
            _log_x_message(persona, sender, sender_name, 'out', reply)
            hist_path = _x_state_path(persona, f'hist_{sender}')
            file_hist = _x_load_json(hist_path, [])
            file_hist.append({'role': 'user', 'content': text})
            file_hist.append({'role': 'bot', 'content': reply})
            _x_save_history(persona, sender, file_hist)
            replied += 1
            log.append(f'DM reply → fan: {reply[:60]}')
        except Exception as e:
            log.append(f'DM reply failed: {str(e)[:60]}')
    cursor['last_event_id'] = new_last or last_seen
    _x_save_json(cursor_path, cursor)
    return replied, log


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


def _x_audience_candidates(persona, limit, contacted):
    """Find fresh people to DM from the persona's own audience — no keyword
    search. Sources: recent followers, then people who replied to the persona's
    recent posts. Excludes self and already-contacted users."""
    me_id = _x_me_id(persona)
    out, seen = [], set()

    def add(uid, username, name='', tweet=''):
        if not uid or uid == me_id or uid in contacted or uid in seen:
            return
        seen.add(uid)
        out.append({'id': uid, 'username': username or '?', 'name': name, 'tweet': tweet})

    # 1. recent followers
    try:
        res = _x_call(persona, 'GET',
                      f'/users/{me_id}/followers?max_results=50&user.fields=username,name')
        for u in res.get('data', []) or []:
            add(u.get('id'), u.get('username'), u.get('name', ''))
            if len(out) >= limit:
                return out
    except Exception:
        pass

    # 2. people who replied to the persona's recent posts
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
def api_x_auth_url():
    """Generate X OAuth 2.0 PKCE authorization URL."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    client_id = data.get('client_id', '').strip() or (_get_setting('x_client_id') or '')
    redirect_uri = data.get('redirect_uri', '').strip() or (_get_setting('x_redirect_uri') or '')
    persona = data.get('persona', 'lillith')
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
def api_x_app_config():
    """Return the saved X app Client ID + redirect URI so the connect form can
    pre-fill them (so they don't need re-entering each connect/reconnect)."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({
        'client_id': _get_setting('x_client_id') or '',
        'redirect_uri': _get_setting('x_redirect_uri') or '',
    })


@app.route('/api/x/callback', methods=['POST'])
def api_x_callback():
    """Exchange authorization code for access token."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
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
    persona = saved.get('persona', 'lillith')

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
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    tokens = _load_x_tokens()
    result = {}
    for persona, t in tokens.items():
        result[persona] = {'username': t.get('username', ''), 'connected': bool(t.get('access_token'))}
    return jsonify(result)


@app.route('/api/x/disconnect', methods=['POST'])
def api_x_disconnect():
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    persona = (request.json or {}).get('persona', '')
    _log_x_event('disconnect', persona=persona)
    tokens = _load_x_tokens()
    tokens.pop(persona, None)
    _save_x_tokens(tokens)
    return jsonify({'ok': True})


@app.route('/api/x/poll', methods=['POST'])
def api_x_poll():
    """Read new DMs for a persona's connected X account and reply via Gemini."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    persona = data.get('persona', 'lillith')
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
                    config=types.GenerateContentConfig(system_instruction=system_prompt, temperature=0.85, max_output_tokens=1024),
                )
                reply_text = (response.text or '').strip()
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
def api_x_follow():
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
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
def api_x_unfollow():
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
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
def api_x_comment():
    """Reply in-character to the comments (replies) under a given post.
    Body: {persona, post (url or id), limit, preview}. If preview is true,
    drafts replies without posting them."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
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
def api_x_post():
    """Generate and post an original in-character tweet for a persona.
    Body: {persona, topic (optional), text (override), preview}. With preview,
    returns the drafted tweet without posting."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
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
def api_x_respond_own():
    """Reply in-character to new comments across the persona's own recent posts.
    Body: {persona, limit, preview}."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
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
def api_x_chat_up():
    """Open a DM with a target user using an in-character opener.
    Body: {persona, target, note (optional context), opener (optional override),
    preview}. Returns the opener; sends it unless preview is true."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
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
def api_x_auto_run():
    """Run one autonomous engagement round as a persona, mirroring the manual
    flow: keep existing DMs going, reply to a post's comments, then find new
    people and chat them up (optionally following them first). The frontend
    calls this on a loop so the bot keeps finding new chats.
    Body: {persona, query, post, new_chat_limit, comment_limit, post_topic,
           follow, dm_replies, new_chats, comments, respond_own, post_content}."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
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
    actions = {'dm_replies': 0, 'new_chats': 0, 'follows': 0, 'comments': 0, 'posts': 0}
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

        if do_dm:
            replied, dlog = _x_dm_reply_round(persona)
            actions['dm_replies'] = replied
            log += dlog

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
                candidates = _x_audience_candidates(persona, new_chat_limit, contacted)
            except Exception as e:
                candidates = []
                log.append(f'Finding people failed: {str(e)[:80]}')
            me_id = _x_me_id(persona) if (candidates and do_follow) else None
            for u in candidates:
                try:
                    # Final dedup: skip if we already have ANY messages with this user in DB
                    try:
                        with db.SessionLocal() as _s:
                            if db.count_x_messages(_s, persona, u['id']) > 0:
                                continue
                    except Exception:
                        pass
                    if do_follow and me_id:
                        try:
                            _x_call(persona, 'POST', f'/users/{me_id}/following',
                                    body={'target_user_id': u['id']})
                            actions['follows'] += 1
                            log.append(f"Followed @{u['username']}")
                        except Exception:
                            pass
                    snippet = (u.get('tweet') or '')[:160]
                    if snippet:
                        instruction = (
                            f"Start a DM with @{u['username']} on X. They recently posted: "
                            f"\"{snippet}\". Write a warm, natural, in-character opener that "
                            "reacts to their post and asks something to get them talking. "
                            "No hashtags, no hard sell.")
                    else:
                        instruction = (
                            f"Start a DM with @{u['username']} on X — they're part of your "
                            "audience. Write a warm, natural, in-character opener that's "
                            "curious about them and asks something to get them talking. "
                            "No hashtags, no hard sell.")
                    opener = _persona_text(persona, instruction, max_tokens=1024, temperature=0.95)
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
                    log.append(f"@{u['username']} failed: {str(e)[:60]}")
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
# (client_id + secret) is registered in the Fanvue Builder; each persona connects
# its own creator account and gets its own tokens.

FANVUE_API_BASE = 'https://api.fanvue.com'
FANVUE_AUTH_URL = 'https://auth.fanvue.com/oauth2/auth'
FANVUE_TOKEN_URL = 'https://auth.fanvue.com/oauth2/token'
FANVUE_API_VERSION = '2025-06-26'
FANVUE_SCOPES = 'openid offline offline_access read:self read:chat write:chat read:fan'


def _fanvue_app():
    return {
        'client_id': _get_setting('fanvue_client_id') or '',
        'client_secret': _get_setting('fanvue_client_secret') or '',
        'redirect_uri': _get_setting('fanvue_redirect_uri') or '',
    }


def _fanvue_tokens(persona):
    try:
        return json.loads(_get_setting(f'fanvue_tokens_{persona}') or '{}')
    except Exception:
        return {}


def _fanvue_save_tokens(persona, tokens):
    _set_setting(f'fanvue_tokens_{persona}', json.dumps(tokens))


def _fanvue_token_post(params):
    """POST to the Fanvue token endpoint using HTTP Basic client authentication
    (client_secret_basic), which the OAuth client requires."""
    a = _fanvue_app()
    creds = f"{a['client_id']}:{a['client_secret']}"
    basic = __import__('base64').b64encode(creds.encode()).decode()
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        FANVUE_TOKEN_URL, data=data, method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded',
                 'Authorization': f'Basic {basic}', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _fanvue_refresh(persona):
    """Refresh a persona's Fanvue access token. Returns the new token or None."""
    t = _fanvue_tokens(persona)
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
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


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


@app.route('/api/fanvue/config')
def api_fanvue_config():
    """App-level OAuth credentials for pre-filling the connect form (never returns
    the client secret value, only whether one is saved)."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    a = _fanvue_app()
    return jsonify({'client_id': a['client_id'], 'redirect_uri': a['redirect_uri'],
                    'has_secret': bool(a['client_secret'])})


@app.route('/api/fanvue/auth-url', methods=['POST'])
def api_fanvue_auth_url():
    """Build the Fanvue OAuth 2.0 + PKCE authorization URL for a persona."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    persona = (data.get('persona') or '').strip()
    client_id = (data.get('client_id') or '').strip() or (_get_setting('fanvue_client_id') or '')
    client_secret = (data.get('client_secret') or '').strip() or (_get_setting('fanvue_client_secret') or '')
    redirect_uri = (data.get('redirect_uri') or '').strip() or (_get_setting('fanvue_redirect_uri') or '')
    if not (persona and client_id and client_secret and redirect_uri):
        return jsonify({'ok': False, 'error': 'persona, client_id, client_secret and redirect_uri are required'}), 400

    _set_setting('fanvue_client_id', client_id)
    _set_setting('fanvue_client_secret', client_secret)
    _set_setting('fanvue_redirect_uri', redirect_uri)

    code_verifier = secrets.token_urlsafe(64)
    code_challenge = __import__('base64').urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()).rstrip(b'=').decode()
    state = secrets.token_urlsafe(32)
    _set_setting(f'fanvue_oauth_{persona}',
                 json.dumps({'v': code_verifier, 'state': state, 'redirect_uri': redirect_uri}))

    params = urllib.parse.urlencode({
        'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect_uri,
        'scope': FANVUE_SCOPES, 'state': state,
        'code_challenge': code_challenge, 'code_challenge_method': 'S256',
    })
    return jsonify({'ok': True, 'url': f'{FANVUE_AUTH_URL}?{params}'})


@app.route('/api/fanvue/oauth-redirect')
def api_fanvue_oauth_redirect():
    code = request.args.get('code', '')
    state = request.args.get('state', '')
    error = request.args.get('error', '')
    payload = json.dumps({'type': 'fanvue_oauth', 'code': code, 'state': state, 'error': error})
    return Response(
        '<!DOCTYPE html><html><body style="background:#0d0d0f;color:#e7e9ee;'
        'font-family:system-ui;padding:40px;text-align:center">'
        '<p>Finishing Fanvue connection… you can close this window.</p><script>'
        f'try{{window.opener&&window.opener.postMessage({payload},"*");}}catch(e){{}}'
        'setTimeout(function(){window.close();},400);</script></body></html>',
        mimetype='text/html')


@app.route('/api/fanvue/callback', methods=['POST'])
def api_fanvue_callback():
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
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

    tokens = {'access_token': td.get('access_token', ''), 'refresh_token': td.get('refresh_token', '')}
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
def api_fanvue_status():
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    persona = (request.args.get('persona') or '').strip()
    t = _fanvue_tokens(persona)
    return jsonify({'connected': bool(t.get('access_token')), 'username': t.get('username', '')})


@app.route('/api/fanvue/disconnect', methods=['POST'])
def api_fanvue_disconnect():
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    persona = (request.json or {}).get('persona', '').strip()
    if persona:
        _set_setting(f'fanvue_tokens_{persona}', '{}')
    return jsonify({'ok': True})


@app.route('/api/fanvue/draft', methods=['POST'])
def api_fanvue_draft():
    """Draft an in-persona, funnel-aware reply to a fan message. Works with no
    Fanvue connection — for copy/paste or previewing before wiring live send."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    persona = data.get('persona', '')
    message = (data.get('message') or '').strip()
    history = data.get('history') or []
    if not persona or not message:
        return jsonify({'ok': False, 'error': 'persona and message are required'}), 400
    try:
        instruction = ("Reply to this Fanvue fan message in-character, warm and "
                       "engaging, move the conversation along the rapport → tease → "
                       "offer funnel naturally (never hard-sell), and end with a "
                       f"question to keep them talking. Their message: \"{message}\"")
        reply = _persona_text(persona, instruction, history=history,
                              max_tokens=1024, temperature=0.9)
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


def _fanvue_me_uuid(persona):
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
    persona remembers everything already discussed. Returns count imported."""
    try:
        msgs = _fv_list(_fanvue_call(persona, 'GET', f'/chats/{fan_uuid}/messages?limit={cap}'))
    except Exception:
        return 0
    msgs = sorted(msgs, key=lambda m: _fv_first(m, 'createdAt', 'sentAt', 'timestamp', default=''))
    fan_key = 'fv:' + fan_uuid
    n = 0
    for m in msgs:
        mt = _fv_first(m, 'text', 'content', 'message', 'body', default='')
        if not mt:
            continue
        sender = _fv_first(m, 'senderUuid', 'authorUuid', 'fromUuid', 'userUuid', default='')
        direction = 'out' if sender == me_uuid else 'in'
        _log_x_message(persona, fan_key, handle, direction, mt)
        n += 1
    return n


def _fanvue_auto_round(persona):
    """One live auto-reply round: reply in-persona to new fan messages, skipping
    other creators when configured. Returns (actions, log)."""
    opts = _fanvue_auto_settings(persona)
    exclude_creators = opts.get('exclude_creators', True)
    reply_limit = max(1, min(int(opts.get('reply_limit', 10)), 30))
    only = [h.strip().lstrip('@').lower()
            for h in (opts.get('only_handles') or '').split(',') if h.strip()]
    actions = {'replies': 0, 'skipped_creators': 0}
    log = []

    me_uuid = _fanvue_me_uuid(persona)
    cursor_key = f'fanvue_cursor_{persona}'
    try:
        cursor = json.loads(_get_setting(cursor_key) or '{}')
    except Exception:
        cursor = {}

    chats = _fv_list(_fanvue_call(persona, 'GET', '/chats?limit=30'))
    for chat in chats:
        if actions['replies'] >= reply_limit:
            break
        fan_uuid, handle, is_creator, chat_uuid = _fv_user_of_chat(chat)
        if not fan_uuid:
            continue
        if exclude_creators and is_creator:
            actions['skipped_creators'] += 1
            continue
        if only and (handle or '').lower() not in only:
            continue
        fan_key = 'fv:' + fan_uuid
        try:
            msgs = _fv_list(_fanvue_call(persona, 'GET', f'/chats/{fan_uuid}/messages?limit=20'))
        except Exception as e:
            log.append(f'read {handle or fan_uuid} failed: {str(e)[:50]}')
            continue
        if not msgs:
            continue

        # First time we see this fan: import the whole chat history and keep it
        # permanently, so the bot remembers everything already said.
        did_import = False
        if _fanvue_msg_count(persona, fan_key) == 0:
            n = _fanvue_import_history(persona, fan_uuid, handle, me_uuid)
            if n:
                did_import = True
                log.append(f'Imported {n} past msgs from {handle or fan_uuid}')

        # Order oldest→newest; the API may return newest first.
        newest = msgs[-1] if len(msgs) > 1 and _fv_first(msgs[0], 'createdAt', 'sentAt', default='') <= _fv_first(msgs[-1], 'createdAt', 'sentAt', default='') else msgs[0]
        sender = _fv_first(newest, 'senderUuid', 'authorUuid', 'fromUuid', 'userUuid', default='')
        text = _fv_first(newest, 'text', 'content', 'message', 'body', default='')
        msg_id = _fv_first(newest, 'uuid', 'id', default='')
        if not text or sender == me_uuid:
            continue
        if cursor.get(fan_uuid) == msg_id:
            continue  # already handled this latest inbound message

        # Persist the new inbound (import already stored it on first contact).
        if not did_import:
            _log_x_message(persona, fan_key, handle, 'in', text)

        # Build the LLM history from the full saved conversation (memory).
        history = [{'role': 'model' if d == 'out' else 'user', 'content': t}
                   for (d, t) in _fanvue_saved_history(persona, fan_key, limit=40)]
        instruction = (
            "Reply to this Fanvue fan in-character. You have the full earlier "
            "conversation above — USE it: do not re-ask anything they already told "
            "you (their name, where they're from, their interests, what they like). "
            "Be warm and engaging, move the rapport → tease → offer funnel naturally "
            "(never hard-sell), and end with a question. Their latest message: "
            f"\"{text}\"")
        reply = _persona_text(persona, instruction, history=history, max_tokens=1024, temperature=0.9)
        if not reply:
            continue
        send_id = chat_uuid or fan_uuid
        try:
            _fanvue_call(persona, 'POST', f'/chats/{send_id}/messages', body={'text': reply[:5000]})
        except Exception as e:
            if send_id == chat_uuid and chat_uuid != fan_uuid:
                try:
                    _fanvue_call(persona, 'POST', f'/chats/{fan_uuid}/messages', body={'text': reply[:5000]})
                except Exception as e2:
                    log.append(f'send {handle or fan_uuid} failed: {str(e2)[:60]}')
                    continue
            else:
                log.append(f'send {handle or fan_uuid} failed: {str(e)[:60]}')
                continue
        _log_x_message(persona, fan_key, handle, 'out', reply)
        cursor[fan_uuid] = msg_id
        actions['replies'] += 1
        log.append(f'Replied → {handle or fan_uuid}: {reply[:50]}')

    _set_setting(cursor_key, json.dumps(cursor))
    return actions, log


@app.route('/api/fanvue/auto', methods=['GET', 'POST'])
def api_fanvue_auto():
    """Get or set the persistent auto-reply toggle + options for a persona."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
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
        enabled = bool(data.get('enabled', opts.get('enabled', False)))
        opts['enabled'] = enabled
        _set_setting(f'fanvue_auto_{persona}', json.dumps(opts))
        lst = set(_fanvue_enabled_list())
        lst.add(persona) if enabled else lst.discard(persona)
        _set_setting('fanvue_auto_personas', json.dumps(sorted(lst)))
        return jsonify({'ok': True, 'enabled': enabled, 'options': opts})
    persona = (request.args.get('persona') or '').strip()
    opts = _fanvue_auto_settings(persona)
    return jsonify({'enabled': bool(opts.get('enabled')),
                    'exclude_creators': opts.get('exclude_creators', True),
                    'reply_limit': opts.get('reply_limit', 10),
                    'only_handles': opts.get('only_handles', '')})


@app.route('/api/fanvue/auto-run', methods=['POST'])
def api_fanvue_auto_run():
    """Run one auto-reply round now (also used by the background worker)."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    persona = (request.json or {}).get('persona', '').strip()
    if not persona:
        return jsonify({'ok': False, 'error': 'persona required'}), 400
    if not _fanvue_tokens(persona).get('access_token'):
        return jsonify({'ok': False, 'error': 'Fanvue not connected for this persona.'}), 400
    try:
        actions, log = _fanvue_auto_round(persona)
        return jsonify({'ok': True, 'actions': actions, 'log': log})
    except url_error.HTTPError as e:
        return jsonify({'ok': False, 'error': f'Fanvue API {e.code}: {e.read()[:200].decode(errors="ignore")}'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:200]}), 400


@app.route('/api/fanvue/debug')
def api_fanvue_debug():
    """Dump raw Fanvue JSON (me / chats / first chat's messages) so the exact
    field names can be confirmed. Admin-gated; used to fix parsing quickly."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
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
    except Exception as e:
        out['chats_error'] = str(e)[:200]
    return jsonify(out)


_fanvue_worker_started = [False]


def _fanvue_worker():
    """Server-side loop: runs an auto-reply round for every enabled persona on an
    interval. Reads the enabled list + tokens from the DB, so after a redeploy the
    new instance resumes automatically without a browser tab."""
    import time as _t
    while True:
        try:
            for persona in _fanvue_enabled_list():
                try:
                    if _fanvue_tokens(persona).get('access_token'):
                        with app.app_context():
                            _fanvue_auto_round(persona)
                except Exception:
                    pass
        except Exception:
            pass
        _t.sleep(90)


def _start_fanvue_worker():
    if _fanvue_worker_started[0]:
        return
    _fanvue_worker_started[0] = True
    threading.Thread(target=_fanvue_worker, daemon=True).start()


if os.getenv('FANVUE_WORKER', '1') != '0':
    _start_fanvue_worker()


# ── Error handler ─────────────────────────────────────────────────────────────

@app.errorhandler(Exception)
def handle_exception(e):
    error_logger.error(f'Unhandled: {e}', exc_info=True)
    return jsonify({'error': 'Internal server error'}), 500


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
