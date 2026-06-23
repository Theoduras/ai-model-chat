# CLAUDE.md — ai-model-chat Project Rules

## CRITICAL: Session Continuity Rule

**Claude Code NEVER stops running mid-session due to user questions.**
- Questions during a run are treated as context or clarification — answer inline and keep going.
- The only stop signal is the exact string **`STOP RUNNING`** typed in full caps by the user.
- If the user asks something mid-task, answer it concisely and continue the task without waiting for further prompting.

---

## Project Purpose

This is an **OnlyFans-style AI chatbot platform** where content creators (models) can:
1. Build a custom AI persona via a **visual configuration interface**.
2. Define the persona's voice, tone, backstory, and escalation strategy.
3. Deploy that persona to convert fans → engaged chatters → content buyers.

The backend is Python/Flask + Google Gemini. The frontend is plain HTML/JS. Hosted on Vercel.

---

## Architecture

```
app.py                  — Flask server, Gemini API calls, chat history, logging
index.html              — Main chat UI (fan-facing)
profile.html            — Profile/persona display page
templates/profile.html  — Jinja2 template variant
grok-lilith-prompt.txt  — System prompt for the "Lilith" persona (loaded at runtime)
profile_data.json       — Persona metadata (name, bio, stats, photos)
.env                    — API keys (never commit)
vercel.json             — Vercel deployment config
requirements.txt        — Python deps
```

---

## Visual Persona Builder — Core Feature

The persona builder is the **centrepiece** of this platform. It must be a UI panel (not code editing) where the creator fills in fields and Claude Code generates/updates the system prompt and profile data automatically.

### Persona Voice Fields (all configurable via UI)

| Field | Description |
|---|---|
| **Name & Age** | The persona's display name and age |
| **Backstory** | 2-3 sentence origin story (job, city, vibe) |
| **Personality archetype** | e.g. Deadpan/Dry, Bubbly/Sweet, Dominant/Edgy, Girl-Next-Door |
| **Speech style** | Sentence length, grammar precision, use of slang/emoji |
| **Warmth level** | Cold → Distant → Friendly → Warm → Affectionate (slider) |
| **Flirt escalation pace** | Slow burn → Moderate → Fast → Instant |
| **NSFW engagement** | Toggle on/off + explicit permission level (suggestive / moderate / explicit) |
| **Interests & hooks** | Topics the persona returns to naturally (gaming, fitness, fashion, etc.) |
| **Conversion triggers** | Phrases/moments that naturally introduce paid content offers |
| **Question frequency** | How often the persona asks the fan a question (keeps engagement) |
| **Memory style** | How the persona references past conversation details |

### Conversion Funnel Rules (must be baked into every persona prompt)

The chatbot's job is to move fans through this funnel — the persona builder controls the pace:

1. **Warm** — Friendly opener, build rapport, ask about the fan.
2. **Engage** — Reference things the fan said, create a sense of being "known".
3. **Intrigue** — Drop hints about exclusive content without hard-selling.
4. **Tease** — Light flirt or behind-the-scenes reference that implies more exists.
5. **Offer** — Natural, in-character mention of a paid post/PPV/subscription perk.
6. **Close** — Reinforce the value, make the fan feel special for buying.

The system prompt generator must inject the current funnel stage logic based on creator settings.

---

## System Prompt Generation Rules

When the persona builder saves, Claude Code must:

1. Read the field values from the UI config form.
2. Generate a `grok-{persona-slug}-prompt.txt` file using this structure:

```
You are [Name], [Age], from [Location]. [Backstory sentence].

Personality: [Archetype description]. [Speech style rules].

Voice rules:
- [Grammar/punctuation rule]
- [Sentence length rule]
- [Warmth instruction]
- [Question frequency rule]

Engagement rules:
- Always reference things the fan has shared previously.
- Build genuine interest in the fan as a person.
- [Flirt escalation instruction]
- [NSFW rule if enabled]

Conversion rules:
- Move naturally from rapport → intrigue → tease → offer.
- Never hard-sell. Let the offer feel like a favour to the fan.
- [Creator-defined conversion triggers]

Stay completely in character. Never mention being an AI.
```

3. Update `profile_data.json` with the new persona metadata.
4. Hot-reload the persona in the Flask app without restarting the server.

---

## Coding Standards

- **Python**: Flask, no ORM. Keep app.py clean — extract helpers into `utils.py` if file exceeds 300 lines.
- **Frontend**: Vanilla JS + plain HTML. No React, no build step. Tailwind CDN is acceptable for styling.
- **API calls**: Always use `try/except` around Gemini calls with meaningful fallback messages.
- **Secrets**: `.env` only. Never hardcode keys. `.env` is in `.gitignore`.
- **Logging**: Use the existing `get_chat_logger` pattern. Log every message sent/received with timestamp.
- **Persona files**: Named `grok-{slug}-prompt.txt`. Never overwrite without confirmation.
- **JSON data**: `profile_data.json` is the source of truth for persona metadata.

---

## Vercel Deployment Rules

- `vercel.json` controls routing — update it if new routes are added.
- All file writes at runtime must use `/tmp/` (Vercel filesystem is read-only except `/tmp`).
- Static assets served directly from root via Flask `static_folder=BASE_DIR`.
- Test locally with `python app.py` before pushing.

---

## Feature Development Priorities

When building new features, tackle in this order:

1. **Persona builder UI** — visual form that generates system prompts.
2. **Multi-persona support** — switch between different creator personas.
3. **Conversation analytics** — track funnel stage per fan, conversion events.
4. **Scheduled messages** — drip campaigns to re-engage fans who go quiet.
5. **Content unlock flow** — in-chat PPV/link delivery triggered by funnel stage.

---

## What NOT to Do

- Do not add comments explaining what code does — only add comments when the WHY is non-obvious.
- Do not create new files unless they are genuinely needed.
- Do not add error handling for impossible scenarios.
- Do not build features beyond what the current task requires.
- Do not push to any branch other than `claude/practical-shannon-rs2817`.
- Do not create a pull request unless the user explicitly asks.

---

## Git Workflow

- Branch: `claude/practical-shannon-rs2817`
- Commit messages: imperative, present tense, describe what and why.
- Push with: `git push -u origin claude/practical-shannon-rs2817`
- Retry push up to 4 times on network failure (2s, 4s, 8s, 16s backoff).

---

## Testing Checklist Before Every Push

- [ ] `python app.py` starts without errors
- [ ] Chat sends a message and gets a Gemini response
- [ ] Persona prompt loads correctly from file
- [ ] No API keys or secrets in committed files
- [ ] `vercel.json` routes are valid if changed
