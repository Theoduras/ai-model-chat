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
                      chat_unsendable, direction_of, media_row, media_thumb,
                      msg_age_minutes, msg_id, msg_time, strip_html, text_of,
                      user_of_chat)

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


class OnlyFansError(RuntimeError):
    """A refusal from OnlyFans. `code` is the HTTP status, `detail` what it said."""

    def __init__(self, code, detail=''):
        self.code = code
        self.detail = detail
        super().__init__(f'OnlyFans {code}: {detail}'.strip())


class SessionExpired(OnlyFansError):
    """The session is no longer valid — the creator has to reconnect."""


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
    if not proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))


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


def call(account, method, path, body=None):
    """One request as `account`, signed, paced, and retried where retrying helps.

    A rules rotation and a rate limit both come back as failures that a second
    attempt fixes; a dead session does not, so it is raised as its own error and
    the account is marked so the creator gets told.
    """
    session = of_session.get(account)
    if not session:
        raise OnlyFansError(0, f'no OnlyFans session for {account}')
    if not path.startswith('/'):
        path = '/' + path
    refreshed = False
    for attempt in range(OF_MAX_RETRIES):
        _wait_turn(account)
        try:
            return _once(account, method, path, body, session)
        except OnlyFansError as e:
            if e.code == 401 or (e.code == 403 and 'sign' not in e.detail.lower()):
                of_session.mark_expired(account, e.detail)
                raise SessionExpired(e.code, 'the OnlyFans session expired — '
                                             'reconnect the account')
            if of_rules.stale_response(e.code, e.detail) and not refreshed:
                logger.info('OnlyFans rules rotated, refetching')
                of_rules.refresh()
                refreshed = True
                continue
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
