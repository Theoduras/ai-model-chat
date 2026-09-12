"""OnlyFans transport — direct, self-hosted.

Talks to onlyfans.com's own endpoints with a session the creator gave us and a
signature from `of_rules`, replacing the paid OnlyFansAPI middleman. The public
surface is deliberately the same as `onlyfans.py`'s so the platform adapter in
app.py can be pointed at either one.

Two things make a request survive here. Rules rotate without warning, so a
rejection that says "please refresh the page" refetches them and retries once.
And OnlyFans watches pacing, so every account has its own token bucket and its
own exit IP — the same one the session was created on.

The payload readers (what a message says, who sent it, what a vault item is)
are shared with `onlyfans.py`: OnlyFansAPI passes OnlyFans' own objects through
untouched, so both transports are reading the same shapes. They move here when
the middleman goes.
"""
import json
import logging
import os
import random
import threading
import time
import urllib.parse
import urllib.request
from urllib import error as url_error

import of_rules
import of_session
from onlyfans import (OF_PRICE_MAX_USD, OF_PRICE_MIN_USD, chat_online,  # noqa: F401
                      chat_unsendable, direction_of, event_fan, event_handle,
                      media_row, media_thumb, msg_age_minutes, msg_id, msg_time,
                      ppv_amount_cents, strip_html, text_of, user_of_chat,
                      verify_signature)

logger = logging.getLogger(__name__)

OF_BASE = 'https://onlyfans.com'
OF_TIMEOUT = 25
OF_PAGE_LIMIT = 50
OF_MAX_PAGES = 20
# The pacing OnlyFans tolerates. One request every two seconds per account,
# jittered, is well under what a creator with the app open produces.
OF_MIN_INTERVAL = float(os.getenv('ONLYFANS_MIN_INTERVAL', '2.0') or 2.0)
OF_JITTER = 0.6
OF_RETRY_STATUSES = (429, 500, 502, 503, 504)
OF_MAX_RETRIES = 3
# How long an account is left alone after a refusal it cannot do anything about.
# Long enough that a watcher stops making the same rejected request every minute,
# short enough that a rotation caught upstream is picked up within the hour.
OF_SIGNING_COOLDOWN = float(os.getenv('ONLYFANS_SIGNING_COOLDOWN', '300') or 300)


class OnlyFansError(RuntimeError):
    """A refusal from OnlyFans. `code` is the HTTP status, `detail` what it said."""

    def __init__(self, code, detail=''):
        self.code = code
        self.detail = detail
        super().__init__(f'OnlyFans {code}: {detail}'.strip())


class SessionExpired(OnlyFansError):
    """The session is no longer valid — the creator has to reconnect."""


class SigningStale(OnlyFansError):
    """OnlyFans refused the signature and no published rule set signs any
    better. The account is fine and reconnecting it would change nothing: the
    free rule sources have fallen behind OnlyFans' current rotation."""


class SignatureRefused(OnlyFansError):
    """OnlyFans refused a request that was signed with rules its own page
    proves are current. The signature is not the problem, so refetching rules
    would change nothing: what is left is who the request says it is — the
    cookie, x-bc, or the user id we sign with."""


# ── Pacing ────────────────────────────────────────────────────────────────────

_buckets = {}
_bucket_lock = threading.Lock()


def _wait_turn(account):
    """Hold the caller until this account is allowed another request."""
    with _bucket_lock:
        last = _buckets.get(account, 0.0)
        gap = OF_MIN_INTERVAL + random.uniform(0, OF_JITTER)
        due = last + gap
        now = time.time()
        _buckets[account] = max(now, due)
        delay = due - now
    if delay > 0:
        time.sleep(delay)


def _opener(proxy):
    """A URL opener pinned to this account's exit IP. Every request an account
    makes has to leave from the address its session was created on — a session
    that suddenly appears from a datacentre is what gets accounts flagged."""
    # A session stores the proxy it was created on, so turning the pool off has
    # to reach the sessions already in the vault too: without a template
    # configured there is no pool, and a stored address would send the account
    # through a gateway nobody is paying for any more.
    if not (os.getenv('ONLYFANS_PROXY_TEMPLATE') or '').strip():
        proxy = ''
    if not proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))


# ── Holding off ───────────────────────────────────────────────────────────────
# Two refusals cannot be argued with: rules no published source signs any better,
# and a signature OnlyFans refuses while its own page proves those rules current.
# Both answer every later request the same way, so a watcher that keeps polling
# only lands a rejected request a minute against an account OnlyFans is already
# unhappy with. The first refusal is kept and the rest are raised from here,
# without a request, until something that could change the answer has: rules that
# rotated under us, a session reconnected, or the cooldown running out.

_held = {}
_held_lock = threading.Lock()


def _hold(account, session, err):
    with _held_lock:
        _held[account] = (time.time(), of_rules.fingerprint(),
                          session.get('connected_at'), err)


def _holding(account, session):
    """The refusal still standing for this account, or None."""
    with _held_lock:
        held = _held.get(account)
        if not held:
            return None
        at, fingerprint, connected_at, err = held
        left = OF_SIGNING_COOLDOWN - (time.time() - at)
        if (left <= 0 or of_rules.fingerprint() != fingerprint
                or session.get('connected_at') != connected_at):
            del _held[account]
            return None
    return type(err)(err.code, f'{err.detail} — not asking OnlyFans again for '
                               f'another {int(left)}s')


def resume(account=''):
    """Try an account again now, whatever the clock says. One that reconnected
    or a rule set an operator just pasted end the hold on their own; this is for
    an operator who wants to know straight away."""
    with _held_lock:
        if account:
            _held.pop(account, None)
        else:
            _held.clear()


def held():
    """Which accounts are being held back, and for how much longer."""
    with _held_lock:
        return {account: max(0, int(OF_SIGNING_COOLDOWN - (time.time() - at)))
                for account, (at, _, _, _) in _held.items()}


# ── Requests ──────────────────────────────────────────────────────────────────

def _once(account, method, path, body, session):
    url = OF_BASE + path
    headers = of_rules.headers(path, session)
    if body is not None:
        headers['content-type'] = 'application/json'
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener(session.get('proxy')).open(req, timeout=OF_TIMEOUT) as r:
            raw = r.read()
        return json.loads(raw.decode('utf-8', 'replace')) if raw else {}
    except url_error.HTTPError as e:
        raise OnlyFansError(e.code, e.read().decode('utf-8', 'replace')[:500])
    except url_error.URLError as e:
        raise OnlyFansError(0, str(getattr(e, 'reason', e))[:200])
    except ValueError:
        raise OnlyFansError(0, 'OnlyFans returned something that was not JSON')


def _adopt_working_rules(rejected):
    """Move on to a rule set OnlyFans has not just refused.

    Refetching alone is not enough: the sources publish independently, so the
    one that answered first may keep serving the very revision that was
    rejected, and adopting it again would burn the retry for nothing. If no
    source has anything else, the signature cannot be fixed here and the caller
    is told that rather than being sent to reconnect a healthy account.
    """
    try:
        fresh = of_rules.refresh(reject=rejected)
    except of_rules.RulesError as e:
        # Reason first: this reaches the console through a 160-character trace,
        # and the reason is the whole point of the message.
        logger.warning('OnlyFans signing is stuck: %s', e)
        raise SigningStale(400, 'no rule set signs what OnlyFans will accept — '
                                f'{str(e)[:200]}') from None
    fresh_fp = of_rules.fingerprint(fresh)
    if fresh_fp and fresh_fp in rejected:
        raise SigningStale(400, 'every published rule set is the one OnlyFans just '
                                'rejected, so the free dynamic rules are behind its '
                                'current rotation. Paste a current set in the console '
                                '— the account itself is still connected.')
    logger.info('OnlyFans rules rotated, signing with %s', fresh.get('_source', ''))


def _repair_identity(account, session):
    """A last look at who this session is, when the signature is not at fault.

    A session stored through the connect fallback carries an id read out of the
    `auth_id` cookie, taken at a moment when our own /users/me could not be
    signed. Now that the rules are proven, that request works, so ask it once:
    a different id is the bug and is worth fixing in place, and no answer at all
    means the session really is dead. Returns True when the caller should retry.
    """
    _wait_turn(account)
    try:
        who = _once(account, 'GET', '/api2/v2/users/me', None, session)
    except OnlyFansError as e:
        if of_rules.stale_response(e.code, e.detail):
            # Refused the same way as the call that got us here, so it is not
            # this session that OnlyFans objects to -- something every request
            # carries is wrong, and sending the creator to reconnect would cost
            # her the session she has for nothing.
            logger.warning('OnlyFans refuses even /users/me while the rules are '
                           'proven current — not blaming the session for %s', account)
            return False
        of_session.mark_expired(account, 'OnlyFans refused this session while the '
                                         'signing rules were current: ' + str(e.detail)[:120])
        return False
    if not (isinstance(who, dict) and who.get('id')):
        of_session.mark_expired(account, 'OnlyFans did not say who this session is')
        return False
    of_session.update(account, verified=True, user_id=str(who['id']))
    if str(who['id']) == str(session.get('user_id') or ''):
        return False
    logger.warning('OnlyFans session for %s had the wrong user id (%s, really %s) — fixed',
                   account, session.get('user_id'), who['id'])
    session['user_id'] = str(who['id'])
    return True


def call(account, method, path, body=None):
    """One request as `account`, signed, paced, and retried where retrying helps.

    A rules rotation and a rate limit both come back as failures that a second
    attempt fixes; a dead session does not, so it is raised as its own error and
    the account is marked so the creator gets told. A refusal that no retry and
    no reconnection would change holds the account back for a while rather than
    being collected again on every poll.
    """
    session = of_session.get(account)
    if not session:
        raise OnlyFansError(0, f'no OnlyFans session for {account}')
    standing = _holding(account, session)
    if standing:
        raise standing
    if not path.startswith('/'):
        path = '/' + path
    try:
        return _attempts(account, method, path, body, session)
    except (SigningStale, SignatureRefused) as e:
        _hold(account, session, e)
        raise


def _attempts(account, method, path, body, session):
    """The request and everything worth retrying it for."""
    rejected = set()
    proven_retried = False
    for attempt in range(OF_MAX_RETRIES):
        _wait_turn(account)
        try:
            return _once(account, method, path, body, session)
        except OnlyFansError as e:
            # A rotated signature and a dead session both come back as a 401,
            # and only the response text tells them apart -- "please refresh
            # the page" is the rotation, not a revoked session. Check that
            # first, or the retry that would have fixed it never gets a turn.
            if of_rules.stale_response(e.code, e.detail):
                # The oracle outranks the body text. A set that reproduces a
                # signature OnlyFans' own page produced cannot be what OnlyFans
                # is refusing -- and treating it as such poisons the one correct
                # set, so every later refresh skips it and the account looks
                # broken forever.
                if of_rules.proven() is True:
                    if attempt < OF_MAX_RETRIES - 1 and not proven_retried:
                        proven_retried = True
                        continue
                    if _repair_identity(account, session):
                        continue
                    raise SignatureRefused(
                        e.code, 'the signing rules reproduce OnlyFans\' own signature, '
                                'so this is not a rotation — OnlyFans is refusing this '
                                'account\'s session. Reconnect the account.') from None
                rejected.add(of_rules.fingerprint())
                _adopt_working_rules(rejected)
                continue
            if e.code == 401 or (e.code == 403 and 'sign' not in e.detail.lower()):
                of_session.mark_expired(account, e.detail)
                raise SessionExpired(e.code, 'the OnlyFans session expired — '
                                             'reconnect the account')
            if e.code in OF_RETRY_STATUSES and attempt < OF_MAX_RETRIES - 1:
                time.sleep((2 ** attempt) + random.uniform(0, 1))
                continue
            raise
    raise OnlyFansError(0, 'gave up after retrying')


def rows(res):
    """The list inside a response, whatever it is wrapped in."""
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        for k in ('list', 'items', 'chats', 'messages', 'data'):
            v = res.get(k)
            if isinstance(v, list):
                return v
    return []


def paged(account, path, want=None, max_pages=OF_MAX_PAGES):
    """Every row of a collection, offset by offset.

    OnlyFans pages with `offset` and tells us when it is done with hasMore, so
    unlike a cursor API the loop has to count for itself — and stop when a page
    adds nothing, or a hiccup would spin it to max_pages.
    """
    out, seen, offset = [], set(), 0
    for _ in range(max_pages):
        sep = '&' if '?' in path else '?'
        res = call(account, 'GET', f'{path}{sep}offset={offset}')
        batch = rows(res)
        if not batch:
            break
        added = 0
        for row in batch:
            rid = str(row.get('id') or '') if isinstance(row, dict) else ''
            if rid and rid in seen:
                continue
            if rid:
                seen.add(rid)
            out.append(row)
            added += 1
        if not added:
            break
        if want is not None and len(out) >= want:
            return out[:want]
        if isinstance(res, dict) and res.get('hasMore') is False:
            break
        offset += len(batch)
    return out


# ── Endpoints ─────────────────────────────────────────────────────────────────

def me(account):
    """Who this session is. Also the health check: it fails exactly when the
    session has died, and costs one cheap request."""
    return call(account, 'GET', '/api2/v2/users/me')


def check(account):
    """(alive, detail). Never raises for an expired session — telling the
    dashboard a session is dead is this function's whole job."""
    try:
        who = me(account)
    except SessionExpired as e:
        return False, e.detail
    except SigningStale as e:
        # Deliberately not marked expired: the creator reconnecting would hand
        # us the same good session and the same unsignable request.
        return False, e.detail
    except OnlyFansError as e:
        return False, str(e)[:200]
    if not (isinstance(who, dict) and who.get('id')):
        of_session.mark_expired(account, 'OnlyFans did not return an account')
        return False, 'OnlyFans did not return an account'
    of_session.mark_live(account)
    return True, str(who.get('username') or '')


def chats(account, unread_only=False, want=None):
    q = f'?limit={OF_PAGE_LIMIT}&order=recent'
    if unread_only:
        q += '&filter=unread'
    return paged(account, '/api2/v2/chats' + q, want=want)


def messages(account, chat_id, want=20):
    """The `want` most recent messages in one chat, oldest last.

    Reading a chat marks it read on OnlyFans, so this is called for chats the
    round is about to answer — never as a way to poll for new ones.
    """
    msgs = paged(account,
                 f'/api2/v2/chats/{chat_id}/messages?limit={min(want, OF_PAGE_LIMIT)}'
                 f'&order=desc',
                 want=want, max_pages=max(1, -(-want // OF_PAGE_LIMIT)))
    return sorted(msgs, key=lambda m: msg_time(m))


def send(account, chat_id, text, price=0, media=(), idem=None):
    """Send one message. A price needs media on it — OnlyFans rejects a paid
    message with nothing locked behind it."""
    body = {'text': (text or '')[:2000], 'lockedText': False,
            'mediaFiles': [], 'previews': [], 'isCouplePeopleMedia': False}
    media = [m for m in (media or []) if m not in (None, '')]
    if media:
        body['mediaFiles'] = [int(m) if str(m).isdigit() else m for m in media]
    if price:
        if not media:
            raise OnlyFansError(0, 'a paid message needs at least one media file')
        usd = round(float(price), 2)
        if usd < OF_PRICE_MIN_USD or usd > OF_PRICE_MAX_USD:
            raise OnlyFansError(0, f'OnlyFans will not take a ${usd:g} unlock — the '
                                   f'price has to be between ${OF_PRICE_MIN_USD:g} '
                                   f'and ${OF_PRICE_MAX_USD:g}')
        body['price'] = usd
    return call(account, 'POST', f'/api2/v2/chats/{chat_id}/messages', body=body)


def typing(account, chat_id):
    """Show the typing indicator. It lasts a few seconds, so it is called again
    while the reply is still being paced out."""
    return call(account, 'POST', f'/api2/v2/chats/{chat_id}/typing')


def read_receipt(account, chat_id, last_read_id):
    return call(account, 'POST', f'/api2/v2/chats/{chat_id}/read',
                body={'lastReadMessageId': int(last_read_id)})


def vault(account, list_id=None, media_type=None, want=200, query=''):
    q = {'limit': OF_PAGE_LIMIT, 'field': 'recent'}
    if list_id:
        q['list'] = list_id
    if media_type:
        q['type'] = media_type
    if query:
        q['query'] = query
    return paged(account, '/api2/v2/vault/media?' + urllib.parse.urlencode(q),
                 want=want)


def vault_lists(account):
    return paged(account, f'/api2/v2/vault/lists?limit={OF_PAGE_LIMIT}')


def transactions(account, want=200):
    return paged(account, f'/api2/v2/payouts/transactions?limit={OF_PAGE_LIMIT}',
                 want=want)


def configured():
    """Whether this deployment can sign at all. Unlike the middleman there is no
    API key to hold — only the rules have to be reachable."""
    try:
        return bool(of_rules.rules())
    except of_rules.RulesError:
        return False


# ── Standing in for the middleman ─────────────────────────────────────────────
# app.py imports whichever transport is in use under one name, so the names the
# middleman's module exposes are answered here too. An account is what the
# vault holds rather than what someone else's dashboard lists, and signing in
# happens in the hosted browser, so the sign-in calls say so instead of failing
# somewhere further down.

OnlyFansApiError = OnlyFansError


def accounts(key=None):
    """Every account connected to this deployment, shaped like the middleman's
    listing so the account picker does not care which transport is running."""
    return [{'id': a['account'], 'onlyfans_username': a['username'],
             'display_name': a['name'],
             'onlyfans_user_data': {'id': a['user_id'], 'name': a['name'],
                                    'username': a['username']},
             'is_authenticated': a['status'] == of_session.STATUS_LIVE,
             'authentication_progress': a['status']}
            for a in of_session.accounts()]


def account_row(account_id, key=None):
    for a in accounts():
        if str(a.get('id') or '') == str(account_id):
            return a
    return {}


def _hosted_only(*a, **kw):
    raise OnlyFansError(0, 'this deployment signs accounts in through the hosted '
                           'browser, not through an API')


auth_start = auth_status = auth_submit = auth_email_otp = _hosted_only


def auth_read(res):
    return {'attempt_id': '', 'done': False, 'account_id': '', 'onlyfans_id': '',
            'username': '', 'name': '', 'needs': '', 'error': '', 'deeplink': ''}
