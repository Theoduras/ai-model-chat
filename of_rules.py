"""OnlyFans request signing.

Every OnlyFans endpoint worth calling wants a `sign` header, computed from
"dynamic rules" OnlyFans rotates — sometimes several times a day. The rules are
published for free by a handful of people who pull them out of the site's own
JS bundle, so this module fetches them, caches them, and recomputes the header
per request.

The rotation is not announced and cannot be polled for usefully: OnlyFans just
starts answering `400 Please refresh the page`. So the cache is refreshed on
that error and nothing else — `refresh()` after a rejection, then retry once.

Nothing here touches the database or Flask. `cache_hooks()` lets app.py lend it
somewhere durable to keep the rules between Cloud Run instances; without that
it holds them in memory, which is all a test or a script needs.
"""
import hashlib
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from urllib import error as url_error

logger = logging.getLogger(__name__)

# Where the rules come from, best first. Each is a raw JSON document with the
# same shape; a source that 404s or serves something unparseable is skipped.
RULES_SOURCES = (
    'https://raw.githubusercontent.com/DATAHOARDERS/dynamic-rules/main/onlyfans.json',
    'https://raw.githubusercontent.com/deviint/onlyfans-dynamic-rules/main/rules.json',
    'https://raw.githubusercontent.com/rileyllc/onlyfans-dynamic-rules/main/rules.json',
)
RULES_TIMEOUT = 15
# Rules older than this are refetched on the next signature, even without a
# rejection — a stale set that still works is fine, one that stopped working an
# hour ago is an outage nobody reported.
RULES_MAX_AGE = 6 * 3600
# OnlyFans' own web client. The user-agent is part of what the session was
# issued to, so a connected account overrides this with the one it logged in on.
DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

_lock = threading.Lock()
_rules = {}
_fetched_at = 0.0
_load_cache = None
_save_cache = None


class RulesError(RuntimeError):
    """No usable rules could be fetched, so nothing can be signed."""


def cache_hooks(load, save):
    """Lend the module a durable place to keep rules. `load()` returns the JSON
    string it was last given, `save(text)` stores one."""
    global _load_cache, _save_cache
    _load_cache, _save_cache = load, save


def _fetch(url):
    req = urllib.request.Request(url, headers={
        'User-Agent': DEFAULT_USER_AGENT, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=RULES_TIMEOUT) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def _valid(rules):
    return bool(isinstance(rules, dict) and rules.get('static_param')
                and rules.get('format') and rules.get('checksum_indexes')
                and rules.get('app_token'))


def _adopt(rules, source=''):
    global _rules, _fetched_at
    _rules = dict(rules)
    _rules.setdefault('_source', source)
    _fetched_at = time.time()
    if _save_cache:
        try:
            _save_cache(json.dumps({'rules': _rules, 'fetched_at': _fetched_at}))
        except Exception as e:
            logger.warning('could not cache OnlyFans rules: %s', str(e)[:120])
    return _rules


def refresh():
    """Pull a fresh set of rules. Raises RulesError if every source failed."""
    errors = []
    for url in RULES_SOURCES:
        try:
            rules = _fetch(url)
        except (url_error.URLError, ValueError, OSError) as e:
            errors.append(f'{url}: {str(e)[:80]}')
            continue
        if not _valid(rules):
            errors.append(f'{url}: missing fields')
            continue
        logger.info('OnlyFans rules refreshed from %s (revision %s)',
                    url, rules.get('revision') or rules.get('format', '')[:8])
        return _adopt(rules, url)
    raise RulesError('no usable OnlyFans rules: ' + '; '.join(errors))


def _restore():
    """Bring back whatever was cached, so a cold instance signs immediately."""
    global _rules, _fetched_at
    if not _load_cache:
        return False
    try:
        blob = json.loads(_load_cache() or '{}')
    except Exception:
        return False
    rules = blob.get('rules') or {}
    if not _valid(rules):
        return False
    _rules, _fetched_at = rules, float(blob.get('fetched_at') or 0)
    return True


def rules(force=False):
    """The rules to sign with, fetching or restoring them if needed."""
    with _lock:
        if not force and _valid(_rules) and time.time() - _fetched_at < RULES_MAX_AGE:
            return _rules
        if not force and not _valid(_rules) and _restore() and \
                time.time() - _fetched_at < RULES_MAX_AGE:
            return _rules
        try:
            return refresh()
        except RulesError:
            # A stale set beats none: OnlyFans often keeps accepting the
            # previous revision for a while, and the caller retries on refusal.
            if _valid(_rules):
                logger.warning('using stale OnlyFans rules — every source failed')
                return _rules
            raise


def state():
    """What the admin screen shows: which source, how old, which revision."""
    r = _rules if _valid(_rules) else {}
    return {'ready': bool(r), 'source': r.get('_source', ''),
            'revision': str(r.get('revision') or ''),
            'app_token': r.get('app_token', ''),
            'age_seconds': int(time.time() - _fetched_at) if _fetched_at else None}


def _checksum(digest, r):
    """The number the sign header carries alongside the digest.

    Newer rule sets ship `checksum_constants` (one per index) as well as the
    single `checksum_constant`; every working implementation uses the single
    one, so the array is only a fallback for a set that omits it.
    """
    total = sum(digest[i] for i in r['checksum_indexes'])
    constant = r.get('checksum_constant')
    if constant is None:
        constant = sum(r.get('checksum_constants') or [])
    return abs(total + int(constant))


def sign(path, user_id='0', when=None, r=None):
    """The `sign` header for one request.

    `path` is the path *and* query exactly as sent — signing /chats?limit=10 and
    then requesting /chats gets the request rejected. `user_id` is the account's
    own OnlyFans id, '0' before sign-in.
    """
    r = r or rules()
    stamp = str(int(when if when is not None else time.time()))
    msg = '\n'.join([r['static_param'], stamp, path, str(user_id or '0')])
    digest = hashlib.sha1(msg.encode('utf-8')).hexdigest().encode('ascii')
    return r['format'].format(digest.decode(), _checksum(digest, r)), stamp


def headers(path, session=None, when=None):
    """Every header a signed OnlyFans request needs.

    `session` is a connected account: {'cookie', 'x_bc', 'user_agent',
    'user_id'}. Without one the request is signed as a logged-out visitor,
    which is enough to check that the current rules still work.
    """
    r = rules()
    session = session or {}
    user_id = str(session.get('user_id') or '0')
    signature, stamp = sign(path, user_id, when=when, r=r)
    out = {
        'accept': 'application/json, text/plain, */*',
        'app-token': r['app_token'],
        'sign': signature,
        'time': stamp,
        'user-id': user_id,
        'user-agent': session.get('user_agent') or DEFAULT_USER_AGENT,
        'accept-language': 'en-US,en;q=0.9',
        'referer': 'https://onlyfans.com/',
    }
    if r.get('revision'):
        out['x-of-rev'] = str(r['revision'])
    if session.get('cookie'):
        out['cookie'] = session['cookie']
    if session.get('x_bc'):
        out['x-bc'] = session['x_bc']
    # Some revisions tell the client which headers OnlyFans no longer wants;
    # sending one it has retired is itself a tell.
    for name in (r.get('remove_headers') or []):
        out.pop(str(name).lower(), None)
    return out


def path_of(url):
    """The path+query to sign for a full URL."""
    parts = urllib.parse.urlsplit(url)
    return parts.path + (('?' + parts.query) if parts.query else '')


def stale_response(status, body):
    """Whether a failed response means the rules rotated under us.

    OnlyFans answers a bad signature with a 400 whose body asks the user to
    refresh the page — the same 400 it uses for a genuinely malformed request,
    hence matching on the text rather than the status alone.
    """
    if status not in (400, 401):
        return False
    text = (body or '')
    if isinstance(text, bytes):
        text = text.decode('utf-8', 'replace')
    text = text.lower()
    return ('refresh the page' in text or 'please refresh' in text
            or 'invalid sign' in text)


if os.getenv('ONLYFANS_RULES_SOURCE'):
    RULES_SOURCES = ((os.environ['ONLYFANS_RULES_SOURCE'],) +
                     tuple(u for u in RULES_SOURCES
                           if u != os.environ['ONLYFANS_RULES_SOURCE']))
