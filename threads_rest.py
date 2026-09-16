"""Threads REST transport, driven off the Instagram session.

A Threads account is an Instagram account — Threads is Instagram's "Barcelona"
surface, served by the same web stack behind a different app id. So the cookie
and CSRF token of_connect.py already captured at /instagram/connect authenticate
Threads too, and the creator never signs in twice. That is the whole reason this
file exists rather than a second hosted sign-in.

The official Threads graph API is still there (app.py's `_threads_*` helpers) and
stays the fallback for a persona with no Instagram session: it needs a Meta app,
app review and a separate approval per account, which is exactly the friction
this avoids.

These endpoints are undocumented and Meta moves them without notice, same as the
Instagram ones next door in instagram_rest.py — each one is an env var so a drift
is a setting to change, not a deploy. A 4xx here is the first thing to check.

Nothing here touches the database or the persona layer; app.py binds it to a
persona the same way instagram_rest.py is bound.
"""
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

logger = logging.getLogger(__name__)

THREADS_WEB = os.getenv('THREADS_WEB_BASE', 'https://www.threads.com')
THREADS_API = f'{THREADS_WEB}/api/v1'
THREADS_TIMEOUT = 30
# Same reasoning as INSTAGRAM_UPLOAD_TIMEOUT: the byte-carrying hop through a
# residential proxy times out long before a configure call would.
THREADS_UPLOAD_TIMEOUT = int(os.getenv('THREADS_UPLOAD_TIMEOUT', '120'))
# Threads' own web client id, the counterpart of instagram_rest.DEFAULT_APP_ID.
# Every signed-out visitor gets the same one, so shipping a default is safe —
# and swapping it for Instagram's is the one header that turns an Instagram
# session into a Threads session.
DEFAULT_APP_ID = os.getenv('THREADS_APP_ID', '238260118697367')
DEFAULT_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

TEXT_LIMIT = 500
CAROUSEL_MAX = 20

PATH_UPLOAD_PHOTO = os.getenv('THREADS_UPLOAD_PHOTO_PATH', '/rupload_igphoto/')
PATH_UPLOAD_VIDEO = os.getenv('THREADS_UPLOAD_VIDEO_PATH', '/rupload_igvideo/')
PATH_CONFIGURE_TEXT = os.getenv('THREADS_CONFIGURE_TEXT_PATH',
                                '/media/configure_text_only_post/')
PATH_CONFIGURE_IMAGE = os.getenv('THREADS_CONFIGURE_IMAGE_PATH',
                                 '/media/configure_text_post_app_feed/')
PATH_CONFIGURE_VIDEO = os.getenv('THREADS_CONFIGURE_VIDEO_PATH',
                                 '/media/configure_text_post_app_video/')
PATH_CONFIGURE_SIDECAR = os.getenv('THREADS_CONFIGURE_SIDECAR_PATH',
                                   '/media/configure_text_post_app_sidecar/')
PATH_ME = os.getenv('THREADS_ME_PATH', '/accounts/current_user/?edit=true')
PATH_FEED = os.getenv('THREADS_FEED_PATH', '/text_feed/{id}/profile/')
PATH_REPLIES = os.getenv('THREADS_REPLIES_PATH', '/text_feed/{id}/replies/')

# ── Direct messages ──────────────────────────────────────────────────────────
#
# Threads DMs ride the same direct surface Instagram's do, reached with the
# Threads app id already set on this session. Nothing here is documented and
# Meta moves it, so every path is an env var, same as the posting half. A 4xx
# is the first thing to check.

PATH_INBOX = os.getenv('THREADS_INBOX_PATH', '/direct_v2/inbox/')
PATH_THREAD = os.getenv('THREADS_THREAD_PATH', '/direct_v2/threads/{id}/')
PATH_BROADCAST = os.getenv('THREADS_BROADCAST_PATH', '/direct_v2/threads/broadcast/text/')
PATH_ACTIVITY = os.getenv('THREADS_ACTIVITY_PATH',
                          '/direct_v2/threads/{id}/items/indicate_activity/')
INBOX_PAGE = int(os.getenv('THREADS_INBOX_PAGE', '20'))


def _q(params):
    return ('?' + urllib.parse.urlencode(params)) if params else ''


# ── Reading what came back ───────────────────────────────────────────────────
#
# app.py's adapter stays a set of one-liners onto these, the same way
# _DiscordPlatform is a set of one-liners onto discord_gateway.

# 0 anyone, 1 accounts you follow, 2 mentioned only. The composer sends the
# creator's choice; anything else Threads rejects outright.
REPLY_CONTROL = {'everyone': 0, 'followed': 1, 'mentioned': 2}


def _opener(proxy):
    """Pinned to the account's exit IP for the same reason instagram_rest does
    it: a session that signs in residential and then posts from a datacentre is
    what gets flagged. Threads rides the Instagram proxy setting, because it is
    the same account behind the same session."""
    if not (os.getenv('INSTAGRAM_PROXY_TEMPLATE') or '').strip():
        proxy = ''
    if not proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))


def _encode_form(body):
    parts = {}
    for k, v in body.items():
        if isinstance(v, (dict, list)):
            parts[k] = json.dumps(v, separators=(',', ':'))
        elif isinstance(v, bool):
            parts[k] = 'true' if v else 'false'
        else:
            parts[k] = str(v)
    return urllib.parse.urlencode(parts).encode()


class ThreadsApiError(RuntimeError):
    """`code` is the HTTP status and `detail` what Threads said, so a caller can
    tell a dead session (401/403) from a rejected post (400)."""

    def __init__(self, code, detail=''):
        self.code = code
        self.detail = detail
        super().__init__(f'Threads API {code}: {detail}'.strip())


class Rest:
    """One account's Threads REST side, built from its Instagram session."""

    def __init__(self, session, base=THREADS_API):
        session = session or {}
        self.cookie = session.get('cookie') or ''
        self.csrftoken = session.get('csrftoken') or ''
        self.user_agent = session.get('user_agent') or DEFAULT_UA
        self.proxy = session.get('proxy') or ''
        # Deliberately not session['app_id']: that one is Instagram's, and the
        # same call with it answers for Instagram instead of Threads.
        self.app_id = DEFAULT_APP_ID
        self.base = base.rstrip('/')
        self._lock = threading.Lock()
        self._until = 0.0

    def configured(self):
        return bool(self.cookie and self.csrftoken)

    def held(self):
        left = self._until - time.time()
        return int(left) + 1 if left > 0 else 0

    def _headers(self, extra=None):
        h = {
            'Cookie': self.cookie,
            'X-CSRFToken': self.csrftoken,
            'X-IG-App-ID': self.app_id,
            'User-Agent': self.user_agent,
            'X-Requested-With': 'XMLHttpRequest',
            'X-Instagram-AJAX': '1',
            'Origin': THREADS_WEB,
            'Referer': THREADS_WEB + '/',
        }
        if extra:
            h.update(extra)
        return h

    def _wait(self):
        while True:
            with self._lock:
                left = self._until - time.time()
                if left <= 0:
                    return
            time.sleep(min(left, 5.0))

    def call(self, method, url, body=None, headers=None, raw=False, retries=1):
        """One request. Form-urlencoded unless `raw`, which sends file bytes and
        signs its own content headers — the same split instagram_rest.call makes,
        because it is the same web stack answering."""
        if not self.configured():
            raise ThreadsApiError(0, 'No Instagram session for this persona')
        self._wait()
        data = body if raw else (_encode_form(body) if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method.upper())
        for key, value in self._headers(headers).items():
            req.add_header(key, value)
        if not raw and body is not None:
            req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        timeout = THREADS_UPLOAD_TIMEOUT if raw else THREADS_TIMEOUT
        try:
            with _opener(self.proxy).open(req, timeout=timeout) as resp:
                out = resp.read()
                if not out:
                    return {}
                try:
                    return json.loads(out)
                except ValueError:
                    return {}
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = (e.read() or b'')[:400].decode('utf-8', 'replace')
            except Exception:
                pass
            if e.code == 429 and retries > 0:
                after = float(e.headers.get('Retry-After') or 5)
                with self._lock:
                    self._until = time.time() + after
                self._wait()
                return self.call(method, url, body, headers, raw, retries - 1)
            logger.warning('threads %s %s -> %s: %s', method, url, e.code, detail[:300])
            raise ThreadsApiError(e.code, detail)
        except urllib.error.URLError as e:
            raise ThreadsApiError(0, str(getattr(e, 'reason', e))[:200])

    # ── The calls the poster actually makes ──────────────────────────────────

    def me(self):
        return self.call('GET', f'{self.base}{PATH_ME}')

    def own_posts(self, user_id, limit=10):
        """Her own recent Threads, for the round that answers their replies."""
        path = PATH_FEED.format(id=user_id)
        res = self.call('GET', f'{self.base}{path}{_q({"limit": limit})}')
        return _feed_rows(res)[:limit]

    def replies(self, media_id, limit=30):
        path = PATH_REPLIES.format(id=media_id)
        res = self.call('GET', f'{self.base}{path}{_q({"limit": limit})}')
        return _feed_rows(res)[:limit]

    def _info(self, reply_control='everyone'):
        return {'reply_control': REPLY_CONTROL.get(reply_control, 0)}

    def _upload(self, media_bytes, kind, width=0, height=0, duration_ms=0):
        """Hand over the bytes, get back an upload_id to configure into a post.

        Byte-identical to the Instagram upload — the rupload endpoints are
        shared, and only the configure call afterwards decides which app the
        post lands in.
        """
        upload_id = str(int(time.time() * 1000))
        is_video = kind == 'video'
        name = f'{upload_id}_0_{"video" if is_video else "photo"}'
        path = PATH_UPLOAD_VIDEO if is_video else PATH_UPLOAD_PHOTO
        url = f'{THREADS_WEB}{path}{name}'
        params = {'media_type': 2 if is_video else 1, 'upload_id': upload_id,
                  'upload_media_height': int(height or 0),
                  'upload_media_width': int(width or 0)}
        if is_video:
            params['upload_media_duration_ms'] = int(duration_ms or 0)
        headers = {
            'X-Entity-Name': name,
            'X-Entity-Length': str(len(media_bytes)),
            'X-Entity-Type': 'video/mp4' if is_video else 'image/jpeg',
            'Offset': '0',
            'Content-Type': 'application/octet-stream',
            'X-Instagram-Rupload-Params': json.dumps(params),
        }
        self.call('POST', url, body=media_bytes, headers=headers, raw=True)
        return upload_id

    def post_text(self, caption, reply_control='everyone', reply_to_id=''):
        """A text-only Thread. No upload at all — the upload_id is just the
        client-minted post id Threads keys the write on."""
        body = {'upload_id': str(int(time.time() * 1000)),
                'caption': caption or '',
                'text_post_app_info': self._info(reply_control),
                'publish_mode': 'text_post'}
        if reply_to_id:
            body['text_post_app_info'] = dict(body['text_post_app_info'],
                                              reply_id=str(reply_to_id))
        return self.call('POST', f'{self.base}{PATH_CONFIGURE_TEXT}', body=body)

    def post_image(self, media_bytes, caption='', width=0, height=0,
                   reply_control='everyone'):
        upload_id = self._upload(media_bytes, 'photo', width, height)
        body = {'upload_id': upload_id, 'caption': caption or '',
                'source_type': '4',
                'text_post_app_info': self._info(reply_control)}
        return self.call('POST', f'{self.base}{PATH_CONFIGURE_IMAGE}', body=body)

    def post_video(self, media_bytes, caption='', width=0, height=0,
                   duration_ms=0, reply_control='everyone'):
        upload_id = self._upload(media_bytes, 'video', width, height, duration_ms)
        body = {'upload_id': upload_id, 'caption': caption or '',
                'source_type': '4', 'length': round((duration_ms or 0) / 1000, 3),
                'text_post_app_info': self._info(reply_control)}
        if width and height:
            body['width'] = int(width)
            body['height'] = int(height)
        return self.call('POST', f'{self.base}{PATH_CONFIGURE_VIDEO}', body=body)

    def post_carousel(self, items, caption='', reply_control='everyone'):
        """`items` are (media_bytes, kind, width, height, duration_ms) tuples.
        Each is uploaded on its own and then named in one configure call."""
        children = []
        for media_bytes, kind, width, height, duration_ms in items[:CAROUSEL_MAX]:
            upload_id = self._upload(media_bytes, kind, width, height, duration_ms)
            child = {'upload_id': upload_id, 'source_type': '4'}
            if kind == 'video':
                child['length'] = round((duration_ms or 0) / 1000, 3)
            children.append(child)
        body = {'caption': caption or '', 'client_sidecar_id': str(int(time.time() * 1000)),
                'source_type': '4', 'children_metadata': children,
                'text_post_app_info': self._info(reply_control)}
        return self.call('POST', f'{self.base}{PATH_CONFIGURE_SIDECAR}', body=body)

    # ── Direct messages ──────────────────────────────────────────────────

    def inbox(self, cursor='', limit=INBOX_PAGE):
        params = {'limit': limit, 'thread_message_limit': 10, 'persistentBadging': 'true'}
        if cursor:
            params['cursor'] = cursor
        res = self.call('GET', f'{self.base}{PATH_INBOX}{_q(params)}')
        return ((res or {}).get('inbox') or {}).get('threads') or []

    def thread(self, thread_id, cursor='', limit=INBOX_PAGE):
        params = {'limit': limit}
        if cursor:
            params['cursor'] = cursor
        path = PATH_THREAD.format(id=thread_id)
        res = self.call('GET', f'{self.base}{path}{_q(params)}')
        return (res or {}).get('thread') or res or {}

    def send_text(self, thread_id, text):
        body = {'action': 'send_item', 'thread_ids': json.dumps([str(thread_id)]),
                'text': text or '',
                'client_context': str(uuid.uuid4()),
                '_uuid': str(uuid.uuid4())}
        return self.call('POST', f'{self.base}{PATH_BROADCAST}', body=body)

    def typing(self, thread_id):
        """Best effort: a typing indicator that does not arrive is not a reason
        to drop the reply that follows it."""
        path = PATH_ACTIVITY.format(id=thread_id)
        try:
            return self.call('POST', f'{self.base}{path}',
                             body={'action': 'indicate_activity', 'activity_status': '1'})
        except ThreadsApiError as e:
            logger.debug('threads typing indicator failed: %s', str(e)[:120])
            return {}


def post_id(result):
    """Threads answers with the created media under a couple of shapes depending
    on which configure call ran, so the caller does not have to guess."""
    result = result or {}
    media = result.get('media') or {}
    return str(media.get('pk') or media.get('id') or result.get('id')
               or result.get('pk') or '')


def thread_id_of(chat):
    return str((chat or {}).get('thread_id') or (chat or {}).get('thread_v2_id') or '')


def user_of_thread(chat):
    """The other person in a one-to-one thread. A group has several, and the
    caller drops those rather than treating a room as a fan."""
    users = (chat or {}).get('users') or []
    if len(users) != 1:
        return {}
    u = users[0]
    return {'id': str(u.get('pk') or u.get('id') or ''),
            'username': u.get('username') or '',
            'name': u.get('full_name') or ''}


def is_group(chat):
    return bool((chat or {}).get('is_group')) or len((chat or {}).get('users') or []) > 1


def messages_of(chat):
    return (chat or {}).get('items') or []


def text_of(msg):
    msg = msg or {}
    if msg.get('item_type') and msg['item_type'] != 'text':
        return ''
    return msg.get('text') or ''


def msg_id(msg):
    return str((msg or {}).get('item_id') or (msg or {}).get('id') or '')


def msg_time(msg):
    """Direct timestamps are microseconds since the epoch, not seconds."""
    raw = (msg or {}).get('timestamp') or 0
    try:
        raw = int(raw)
    except (TypeError, ValueError):
        return 0
    return raw // 1000000 if raw > 10 ** 12 else raw


def msg_age_minutes(msg):
    at = msg_time(msg)
    return (time.time() - at) / 60.0 if at else 0.0


def is_outgoing(msg, me_id):
    return str((msg or {}).get('user_id') or '') == str(me_id or '')


def _feed_rows(res):
    """A text feed comes back as thread containers, each wrapping the posts in
    it. Flattened to the posts themselves, which is what a caller wants."""
    out = []
    for item in ((res or {}).get('threads') or (res or {}).get('items') or []):
        for post in (item.get('thread_items') or [item]):
            node = post.get('post') or post
            if node:
                out.append(node)
    return out


def feed_row(node):
    """One post, in the shape the graph API's reader returns, so the round
    reads the same dict whichever transport filled it."""
    node = node or {}
    user = node.get('user') or {}
    caption = node.get('caption') or {}
    return {'id': str(node.get('pk') or node.get('id') or ''),
            'text': caption.get('text') or node.get('text') or '',
            'username': user.get('username') or '',
            'timestamp': node.get('taken_at') or 0}
