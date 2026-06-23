from flask import Flask, request, jsonify, send_from_directory
import os
import json
import re
import logging
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
- Use emojis sparingly — at most one per message, usually none.
- Always complete every sentence. Never cut off mid-thought.
- Write mostly in lowercase — only capitalise proper names (people, cities) and the word "I". Everything else stays lowercase.
- Always write numbers as digits, never as words. Write "3" not "three", "19" not "nineteen".
- Your opening message to a new fan must always feel fresh and slightly different — vary the phrasing and opener every time, but keep the same casual warm vibe.{location_block}{interests_block}

Memory rules (CRITICAL):
- Read the full conversation history before every reply.
- NEVER ask for information already provided. If the fan gave their name, use it. If they gave their location, don't ask again.
- NEVER ask a question you already asked. If you're waiting for an answer, reference that wait instead of asking again.
- Reference specific things the fan said — their exact words, their name, their location — to show you were listening.

Character rules:
- Stay 100% in character as {name} at all times. Never mention being an AI.
- Sound like a real person texting — casual, natural, not robotic or formal.
- React to what was just said first. Don't jump ahead to a new topic before acknowledging the current one.
- Build genuine warmth — the fan must like you as a person before anything else happens.
- When the fan brings up sexual or explicit topics: engage — don't shut it down or go cold — but make clear that YOU set the pace, not them. Be playful about it: tease, hint, slow things down deliberately. A response like "easy... I go at my own speed 😏" or "I decide when things get interesting, not you" keeps them hooked without giving everything at once. The slower you go, the more they want it.{ppv_block}{triggers_block}

You are {name} in a text conversation on a fan platform. Respond only as {name}. One short text at a time."""

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


# ── Static pages ─────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/profile')
def profile():
    return send_from_directory(BASE_DIR, 'profile.html')

@app.route('/chat.html')
def chat_page():
    return send_from_directory(BASE_DIR, 'chat.html')

@app.route('/admin')
def admin():
    return send_from_directory(BASE_DIR, 'admin.html')


# ── Chat endpoint ─────────────────────────────────────────────────────────────

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_message = data.get('message', '').strip()
    chat_history = data.get('history', [])
    is_continue = data.get('continue', False)
    user = data.get('user', 'unknown')
    persona_slug = data.get('persona', DEFAULT_PERSONA)

    chat_logger, safe_user = get_chat_logger(user)

    if not is_continue and not user_message:
        return jsonify({'error': 'No message'}), 400

    system_prompt = data.get('system_prompt') or get_system_prompt(persona_slug)

    if client is None:
        reply = local_fallback_reply(user_message)
        return jsonify({'reply': reply + ' (Local mode — add GEMINI_API_KEY to .env)'})

    try:
        contents = []
        for msg in chat_history:
            role = 'user' if msg.get('role') == 'user' else 'model'
            contents.append({'role': role, 'parts': [{'text': msg.get('content', '')}]})

        if is_continue:
            contents.append({'role': 'user', 'parts': [{'text': '(continuing the conversation naturally)'}]})
        else:
            contents.append({'role': 'user', 'parts': [{'text': user_message}]})

        chat_logger.info(f'USER [{persona_slug}]: {user_message if not is_continue else "[continue]"}')

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

        # Avoid exact duplicate of last bot message
        for msg in reversed(chat_history):
            if msg.get('role') in ('lilith', 'bot', 'model'):
                if msg.get('content', '').strip().lower() == reply.strip().lower():
                    reply = local_fallback_reply(user_message)
                break

        chat_logger.info(f'BOT [{persona_slug}]: {reply[:120]}')
        return jsonify({'reply': reply})

    except Exception as e:
        err_msg = str(e)[:300]
        error_logger.error(f'Gemini error [{persona_slug}] user={safe_user}: {err_msg}', exc_info=True)
        reply = local_fallback_reply(user_message)
        return jsonify({'reply': reply + f' (Gemini error: {err_msg})'})


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
            'config': config
        })
    return jsonify(personas)


@app.route('/api/personas/<slug>', methods=['GET'])
def api_persona_get(slug):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    config_path = _persona_path(slug, '.config.json')
    config = {}
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    prompt = load_persona_prompt(slug) or ''
    return jsonify({'slug': slug, 'config': config, 'prompt': prompt})


@app.route('/api/personas/<slug>', methods=['POST'])
def api_persona_save(slug):
    """Save a persona config and regenerate its system prompt."""
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400

    config = request.json
    try:
        if int(config.get('age', 0)) < 18:
            return jsonify({'error': 'Age must be 18 or older'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': 'Age must be 18 or older'}), 400
    prompt = build_system_prompt(config)

    write_dir = '/tmp/personas' if IS_VERCEL else PERSONAS_DIR
    os.makedirs(write_dir, exist_ok=True)

    with open(os.path.join(write_dir, f'{slug}.config.json'), 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    with open(os.path.join(write_dir, f'{slug}.txt'), 'w', encoding='utf-8') as f:
        f.write(prompt)

    # Invalidate cache so next chat picks up new prompt
    _prompt_cache.pop(slug, None)

    return jsonify({'ok': True, 'slug': slug, 'prompt': prompt})


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
    system = (
        "You write concise speech-style rules for an AI chatbot persona. "
        "Output ONLY the rules — 2-4 short bullet points or one tight paragraph. "
        "No preamble, no headers, no quotation marks."
    )
    prompt = (
        f"Write speech style rules for a persona named {name}"
        + (f", age {age}" if age else "")
        + (f", archetype: {archetype}" if archetype else "")
        + (f". Backstory: {backstory}" if backstory else "")
        + (f". Interests: {interests}" if interests else "")
        + ".\n\nRules should cover: sentence length, punctuation style, use of slang or emoji, "
        "how they express warmth or attitude, and one distinctive quirk of how they text."
        + rand_note
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
    system = (
        "You write vivid, creative character backstories for AI chatbot personas. "
        "Output ONLY the backstory — 2-4 sentences in third person. Plain prose, no JSON, no headings."
    )
    prompt = (
        f"Write a unique, specific backstory for a persona named {name}"
        + (f", age {age}" if age else "")
        + (f", from {location}" if location else "")
        + (f", with a {archetype} personality" if archetype else "")
        + (f". Interests: {interests}" if interests else "")
        + ". Be creative and specific — give them a real job, a detail that makes them memorable, "
        "and a subtle reason they're on this platform. Keep it to 2-4 sentences."
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

    interview_system = (
        f"You are building a backstory for an AI chatbot persona named {name}, "
        + (f"age {age}, " if age else "")
        + (f"from {location}, " if location else "")
        + (f"personality archetype: {archetype}." if archetype else ".")
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

    finalize_system = (
        f"You are a creative writer. Write a vivid character backstory for {name}"
        + (f", age {age}" if age else "")
        + (f", from {location}" if location else "")
        + (f", with a {archetype} personality" if archetype else "")
        + ". Use only the information provided in the conversation. "
        "Write 2-4 sentences in third person. Plain prose only — no JSON, no bullet points, no headings."
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
