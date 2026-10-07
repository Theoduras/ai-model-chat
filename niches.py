"""Niche finder: which Fanvue/OnlyFans niches are worth competing in.

A hand-built seed list, re-ranked weekly by an LLM that reads a few public
directory pages. Fanvue and OnlyFans themselves are never fetched: scraping
them is against their terms, and the directories already aggregate them.
The ranked list is one JSON blob in app settings, so every host reads the
same answer and a failed refresh leaves last week's ranking in place.
"""
import json
import os
import re
import urllib.request
from datetime import datetime, timezone

SETTING_KEY = 'niche_rankings'

# (slug, name, group). No age-leaning niches ("teen", "18 years old",
# "schoolgirl"): the character builder refuses youth-leaning looks, so the
# finder must never send a creator towards one.
SEED_NICHES = [
    ('girl-next-door', 'Girl next door', 'Persona'),
    ('gamer-girl', 'Gamer girl', 'Persona'),
    ('cosplay', 'Cosplay', 'Persona'),
    ('waifu-anime', 'Waifu / anime', 'Persona'),
    ('goth-alt', 'Goth / alt', 'Persona'),
    ('egirl', 'E-girl', 'Persona'),
    ('nerdy', 'Nerdy / bookish', 'Persona'),
    ('cheerleader', 'Cheerleader', 'Persona'),
    ('nurse-roleplay', 'Nurse / uniform roleplay', 'Persona'),
    ('office-boss', 'Office / boss lady', 'Persona'),
    ('milf', 'MILF', 'Persona'),
    ('bimbo', 'Bimbo', 'Persona'),
    ('tiktoker', 'TikToker / influencer', 'Persona'),
    ('fitness', 'Fitness / gym', 'Lifestyle'),
    ('yoga', 'Yoga / flexible', 'Lifestyle'),
    ('travel', 'Travel / beach', 'Lifestyle'),
    ('country-girl', 'Country girl', 'Lifestyle'),
    ('luxury', 'Luxury / sugar', 'Lifestyle'),
    ('latina', 'Latina', 'Look'),
    ('asian', 'Asian', 'Look'),
    ('korean', 'Korean', 'Look'),
    ('ebony', 'Ebony', 'Look'),
    ('redhead', 'Redhead', 'Look'),
    ('blonde', 'Blonde', 'Look'),
    ('tattooed', 'Tattooed', 'Look'),
    ('petite', 'Petite', 'Look'),
    ('curvy', 'Curvy / thick', 'Look'),
    ('bbw', 'BBW', 'Look'),
    ('big-tits', 'Big tits', 'Look'),
    ('freckles', 'Freckles', 'Look'),
    ('lingerie', 'Lingerie', 'Content'),
    ('asmr', 'ASMR', 'Content'),
    ('joi', 'JOI', 'Content'),
    ('pov', 'POV', 'Content'),
    ('rating', 'Dick rating', 'Content'),
    ('girlfriend-experience', 'Girlfriend experience (GFE)', 'Content'),
    ('feet', 'Feet', 'Fetish'),
    ('femdom', 'Femdom / dominatrix', 'Fetish'),
    ('submissive', 'Submissive', 'Fetish'),
    ('findom', 'Findom', 'Fetish'),
    ('latex', 'Latex / PVC', 'Fetish'),
    ('kink', 'Kink / BDSM', 'Fetish'),
    ('massage', 'Massage', 'Fetish'),
    ('couple', 'Couple', 'Fetish'),
]

_BLOCKED = re.compile(r'\b(teen|teens|18 ?y|young|barely|schoolgirl|school ?girl|'
                      r'student|loli|petite ?teen|little|underage|minor)\b', re.I)

SOURCES = [s.strip() for s in (os.getenv('NICHE_SOURCES') or ','.join([
    'https://fanvuemodels.com/',
    'https://fanvuemodels.com/best/top-fanvue-models',
    'https://fanvuemodels.com/featured-fanvue-creators',
    'https://onlyfinder.com/',
])).split(',') if s.strip()]

_TREND = {'rising': 100, 'flat': 50, 'falling': 0}


def _clamp(v):
    try:
        return max(0, min(100, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0


def opportunity(row):
    """One score to sort by. Demand over supply weighs most: a popular niche
    nobody can break into is worth less than a smaller, under-served one."""
    return _clamp(0.35 * row['gap'] + 0.25 * row['popularity']
                  + 0.25 * row['monetization'] + 0.15 * _TREND.get(row['trend'], 50))


def _fetch_text(url, limit=6000):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (niche-finder)'})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read(600_000).decode('utf-8', 'replace')
    raw = re.sub(r'(?is)<(script|style|noscript)\b.*?</\1>', ' ', raw)
    text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', raw))
    return text[:limit]


def _grok_text(prompt):
    """xAI's chat completions (OpenAI-compatible). None when no key is set."""
    key = (os.getenv('XAI_API_KEY') or '').strip()
    if not key:
        return None
    body = json.dumps({
        'model': os.getenv('XAI_MODEL') or 'grok-4',
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.3,
        'response_format': {'type': 'json_object'},
    }).encode()
    req = urllib.request.Request('https://api.x.ai/v1/chat/completions', data=body, headers={
        'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    return data['choices'][0]['message']['content']


def _prompt(source_text):
    seed = '\n'.join(f'- {slug}: {name} ({group})' for slug, name, group in SEED_NICHES)
    return f"""You rank creator niches on Fanvue and OnlyFans for someone launching an AI model.

Niches to rank (slug: name (group)):
{seed}

You may add up to 8 extra niches you see rising in the sources, with a new kebab-case slug.
Never add or rank anything age-leaning (teen, young, schoolgirl, student).

Public directory pages, read today (may be partial):
{source_text or '(no source text could be fetched; use your own knowledge)'}

For every niche give integers 0-100:
- popularity: fan demand and search interest
- saturation: how crowded it is with creators
- gap: how far demand outruns supply (high = under-served)
- monetization: how well fans in it pay (PPV, tips, customs)
and trend: "rising", "flat" or "falling", plus why: one plain sentence a creator would act on.

Answer with JSON only: {{"niches": [{{"slug": "...", "name": "...", "group": "...",
"popularity": 0, "saturation": 0, "gap": 0, "monetization": 0, "trend": "flat", "why": "..."}}]}}"""


def _parse(text):
    text = (text or '').strip()
    m = re.search(r'\{.*\}', text, re.S)
    data = json.loads(m.group(0) if m else text)
    rows = data.get('niches') if isinstance(data, dict) else data
    seed = {slug: (name, group) for slug, name, group in SEED_NICHES}
    out, seen, extra = [], set(), 0
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        slug = re.sub(r'[^a-z0-9-]+', '-', str(r.get('slug') or '').lower()).strip('-')
        name = str(r.get('name') or '').strip()[:60]
        if not slug or slug in seen or _BLOCKED.search(slug + ' ' + name):
            continue
        if slug not in seed:
            if extra >= 8:
                continue
            extra += 1
        name, group = seed.get(slug, (name or slug, str(r.get('group') or 'Other')[:30]))
        trend = str(r.get('trend') or 'flat').lower()
        row = {'slug': slug, 'name': name, 'group': group,
               'popularity': _clamp(r.get('popularity')),
               'saturation': _clamp(r.get('saturation')),
               'gap': _clamp(r.get('gap')),
               'monetization': _clamp(r.get('monetization')),
               'trend': trend if trend in _TREND else 'flat',
               'why': str(r.get('why') or '').strip()[:280],
               'new': slug not in seed}
        row['score'] = opportunity(row)
        out.append(row)
        seen.add(slug)
    if len(out) < len(SEED_NICHES) // 2:
        raise ValueError(f'ranking came back with {len(out)} niches')
    return sorted(out, key=lambda r: -r['score'])


def rank(fallback_llm=None):
    """Fetch the sources and ask Grok (or the fallback) to rank. Returns the
    blob that refresh() stores; raises when no ranking could be produced."""
    texts, used = [], []
    for url in SOURCES:
        try:
            texts.append(f'[{url}]\n{_fetch_text(url)}')
            used.append(url)
        except Exception:
            continue
    prompt = _prompt('\n\n'.join(texts))
    model = None
    try:
        reply = _grok_text(prompt)
        if reply:
            model = os.getenv('XAI_MODEL') or 'grok-4'
    except Exception:
        reply = None
    if not reply:
        if fallback_llm is None:
            raise RuntimeError('No XAI_API_KEY and no fallback model')
        reply, model = fallback_llm(prompt), 'gemini'
    return {'niches': _parse(reply), 'sources': used, 'model': model,
            'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}


def refresh(session, fallback_llm=None):
    from db import set_app_setting
    blob = rank(fallback_llm)
    set_app_setting(session, SETTING_KEY, json.dumps(blob))
    return blob


def current(session):
    from db import get_app_setting
    raw = get_app_setting(session, SETTING_KEY)
    if raw:
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return {'niches': [{'slug': s, 'name': n, 'group': g} for s, n, g in SEED_NICHES],
            'sources': [], 'model': None, 'updated_at': None}
