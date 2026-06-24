## What's included

- `index.html` — Standalone web chat UI
- `grok.ps1` — PowerShell terminal version

## Run the terminal version

```powershell
cd "F:\OneDrive\OnlyFans Accounts\Chatbot AI\ai-model-chat"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\grok.ps1
```

Type `exit` (or `quit` / `bye`) to leave.

## Web version (recommended - fully automatic with Gemini)

**No more simple script or manual copy-paste.**

The chat now automatically sends your message + full conversation history to **Gemini** (free Gemini API + the full Lilith character bible from grok-lilith-prompt.txt). Gemini generates the reply as Lilith and it appears directly in the chat.

### Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Get Gemini access. You have two main options:

   **Option A – Simple API key (easiest for most people)**
   - Go to https://aistudio.google.com/app/apikey
   - Create a key (note: newer keys may start with `AQ.` instead of `AIzaSy...`).

   **Option B – Service Account (for your Cloud project 793708886252 + restrictions)**
   - In Google Cloud Console → IAM & Admin → Service Accounts
   - Create a service account and download its JSON key file.
   - Grant it roles such as **Vertex AI User** and/or **Generative AI User**.
   - Enable the necessary APIs (Generative Language API or Vertex AI API) for project 793708886252.
   - Put the path in `.env` (see below).

   The app now supports both methods.

3. Create/update `.env` file in the project folder.

   For a normal API key:
   ```
   GEMINI_API_KEY=AIzaSy...or AQ....your_key_here
   ```

   For a service account (recommended for your setup):
   ```
   GOOGLE_APPLICATION_CREDENTIALS=C:\full\path\to\your-service-account.json
   GOOGLE_CLOUD_PROJECT=793708886252
   GOOGLE_CLOUD_LOCATION=us-central1
   ```

4. Run the server:
   ```bash
   python app.py
   ```

5. Open **http://localhost:5000**

Gemini reads the entire chat history + Lilith character to produce natural, short, conversational replies as Lilith (with persistent per-person memory).

If you see the local fallback + error message, check:
- The actual error printed by the server (now shown in the fallback reply)
- That you either have a working `GEMINI_API_KEY` **or** a valid `GOOGLE_APPLICATION_CREDENTIALS` pointing to a JSON service account key
- The service account has the required IAM roles
- The correct APIs are enabled on the project
- Restart the server after changing .env

### Notes
- The API key is required for automatic replies.
- Conversation history and topic memory are preserved per user name.
- The terminal version (grok.ps1) still uses the older local logic if you want to run it separately.

### Troubleshooting connection issues
1. Run the correct server: `python app.py`
2. Open **exactly** http://localhost:5000 (not the .html file directly)
3. Make sure `.env` has either `GEMINI_API_KEY` or `GOOGLE_APPLICATION_CREDENTIALS` set correctly.
4. Check the terminal running app.py for errors when sending messages.
5. Get a free Gemini key at https://aistudio.google.com/app/apikey if the key is missing.


## Customization

Edit the response logic in `index.html` (getBotReply) or `grok.ps1` (switch block) to expand topics.

## Persistent Per-Person Memory (new)

Memory no longer resets on page reload or script restart.

**Web (index.html):**
- On first load you are asked for a name/alias.
- Everything is saved in your browser's localStorage.
- Click the underlined name in the header to switch to a different person (new separate memory).
- Each name has its own ongoing conversation memory with Lilith.

**Terminal (grok.ps1):**
- Prompts for your name at the start of each run.
- Saves to `lilith-memories.json` (in the same folder).
- Enter the same name to continue where you left off with that person.
- Enter a different name to start a fresh memory for a new "chatter".

This way every person who chats feels like they have their own continuous relationship with Lilith.

## Topic Memory

- Follow-up phrases like "tell me more", "what about that", "about her" will pull context from the last topic.
- Responses occasionally reference previous topics for continuity.
- Last ~4 topics are remembered.

