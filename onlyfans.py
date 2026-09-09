"""OnlyFansAPI transport.

OnlyFans has no public chat API, so everything here goes through
OnlyFansAPI.com: one team API key, accounts connected in their dashboard, and
per-account paths of the form /api/{account}/chats/{chat_id}/messages. A chat
id *is* the fan's OnlyFans user id, which is what lets the shared reply engine
treat a chat here the same way it treats a Fanvue one.

Nothing in this module touches the database or the persona layer — app.py binds
it to a platform adapter. Keeping it separate is also what keeps the webhook
verification testable without a Flask app.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from urllib import error as url_error

logger = logging.getLogger(__name__)

OF_API_BASE = 'https://app.onlyfansapi.com'
OF_TIMEOUT = 20
OF_PAGE_LIMIT = 50
OF_MAX_PAGES = 20
# What OnlyFans itself allows on a paid message. A tier priced outside this
# cannot be sent at all, so the caller is told rather than the send failing.
OF_PRICE_MIN_USD = 3.0
OF_PRICE_MAX_USD = 200.0

_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'[ \t]+')


class OnlyFansApiError(RuntimeError):
    """An error OnlyFansAPI returned. `code` is the HTTP status, `detail` the
    message it gave, so the caller can tell a blocked fan from a bad key."""

    def __init__(self, code, detail=''):
        self.code = code
        self.detail = detail
        super().__init__(f'OnlyFans API {code}: {detail}'.strip())


def api_key():
    return (os.environ.get('ONLYFANSAPI_KEY', '')
            or os.environ.get('ONLYFANS_API_KEY', '')).strip()


def configured():
    return bool(api_key())


def call(method, path, body=None, idem=None, key=None, timeout=OF_TIMEOUT):
    """One call to OnlyFansAPI. Returns the decoded body, {} for an empty one."""
    token = key or api_key()
    if not token:
        raise OnlyFansApiError(0, 'no OnlyFansAPI key configured')
    url = path if path.startswith('http') else OF_API_BASE + path
    headers = {'Authorization': f'Bearer {token}',
               'Content-Type': 'application/json', 'Accept': 'application/json'}
    if idem:
        headers['Idempotency-Key'] = idem
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except url_error.HTTPError as e:
        raw = b''
        try:
            raw = e.read()
        except Exception:
            pass
        text = raw.decode(errors='ignore')[:500] if raw else ''
        detail = text
        try:
            d = json.loads(text)
            if isinstance(d, dict):
                detail = str(d.get('message') or d.get('description')
                             or d.get('error') or text)[:300]
        except Exception:
            pass
        logger.warning('OnlyFans API %s %s → %s: %s', method, url, e.code, text[:200])
        raise OnlyFansApiError(e.code, detail) from None
    except Exception as e:
        raise OnlyFansApiError(0, str(e)[:200]) from None


def rows(res):
    """The list inside a response, whatever it is wrapped in."""
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        d = res.get('data')
        if isinstance(d, list):
            return d
        if isinstance(d, dict):
            for k in ('list', 'items', 'chats', 'messages', 'data'):
                if isinstance(d.get(k), list):
                    return d[k]
    return []


def paged(path, want=None, max_pages=OF_MAX_PAGES, key=None):
    """Every row of a collection, following _pagination.next_page.

    OnlyFansAPI hands back a full URL for the next page, cursor-style — asking
    for page numbers the way Fanvue does would silently return the first page
    for ever.
    """
    out, seen, url = [], set(), path
    for _ in range(max_pages):
        res = call('GET', url, key=key)
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
        nxt = ((res.get('_pagination') or {}).get('next_page')
               if isinstance(res, dict) else None)
        if not nxt:
            break
        url = nxt
    return out


# ── Endpoints ─────────────────────────────────────────────────────────────────

def accounts(key=None):
    """Every OnlyFans account connected to this OnlyFansAPI team."""
    res = call('GET', '/api/accounts', key=key)
    return rows(res) or (res if isinstance(res, list) else [])


def account_row(account_id, key=None):
    for a in accounts(key=key):
        if str(a.get('id') or '') == str(account_id):
            return a
    return {}


def chats(account, unread_only=False, want=None):
    q = f'?limit={OF_PAGE_LIMIT}' + ('&filter=unread' if unread_only else '')
    return paged(f'/api/{account}/chats{q}', want=want)


def messages(account, chat_id, want=20):
    """The `want` most recent messages in one chat, oldest last.

    Reading a chat marks it read on OnlyFans, so this is called for chats the
    round is actually about to answer — never as a way to poll for new ones.
    """
    msgs = paged(f'/api/{account}/chats/{chat_id}/messages?limit={min(want, OF_PAGE_LIMIT)}',
                 want=want, max_pages=max(1, -(-want // OF_PAGE_LIMIT)))
    return sorted(msgs, key=lambda m: msg_time(m))


def send(account, chat_id, text, price=0, media=(), idem=None):
    """Send one message. A price needs media on it — OnlyFans rejects a paid
    message with nothing locked behind it."""
    body = {'text': (text or '')[:2000]}
    media = [m for m in (media or []) if m not in (None, '')]
    if media:
        body['mediaFiles'] = media
    if price:
        if not media:
            raise OnlyFansApiError(0, 'a paid message needs at least one media file')
        body['price'] = round(float(price), 2)
    return call('POST', f'/api/{account}/chats/{chat_id}/messages', body=body,
                idem=idem or str(uuid.uuid4()))


def typing(account, chat_id):
    """Show the typing indicator. It lasts a few seconds, so it is called again
    while the reply is still being paced out."""
    return call('POST', f'/api/{account}/chats/{chat_id}/typing')


def vault(account, list_id=None, media_type=None, want=200, query=''):
    q = {'limit': OF_PAGE_LIMIT}
    if list_id:
        q['list'] = list_id
    if media_type:
        q['type'] = media_type
    if query:
        q['query'] = query
    return paged(f'/api/{account}/media/vault?' + urllib.parse.urlencode(q), want=want)


def vault_lists(account):
    return paged(f'/api/{account}/media/vault/lists?limit={OF_PAGE_LIMIT}')


def transactions(account, want=200):
    return paged(f'/api/{account}/payouts/transactions?limit={OF_PAGE_LIMIT}', want=want)


# ── Payload readers ───────────────────────────────────────────────────────────
# Everything below is pure: given what the API returned, say what it means. The
# reply engine only ever sees these, never the raw shapes.

def strip_html(text):
    """Message text arrives as HTML. A fan's <p>hey</p> must reach the model as
    "hey", or the persona ends up answering markup."""
    s = str(text or '')
    if '<' not in s:
        return s.strip()
    s = re.sub(r'<br\s*/?>', '\n', s, flags=re.I)
    s = re.sub(r'</p\s*>', '\n', s, flags=re.I)
    s = _TAG_RE.sub('', s)
    for a, b in (('&nbsp;', ' '), ('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                 ('&quot;', '"'), ('&#39;', "'")):
        s = s.replace(a, b)
    return _WS_RE.sub(' ', s).strip()


def user_of_chat(chat):
    """(fan_id, handle, is_creator, chat_id) for the fan on the other side.

    The chat id is the fan's user id on OnlyFans, so both come from the one
    object — there is no separate conversation id to carry around.
    """
    if not isinstance(chat, dict):
        return '', '', False, ''
    u = chat.get('fan') or chat.get('withUser') or chat.get('user') or {}
    if not isinstance(u, dict):
        u = {}
    fan_id = str(u.get('id') or chat.get('id') or '')
    handle = str(u.get('username') or u.get('name') or '')
    return fan_id, handle, bool(u.get('isPerformer')), fan_id


def chat_online(chat, grace_minutes=5):
    u = (chat.get('fan') or chat.get('withUser') or chat.get('user') or {}) \
        if isinstance(chat, dict) else {}
    if not isinstance(u, dict):
        u = {}
    for src in (chat if isinstance(chat, dict) else {}, u):
        if src.get('isOnline') is True or src.get('online') is True:
            return True
    seen = u.get('lastSeen') or (chat.get('lastSeen') if isinstance(chat, dict) else '')
    age = _age_minutes(seen)
    return age is not None and age <= grace_minutes


def chat_unsendable(chat):
    """Why OnlyFans will not accept a message for this chat, or ''."""
    if not isinstance(chat, dict):
        return ''
    if chat.get('canSendMessage') is False:
        return str(chat.get('canNotSendReason') or 'cannot send to this chat')[:160]
    return ''


def text_of(msg):
    return strip_html(msg.get('text') if isinstance(msg, dict) else '')


def msg_id(msg):
    return str(msg.get('id') or '') if isinstance(msg, dict) else ''


def msg_time(msg):
    return str(msg.get('createdAt') or msg.get('changedAt') or '') \
        if isinstance(msg, dict) else ''


def _age_minutes(raw):
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    except Exception:
        return None


def msg_age_minutes(msg):
    return _age_minutes(msg_time(msg))


def direction_of(msg, fan_id, me_id=None, recent_out=()):
    """'out' (us), 'in' (the fan), or '' when nothing settles it.

    OnlyFans answers this outright with isSentByMe, so unlike Fanvue there is
    almost never anything to guess at — which is the whole reason the persona
    cannot end up reading her own words back as the fan's.
    """
    if not isinstance(msg, dict):
        return ''
    if msg.get('isSentByMe') is True:
        return 'out'
    if msg.get('isSentByMe') is False:
        return 'in'
    sender = str(((msg.get('fromUser') or {}).get('id')
                  if isinstance(msg.get('fromUser'), dict) else '') or '')
    if sender:
        if fan_id and sender == str(fan_id):
            return 'in'
        if me_id and sender == str(me_id):
            return 'out'
    text = text_of(msg)
    return 'out' if text and text in recent_out else ''


def media_thumb(m):
    """Best preview URL for a vault item."""
    files = m.get('files') if isinstance(m, dict) else None
    if isinstance(files, dict):
        for k in ('thumb', 'preview', 'squarePreview', 'full', 'source'):
            v = files.get(k)
            if isinstance(v, dict) and v.get('url'):
                return v['url']
            if isinstance(v, str) and v:
                return v
    return (m.get('url') or '') if isinstance(m, dict) else ''


def media_row(m):
    """One vault item in the shape the persona builder's media picker wants."""
    return {'id': str(m.get('id') or ''), 'type': str(m.get('type') or ''),
            'thumb': media_thumb(m), 'created_at': str(m.get('createdAt') or ''),
            'ready': m.get('isReady') is not False}


# ── Webhooks ──────────────────────────────────────────────────────────────────

def verify_signature(raw_body, header, secret):
    """(ok, why). The signature is a hex HMAC-SHA256 of the exact bytes we were
    posted, so it is checked before the body is parsed."""
    if not secret:
        return False, 'no signing secret configured'
    if not header:
        return False, 'no Signature header'
    want = hmac.new(secret.encode(), raw_body or b'', hashlib.sha256).hexdigest()
    got = str(header).strip().lower()
    if got.startswith('sha256='):
        got = got[7:]
    return (True, '') if hmac.compare_digest(want, got) else (False, 'signature mismatch')


def event_fan(payload, event=''):
    """The fan id an event is about — which is also the chat id to reply in."""
    if not isinstance(payload, dict):
        return ''
    for k in ('user_id', 'fromUser', 'user', 'fan'):
        v = payload.get(k)
        if isinstance(v, dict) and v.get('id'):
            return str(v['id'])
        if isinstance(v, (str, int)) and v:
            return str(v)
    return str(payload.get('id') or '')


def event_handle(payload):
    for k in ('fromUser', 'user', 'fan'):
        v = payload.get(k) if isinstance(payload, dict) else None
        if isinstance(v, dict):
            return str(v.get('username') or v.get('name') or '')
    return ''


def ppv_amount_cents(payload):
    """The purchase amount on a messages.ppv.unlocked event, in cents."""
    if not isinstance(payload, dict):
        return 0
    raw = (payload.get('replacePairs') or {}).get('{AMOUNT}') if isinstance(
        payload.get('replacePairs'), dict) else None
    raw = raw if raw not in (None, '') else payload.get('amount')
    if raw in (None, ''):
        return 0
    try:
        return int(round(float(str(raw).replace('$', '').replace(',', '').strip()) * 100))
    except (TypeError, ValueError):
        return 0
