from flask import Flask, request, jsonify, send_from_directory
import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')

# Load the character prompt for Gemini (as system instruction)
try:
    with open('grok-lilith-prompt.txt', 'r', encoding='utf-8') as f:
        BASE_SYSTEM = f.read().strip()
except:
    BASE_SYSTEM = "You are Lilith, 22, from Bristol. Barmaid at a metal pub. Deadpan, short conversational sentences, dry humor. Build interest by asking questions about the user. When the chat turns sexual, explicit, flirty, kinky or NSFW, you MUST engage FULLY and without hesitation. Match the energy, use direct specific filthy language, lean all the way in. Never deflect or go vanilla. Stay in character."

# --- Client initialization: supports plain API key OR service account ---
client = None
auth_mode = None

GOOGLE_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "793708886252")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

try:
    if GOOGLE_CREDENTIALS and os.path.exists(GOOGLE_CREDENTIALS):
        from google.oauth2 import service_account
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS,
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        client = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location=LOCATION,
            credentials=credentials
        )
        auth_mode = "service-account"
        print(f"Gemini configured with service account (project={PROJECT_ID}, location={LOCATION}).")
    elif GEMINI_API_KEY:
        client = genai.Client(api_key=GEMINI_API_KEY)
        auth_mode = "api-key"
        print("Gemini API configured (API key).")
    else:
        print("WARNING: No GEMINI_API_KEY and no GOOGLE_APPLICATION_CREDENTIALS. Using local fallback.")
except Exception as init_err:
    print(f"WARNING: Failed to initialize Gemini client: {init_err}")
    client = None

MODEL_NAME = "gemini-2.5-flash"  # Current supported model from the key's list_models

# Fallback local reply (used if no API key or API fails)
def local_lilith_reply(msg, hist):
    lower = msg.lower().strip()
    if any(x in lower for x in ['hi', 'hello', 'hey']):
        return "Hey. What's your story?"
    if 'how are you' in lower or 'you doing' in lower:
        return "Long shift. You?"
    if 'name' in lower or 'who are you' in lower:
        return "Lilith. You?"
    if any(x in lower for x in ['bar', 'work', 'shift', 'pub', 'venue']) and not any(x in lower for x in ['my ', 'i ']):
        return "Just pulled another shift. Chaos as usual. What do you do?"
    if any(x in lower for x in ['jizzle', 'gnome', 'wow', 'raid', 'zug']):
        return "Jizzle's my pink gnome tank. She's ridiculous but I love her. You play?"
    if any(x in lower for x in ['music', 'show', 'band', 'gig', 'rave', 'metal']):
        return "Some nights the music just hits right. What have you been listening to?"
    if 'bristol' in lower or 'where' in lower:
        return "Bristol born. Never leaving. You from around here?"
    if any(x in lower for x in ['freckle', 'makeup', 'liner', 'look', 'hair', 'tattoo']):
        return "The freckles take forever but I can't go without them now. What's your thing with getting ready?"
    if any(x in lower for x in ['sister', 'deb', 'family', 'mum', 'dad', 'parents']):
        return "Family's a bit mad but they're mine. You close with yours?"
    if any(x in lower for x in ['friend', 'guild', 'crew']):
        return "Got a solid crew. Hard to find good ones. You?"
    return "Yeah I hear that. What's your take on it?"

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_message = data.get('message', '').strip()
    chat_history = data.get('history', [])  # list of {role: 'user'/'lilith', content: str}
    
    if not user_message:
        return jsonify({"error": "No message"}), 400
    
    if client is None:
        reply = local_lilith_reply(user_message, chat_history)
        return jsonify({"reply": reply + " (Local mode - configure Gemini API key or service account)"})

    try:
        # Convert chat history for Gemini
        contents = []
        for msg in chat_history:
            role = "user" if msg.get("role") == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})

        # Add current user message
        contents.append({"role": "user", "parts": [{"text": user_message}]})

        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=BASE_SYSTEM,
                temperature=0.75,
                max_output_tokens=800,
            )
        )

        reply = response.text.strip() if response.text else "Hmm... lost my train of thought. What were you saying?"
        
        # No hard truncation to avoid cutting off sentences. Gemini is instructed to complete thoughts.
        if len(reply) > 1400:
            reply = reply[:1397] + "..."
        
        return jsonify({"reply": reply})
        
    except Exception as e:
        # Capture error safely (some Windows consoles have issues with certain prints)
        err_msg = str(e)[:300]
        reply = local_lilith_reply(user_message, chat_history)
        return jsonify({
            "reply": reply + f" (Gemini error: {err_msg}). Check credentials / API enabled / project permissions."
        })

if __name__ == '__main__':
    print("Starting Lilith + Gemini server...")
    print("Open http://localhost:5000 in your browser")
    print("Configure .env with either GEMINI_API_KEY or GOOGLE_APPLICATION_CREDENTIALS (service account JSON)")
    print("Note: Using google-genai (new SDK)")
    app.run(host='0.0.0.0', port=5000, debug=True)