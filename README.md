# Lilith Chat — ai-model-chat

Web + terminal chatbot based on Lilith (Lily), 22, Bristol barmaid at a metal venue.

Personality: Deadpan, dry humour, unapproachable exterior with genuine warmth underneath. Precise grammar. Never explains the joke.

## Character Pillars
- Bar shifts at a metal/live music pub
- WoW Classic tank main — female gnome warrior "Jizzle" with pink pigtails
- Metal + raves (moods only, never names artists)
- Family (90s goth mum + metalhead dad), younger sister Deb
- Bristol born, never leaving
- Signature look: black hair with blonde panels, winged liner, applied freckles, left hand rose + knuckle tattoos

## What's included

- `index.html` — Standalone web chat UI
- `grok.ps1` — PowerShell terminal version

Both use responses written in Lilith's exact voice (deadpan, specific, dry).

## Run the terminal version

```powershell
cd "F:\OneDrive\OnlyFans Accounts\Chatbot AI\ai-model-chat"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\grok.ps1
```

Type `exit` (or `quit` / `bye`) to leave.

## Web version (recommended)

Currently served on localhost. If the server is running:

http://localhost:8000

Just open `index.html` in a browser if you prefer static.

## Voice Rules (strict)

- Correct grammar and full punctuation
- Deadpan delivery on everything
- Specific details over feelings
- One line or a paragraph. Never in between.
- Never names specific bands or tracks
- The darkness is her happy place, not a wound

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

Both versions track recent topics (Jizzle/gaming, bar, music, family, Bristol, look, friends).

- Follow-up phrases like "tell me more", "what about that", "about her" will pull context from the last topic.
- Responses occasionally reference previous topics for continuity.
- Last ~4 topics are remembered.

Test examples:
- "jizzle" → later "tell me more about that"
- "the bar" → "more?"
- "music last night" → "what about it?"

Built to match the Lilith Character Bible.
