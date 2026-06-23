#!/usr/bin/env python3
"""Terminal chat client — talk to any persona via Gemini.

Usage:
    python chat_cli.py                # default persona (lilith)
    python chat_cli.py aria           # specific persona slug
Type 'exit', 'quit' or 'bye' to leave.
"""
import os
import sys
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PERSONAS_DIR = os.path.join(BASE_DIR, 'personas')
MODEL_NAME = 'gemini-2.5-flash'

# ANSI colors
DIM, MAGENTA, CYAN, RESET = '\033[2m', '\033[35m', '\033[36m', '\033[0m'


def load_prompt(slug):
    for path in (os.path.join(PERSONAS_DIR, f'{slug}.txt'),
                 os.path.join(BASE_DIR, f'grok-{slug}-prompt.txt')):
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return f.read().strip()
    return None


def persona_name(slug):
    import json
    cfg = os.path.join(PERSONAS_DIR, f'{slug}.config.json')
    if os.path.exists(cfg):
        with open(cfg, 'r', encoding='utf-8') as f:
            return json.load(f).get('name', slug.capitalize())
    return slug.capitalize()


def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else 'lilith'
    system_prompt = load_prompt(slug)
    if not system_prompt:
        print(f"No persona found for '{slug}'. Available: "
              f"{[f[:-4] for f in os.listdir(PERSONAS_DIR) if f.endswith('.txt')]}")
        return
    name = persona_name(slug)

    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        print("No GEMINI_API_KEY in .env. Add one (e.g. via the /admin dashboard) and retry.")
        return
    client = genai.Client(api_key=api_key)

    print(f"{MAGENTA}{name}{RESET} {DIM}({slug}) — terminal chat. Type 'exit' to quit.{RESET}\n")

    history = []  # list of {role, parts}
    while True:
        try:
            user_msg = input(f"{CYAN}you ›{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            break
        if user_msg.lower() in ('exit', 'quit', 'bye'):
            print("bye.")
            break
        if not user_msg:
            continue

        history.append({'role': 'user', 'parts': [{'text': user_msg}]})
        try:
            resp = client.models.generate_content(
                model=MODEL_NAME,
                contents=history,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.75,
                    max_output_tokens=1024,
                ),
            )
            reply = (resp.text or '').strip() or "..."
        except Exception as e:
            reply = f"[error: {str(e)[:200]}]"

        history.append({'role': 'model', 'parts': [{'text': reply}]})
        print(f"{MAGENTA}{name} ›{RESET} {reply}\n")


if __name__ == '__main__':
    main()
