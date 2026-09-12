"""OnlyFans request signing.

Every OnlyFans endpoint worth calling wants a `sign` header, computed from
"dynamic rules" OnlyFans rotates — sometimes several times a day. The rules are
published for free by a handful of people who pull them out of the site's own
JS bundle, so this module fetches them, caches them, and recomputes the header
per request.

The rotation is not announced and cannot be polled for usefully: OnlyFans just
starts answering `400 Please refresh the page`. So the cache is refreshed on
that error and nothing else — `refresh()` after a rejection, then retry.

A source can also simply fall behind: two of these repos publish at different
times, and one carrying last week's static_param is structurally perfect and
rejected by every request. So a refresh chasing a rejection is told which set
was rejected and refuses to adopt that same set again — otherwise refetching
from the one stale source returns the identical rules and the retry cannot
possibly succeed.

Nothing here touches the database or Flask. `cache_hooks()` lets app.py lend it
somewhere durable to keep the rules between Cloud Run instances; without that
it holds them in memory, which is all a test or a script needs.
"""
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from urllib import error as url_error

logger = logging.getLogger(__name__)

# Where the rules come from, best first. Each is a raw JSON document with the
# same shape; a source that 404s or serves something unparseable is skipped.
RULES_SOURCES = (
    'https://raw.githubusercontent.com/DIGITALCRIMINALS/dynamic-rules/main/onlyfans.json',
    'https://raw.githubusercontent.com/DATAHOARDERS/dynamic-rules/main/onlyfans.json',
)
RULES_TIMEOUT = 15
# Rules older than this are refetched on the next signature, even without a
# rejection — a stale set that still works is fine, one that stopped working an
# hour ago is an outage nobody reported.
RULES_MAX_AGE = 6 * 3600
# A refresh that failed fails the same way for every caller, so retrying it per
# request only fills the log while the cached set keeps working.
REFRESH_RETRY_AFTER = 60
_refresh_failed_at = [0.0]
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
_load_override = None
_load_sample = None
_save_sample = None
_sample_cache = (0.0, {})
# The oracle is consulted on the way to every signature, so it is held briefly
# rather than read from the database each time. It only changes on a connect.
SAMPLE_TTL = 60


class RulesError(RuntimeError):
    """No usable rules could be fetched, so nothing can be signed."""


def cache_hooks(load, save):
    """Lend the module a durable place to keep rules. `load()` returns the JSON
    string it was last given, `save(text)` stores one."""
    global _load_cache, _save_cache
    _load_cache, _save_cache = load, save


def override_hooks(load):
    """Lend the module an operator-set rule set. `load()` returns the JSON an
    operator pasted into the console, or ''. It wins over every published
    source: it is the escape hatch for a rotation the mirrors have not caught."""
    global _load_override
    _load_override = load


def sample_hooks(load, save):
    """Lend the module somewhere to keep the signature oracle."""
    global _load_sample, _save_sample, _sample_cache
    _load_sample, _save_sample = load, save
    _sample_cache = (0.0, {})


def override():
    """The operator's rule set, if one is stored and usable."""
    if not _load_override:
        return {}
    try:
        r = json.loads(_load_override() or '{}')
    except (ValueError, TypeError):
        return {}
    return r if _valid(r) else {}


def sample():
    """One request OnlyFans' own page signed: {path, time, user_id, sign}.

    This is the oracle. Because we know what the correct signature for that
    exact request was, any candidate rule set can be checked against it offline
    — no live request, no waiting to be refused.
    """
    global _sample_cache
    if not _load_sample:
        return {}
    at, cached = _sample_cache
    if time.time() - at < SAMPLE_TTL:
        return cached
    try:
        s = json.loads(_load_sample() or '{}')
    except (ValueError, TypeError):
        s = {}
    if not (isinstance(s, dict) and s.get('sign') and s.get('path')):
        s = {}
    _sample_cache = (time.time(), s)
    return s


def put_sample(s):
    """Keep a freshly captured sample. Carries no credentials, so it is stored
    as it came."""
    if not (_save_sample and isinstance(s, dict) and s.get('sign') and s.get('path')):
        return {}
    kept = {k: s.get(k) for k in ('path', 'time', 'user_id', 'sign', 'app_token')
            if s.get(k) not in (None, '')}
    global _sample_cache
    _save_sample(json.dumps(kept))
    _sample_cache = (time.time(), kept)
    return kept


def sample_age():
    """Seconds since the oracle was captured, or None."""
    s = sample()
    try:
        return max(0, int(time.time() - int(s['time']) / (1000 if len(s['time']) > 11 else 1)))
    except (KeyError, ValueError, TypeError):
        return None


def format_of(s):
    """The format string a captured signature was built with.

    A sign reads `<prefix>:<sha1>:<hex checksum>:<suffix>`, so the format is
    sitting in plain view in every sample — no need to trust a published set to
    still have the right one.
    """
    parts = str((s or {}).get('sign') or '').split(':')
    if len(parts) != 4:
        return ''
    return f'{parts[0]}:{{}}:{{:x}}:{parts[3]}'


def verify(s, r):
    """Does `r` reproduce the signature in sample `s`? None when unanswerable."""
    if not (s and s.get('sign') and _valid(r)):
        return None
    try:
        got, _ = sign(s['path'], s.get('user_id') or '0', when=int(s['time']), r=r)
    except (KeyError, ValueError, TypeError, IndexError):
        return None
    return got == s['sign']


def proven():
    """Do the rules in use reproduce OnlyFans' own signature? True/False/None.

    The one question worth asking before believing a 400: a set that
    reproduces a signature OnlyFans itself produced cannot be the reason
    OnlyFans refused a request, whatever the body says.
    """
    return verify(sample(), _rules)


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


def label_of(source):
    """A name for a rule source that means something to whoever is reading it.

    The published sets come from community repositories whose names are their
    own business but say nothing about what the set is, and reading one in the
    console would only make a creator wonder what her chat app is mixed up in.
    Position is the honest description: these are interchangeable mirrors of
    the same thing, tried in order.
    """
    if source == 'override':
        return 'Pasted in the console'
    if source == 'cached':
        return 'In use now'
    for i, url in enumerate(RULES_SOURCES):
        if url == source:
            return f'Published set {i + 1}'
    return 'Published set' if str(source).startswith('http') else str(source or '')


def fingerprint(rules=None):
    """What identifies one revision of the rules, for saying "not that set
    again". The static_param and format are what a signature is actually built
    from, so two sets agreeing on both sign identically."""
    r = _rules if rules is None else rules
    if not _valid(r):
        return ''
    return f"{r['static_param']}|{r['format']}"


def refresh(reject=()):
    """Pull a fresh set of rules. Raises RulesError if every source failed.

    `reject` is a fingerprint, or a collection of them, that OnlyFans has just
    refused. A source still serving one of those sets is skipped rather than
    adopted: it is a set we already know does not work, and taking it would end
    the caller's retry before a source that has caught up gets a turn.
    """
    reject = {reject} if isinstance(reject, str) else set(reject or ())
    reject.discard('')
    want = sample()
    errors, fallback = [], None
    for url in ('override',) + tuple(RULES_SOURCES):
        try:
            rules = override() if url == 'override' else _fetch(url)
        except (url_error.URLError, ValueError, OSError) as e:
            errors.append(f'{url}: {str(e)[:80]}')
            continue
        if not _valid(rules):
            if url != 'override':
                errors.append(f'{url}: missing fields')
            continue
        if fingerprint(rules) in reject:
            errors.append(f'{url}: still serving the revision OnlyFans rejected')
            continue
        # With an oracle there is nothing to guess at: a set that reproduces a
        # signature OnlyFans' own page produced is the current one, and a set
        # that cannot is not worth a request.
        proven = verify(want, rules)
        if proven:
            logger.info('OnlyFans rules from %s match the captured signature', url)
            return _adopt(rules, url)
        if proven is False:
            errors.append(f'{url}: does not match the signature OnlyFans last produced')
            continue
        if fallback is None:
            fallback = (rules, url)
    if fallback:
        rules, url = fallback
        logger.info('OnlyFans rules refreshed from %s (revision %s)',
                    url, rules.get('revision') or rules.get('format', '')[:8])
        return _adopt(rules, url)
    raise RulesError(('every published rule set fails the captured signature'
                      if want else 'no usable OnlyFans rules')
                     + ': ' + '; '.join(errors))


def compare():
    """Every source, what revision it serves, and what the oracle says of it.

    `refresh` reports this as a sentence in an exception. Here it is the table
    behind that sentence, so a disagreement can be read rather than inferred.
    """
    want = sample()
    out = []
    for url in ('override',) + tuple(RULES_SOURCES):
        row = {'source': url}
        try:
            rules = override() if url == 'override' else _fetch(url)
        except Exception as e:
            row['error'] = str(e)[:120]
            out.append(row)
            continue
        if not _valid(rules):
            row['error'] = 'missing fields'
            out.append(row)
            continue
        row['revision'] = str(rules.get('revision') or '')
        row['app_token'] = rules.get('app_token') or ''
        row['fingerprint'] = fingerprint(rules)
        row['verify'] = verify(want, rules)
        out.append(row)
    return out


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
        # A cached set the oracle disproves is worse than no set: it will be
        # refused on every request until something forces a refetch.
        # Only a caller asking for it outright ignores the cooldown below: the
        # oracle disproving the cached set is exactly the case that repeated per
        # request, and forcing it here would walk straight past the cooldown.
        if not force and verify(sample(), _rules) is False:
            if _valid(_rules) and time.time() - _refresh_failed_at[0] < REFRESH_RETRY_AFTER:
                return _rules
            force = True
        if not force and _valid(_rules) and time.time() - _fetched_at < RULES_MAX_AGE:
            return _rules
        if not force and not _valid(_rules) and _restore() and \
                time.time() - _fetched_at < RULES_MAX_AGE:
            return _rules
        if not force and _valid(_rules) and \
                time.time() - _refresh_failed_at[0] < REFRESH_RETRY_AFTER:
            return _rules
        try:
            out = refresh()
            _refresh_failed_at[0] = 0.0
            return out
        except RulesError:
            _refresh_failed_at[0] = time.time()
            # A stale set beats none: OnlyFans often keeps accepting the
            # previous revision for a while, and the caller retries on refusal.
            if _valid(_rules):
                logger.warning('using stale OnlyFans rules — every source failed')
                return _rules
            raise


def state():
    """What the admin screen shows: which source, how old, which revision."""
    r = _rules if _valid(_rules) else {}
    return {'ready': bool(r), 'source': label_of(r.get('_source', '')),
            'revision': str(r.get('revision') or r.get('format', '').split(':')[0]),
            'app_token': r.get('app_token', ''),
            'age_seconds': int(time.time() - _fetched_at) if _fetched_at else None,
            'verified': verify(sample(), r), 'has_sample': bool(sample()),
            'override': bool(override())}


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


# A static_param is a long opaque literal in the site's own bundle. Nothing
# marks it as one, so every literal of about the right shape is a candidate and
# the oracle says which is right — brute force with a perfect checker, rather
# than trying to understand minified code.
_LITERAL_RE = re.compile(r'[\'"]([A-Za-z0-9+/=_-]{16,64})[\'"]')


def solve(bundle, s=None, bases=None):
    """Recover the rules from OnlyFans' own JS, given a sample to check against.

    The format comes from the sample itself. What cannot be read off a signature
    is the checksum recipe, so each known set's indexes and constant are tried
    in turn: a rotation of the static_param alone — the usual one — is then
    recovered outright. Returns the working set, or {}.
    """
    s = s or sample()
    fmt = format_of(s)
    if not (s and fmt):
        return {}
    bases = [b for b in (bases or [_rules, override()]) if b and b.get('checksum_indexes')]
    if not bases:
        return {}
    candidates, seen = [], set()
    for match in _LITERAL_RE.finditer(bundle or ''):
        value = match.group(1)
        if value not in seen:
            seen.add(value)
            candidates.append(value)
    for base in bases:
        for value in candidates:
            trial = dict(base, static_param=value, format=fmt)
            if verify(s, trial):
                logger.info('recovered the OnlyFans static_param from the page bundle')
                return trial
    return {}


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
