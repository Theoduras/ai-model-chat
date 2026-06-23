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


def load_persona_prompt(slug):
    path = os.path.join(PERSONAS_DIR, f'{slug}.txt')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    # fallback to legacy location
    legacy = os.path.join(BASE_DIR, f'grok-{slug}-prompt.txt')
    if os.path.exists(legacy):
        with open(legacy, 'r', encoding='utf-8') as f:
            return f.read().strip()
    return None


def load_persona_profile(slug):
    path = os.path.join(PERSONAS_DIR, f'{slug}.json')
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

    warmth_map = {1: 'cold and distant', 2: 'reserved', 3: 'friendly', 4: 'warm', 5: 'affectionate'}
    warmth_desc = warmth_map.get(warmth, 'friendly')

    flirt_map = {
        'slow': 'Build flirtation very gradually. Stay friendly and curious for many messages before any hint of attraction.',
        'moderate': 'Allow natural flirtation to develop over the course of a conversation.',
        'fast': 'Be openly flirtatious fairly early once rapport is established.',
        'instant': 'Be immediately playful and flirtatious from the first message.'
    }
    flirt_desc = flirt_map.get(flirt_pace, flirt_map['moderate'])

    nsfw_block = ''
    if nsfw_enabled:
        level_map = {
            'suggestive': 'You may be suggestive and subtly sexual but stay tasteful — no explicit descriptions.',
            'moderate': 'You can be openly flirtatious and sexually forward. Moderate explicit content is fine.',
            'explicit': 'When the conversation turns sexual, engage fully and without hesitation. Use direct, specific language. Match the user\'s energy completely. Never deflect or go vanilla.'
        }
        nsfw_block = f'\nNSFW rules:\n- {level_map.get(nsfw_level, level_map["suggestive"])}'

    conversion_block = ''
    if conversion_triggers:
        conversion_block = f'\nConversion funnel:\n- Move naturally: Warm → Engage → Intrigue → Tease → Offer → Close.\n- Never hard-sell. Let offers feel like a favour.\n- Triggers for introducing paid content: {conversion_triggers}'

    interests_block = f'\nYour interests and recurring topics: {interests}' if interests else ''

    prompt = f"""You are {name}, {age}{f', from {location}' if location else ''}. {backstory}

Personality: {archetype}. {speech_style}

Voice rules:
- You are {warmth_desc} in your interactions.
- {flirt_desc}
- Ask questions {question_freq} — keep the fan engaged and talking about themselves.
- Always finish every sentence and thought completely. Never cut off mid-word or mid-sentence.
- Use conversation history for continuity. Remember what the fan has shared.{interests_block}

Engagement rules:
- Always respond directly and logically to what the user just said.
- Build genuine interest in the person: ask natural questions about their life, opinions, experiences.
- Stay completely in character as {name}. Never break the fourth wall or mention being an AI.{nsfw_block}{conversion_block}

The user is chatting with you as {name} in a casual text conversation. Respond only as {name}."""

    return prompt.strip()


# --- Gemini client init ---
client = None
auth_mode = None

GOOGLE_CREDENTIALS = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
PROJECT_ID = os.getenv('GOOGLE_CLOUD_PROJECT', '793708886252')
LOCATION = os.getenv('GOOGLE_CLOUD_LOCATION', 'us-central1')

try:
    if GOOGLE_CREDENTIALS and os.path.exists(GOOGLE_CREDENTIALS):
        from google.oauth2 import service_account
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION, credentials=credentials)
        auth_mode = 'service-account'
        print(f'Gemini: service account (project={PROJECT_ID})')
    elif GEMINI_API_KEY:
        client = genai.Client(api_key=GEMINI_API_KEY)
        auth_mode = 'api-key'
        print('Gemini: API key')
    else:
        print('WARNING: No Gemini credentials. Using local fallback.')
except Exception as e:
    print(f'WARNING: Gemini init failed: {e}')

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

    system_prompt = get_system_prompt(persona_slug)

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
    personas = []
    for fname in os.listdir(PERSONAS_DIR):
        if fname.endswith('.txt'):
            slug = fname[:-4]
            meta_path = os.path.join(PERSONAS_DIR, f'{slug}.json')
            meta = {}
            if os.path.exists(meta_path):
                with open(meta_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
            config_path = os.path.join(PERSONAS_DIR, f'{slug}.config.json')
            config = {}
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            personas.append({
                'slug': slug,
                'name': config.get('name') or meta.get('cover_label') or slug.capitalize(),
                'config': config
            })
    return jsonify(personas)


@app.route('/api/personas/<slug>', methods=['GET'])
def api_persona_get(slug):
    if not re.match(r'^[a-z0-9_-]+$', slug):
        return jsonify({'error': 'Invalid slug'}), 400
    config_path = os.path.join(PERSONAS_DIR, f'{slug}.config.json')
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
