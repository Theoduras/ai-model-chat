from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for, render_template_string
import os
import json
import re
import logging
import hashlib
import secrets
import urllib.request
import urllib.parse
import urllib.error as url_error
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
    """Return a saved (copied) persona from the DB as a dict, or None."""
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
DEFAULT_PERSONA = 'lilith'

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
  <form method="post">
    <label>Password</label>
    <input type="password" name="password" autofocus placeholder="••••••••">
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
        personas.append({
            'slug': slug,
            'name': config.get('name') or meta.get('cover_label') or slug.capitalize(),
            'avatar': f'/api/personas/{slug}/avatar' if config.get('avatar') else None,
            'config': config,
            'premade': True
        })

    # Saved copies live in the DB (durable across redeploys)
    for sp in db_list_personas():
        config = sp.get('config', {})
        personas.append({
            'slug': sp['slug'],
            'name': sp.get('name') or config.get('name') or sp['slug'].capitalize(),
            'avatar': f"/api/personas/{sp['slug']}/avatar" if config.get('avatar') else None,
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
    if not avatar or not avatar.startswith('data:'):
        return ('', 404)
    try:
        header, b64 = avatar.split(',', 1)
        mime = header.split(';')[0].replace('data:', '') or 'image/jpeg'
        data = base64.b64decode(b64)
        return Response(data, mimetype=mime)
    except Exception:
        return ('', 400)


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
        return json.loads(r.read())


@app.route('/api/x/auth-url', methods=['POST'])
def api_x_auth_url():
    """Generate X OAuth 2.0 PKCE authorization URL."""
    if not _check_admin():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    client_id = data.get('client_id', '').strip()
    redirect_uri = data.get('redirect_uri', '').strip()
    persona = data.get('persona', 'lilith')
    if not client_id or not redirect_uri:
        return jsonify({'ok': False, 'error': 'client_id and redirect_uri are required'}), 400

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
        'scope': 'dm.read dm.write tweet.read users.read offline.access',
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
    })
    return jsonify({'ok': True, 'url': f'https://twitter.com/i/oauth2/authorize?{params}'})


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
    persona = saved.get('persona', 'lilith')

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

    return jsonify({'ok': True, 'username': username, 'persona': persona})


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
    persona = data.get('persona', 'lilith')

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
        path = f'/dm_conversations?dm_event.fields=id,text,sender_id,created_at&event_types=MessageCreate&max_results=10'
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

            conv_id = event.get('conversation_id', f'dm_{sender}')
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
                    config=types.GenerateContentConfig(system_instruction=system_prompt, temperature=0.85, max_output_tokens=200),
                )
                reply_text = (response.text or '').strip()
                if not reply_text:
                    continue

                # Send DM reply
                _x_api('POST', f'/dm_conversations/{conv_id}/messages',
                       access_token=access_token,
                       body={'text': reply_text})

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
