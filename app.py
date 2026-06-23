from flask import Flask, request, jsonify, send_from_directory
import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')

# Load the character prompt
try:
    with open('grok-lilith-prompt.txt', 'r', encoding='utf-8') as f:
        BASE_SYSTEM = f.read().strip()
except:
    BASE_SYSTEM = "You are Lilith, 22, from Bristol. Barmaid at a metal pub. Deadpan, short conversational sentences, dry humor. Build interest by asking questions about the user. Stay in character."

XAI_API_KEY = os.getenv("XAI_API_KEY")
if not XAI_API_KEY:
    print("WARNING: XAI_API_KEY not set. Set it in .env or environment variable.")

MODEL = "grok-beta"  # Change to "grok-2-latest" or whatever is current if needed

def build_messages(user_message, chat_history):
    """Build messages list for Grok API: system + history + new user msg"""
    messages = [
        {"role": "system", "content": BASE_SYSTEM}
    ]
    
    # Add conversation history
    for msg in chat_history:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})
    
    # Add current message
    messages.append({"role": "user", "content": user_message})
    
    return messages

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
    
    if not XAI_API_KEY:
        return jsonify({
            "reply": "Hey... I need an XAI_API_KEY to talk properly right now. Set it up and try again?",
            "error": "no_key"
        }), 200  # Still return something
    
    try:
        messages = build_messages(user_message, chat_history)
        
        payload = {
            "model": MODEL,
            "messages": messages,
            "temperature": 0.75,
            "max_tokens": 280,  # Keep replies relatively short and conversational
            "stream": False
        }
        
        headers = {
            "Authorization": f"Bearer {XAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        resp = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if resp.status_code != 200:
            print("API error:", resp.text)
            return jsonify({
                "reply": "Something's off with the connection to Grok right now. Try again in a sec?",
                "error": "api_error"
            }), 200
        
        result = resp.json()
        reply = result["choices"][0]["message"]["content"].strip()
        
        # Basic cleanup to keep it short and Lilith-like
        if len(reply) > 500:
            reply = reply[:497] + "..."
        
        return jsonify({"reply": reply})
        
    except Exception as e:
        print("Error calling Grok API:", str(e))
        return jsonify({
            "reply": "The connection to Grok glitched. Mind repeating that?",
            "error": str(e)
        }), 200

if __name__ == '__main__':
    print("Starting Lilith + Grok server...")
    print("Open http://localhost:5000 in your browser")
    app.run(host='0.0.0.0', port=5000, debug=True)