"""Instagram REST transport for a user account, driven the same way
discord_rest.py drives Discord: a captured browser session, not an official
app. Instagram has no public API for Stories at all and its Graph API needs a
Business/Creator account plus app review even for Posts and Reels, so this
speaks the same undocumented endpoints instagram.com's own web app uses —
cookie, CSRF token and app id, all taken off a real signed-in session by
of_connect.py.

These endpoints are not published anywhere and Instagram changes them without
notice, the same way Discord moves its "accept a DM request" route. Each one
is overridable by an env var so a drift can be fixed without a deploy; a 4xx
here is the first thing to check.

Nothing here touches the database or the persona layer; app.py binds it to
the persona the same way discord_rest.py is bound.
"""
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid

logger = logging.getLogger(__name__)

INSTAGRAM_WEB = os.getenv('INSTAGRAM_WEB_BASE', 'https://www.instagram.com')
INSTAGRAM_API = f'{INSTAGRAM_WEB}/api/v1'
INSTAGRAM_TIMEOUT = 30
# A photo or a reel's video is the one call carrying real file bytes over an
# extra network hop (app -> residential proxy -> Instagram) instead of a few
# hundred bytes of JSON, and 30s was timing out on it well before the small
# configure/me() calls ever would.
INSTAGRAM_UPLOAD_TIMEOUT = int(os.getenv('INSTAGRAM_UPLOAD_TIMEOUT', '120'))
# Instagram's web app itself, not something scraped per account — every
# signed-out visitor gets the same one, so a default is safe to ship.
DEFAULT_APP_ID = os.getenv('INSTAGRAM_APP_ID', '936619743392459')
DEFAULT_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')


def _opener(proxy):
    """A URL opener pinned to this account's exit IP, same reasoning as
    of_client._opener: a session that suddenly calls from a datacentre after
    signing in from a residential one is what gets an account flagged.

    Turning the pool off (unsetting INSTAGRAM_PROXY_TEMPLATE) has to reach
    sessions already stored too, or they keep dialling a gateway nobody is
    paying for any more.
    """
    if not (os.getenv('INSTAGRAM_PROXY_TEMPLATE') or '').strip():
        proxy = ''
    if not proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))

# Where each step of a post actually goes. Kept as names rather than inlined
# so a rotation shows up as one env var to set, the same idea as Discord's
# ACCEPT_ROUTES.
PATH_UPLOAD_PHOTO = os.getenv('INSTAGRAM_UPLOAD_PHOTO_PATH', '/rupload_igphoto/')
PATH_UPLOAD_VIDEO = os.getenv('INSTAGRAM_UPLOAD_VIDEO_PATH', '/rupload_igvideo/')
PATH_CONFIGURE_FEED = os.getenv('INSTAGRAM_CONFIGURE_FEED_PATH', '/media/configure/')
PATH_CONFIGURE_STORY = os.getenv('INSTAGRAM_CONFIGURE_STORY_PATH',
                                  '/media/configure_to_story/')
PATH_CONFIGURE_REEL = os.getenv('INSTAGRAM_CONFIGURE_REEL_PATH',
                                 '/media/configure_to_clips/')


class InstagramApiError(RuntimeError):
    """`code` is the HTTP status and `detail` what Instagram said, so a caller
    can tell a dead session (401/403) from a rejected upload (400)."""

    def __init__(self, code, detail=''):
        self.code = code
        self.detail = detail
        super().__init__(f'Instagram API {code}: {detail}'.strip())


class Rest:
    """One account's REST side. Holds the captured session and the rate-limit
    state, so a stood-down account slows down everywhere rather than per call
    site — same reasoning as discord_rest.Rest."""

    def __init__(self, session, base=INSTAGRAM_API):
        session = session or {}
        self.cookie = session.get('cookie') or ''
        self.csrftoken = session.get('csrftoken') or ''
        self.app_id = session.get('app_id') or DEFAULT_APP_ID
        self.user_agent = session.get('user_agent') or DEFAULT_UA
        self.proxy = session.get('proxy') or ''
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
            'Origin': INSTAGRAM_WEB,
            'Referer': INSTAGRAM_WEB + '/',
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
        """One request. `body` is JSON unless `raw` — an upload sends bytes and
        signs its own content headers instead, and gets the longer timeout."""
        if not self.configured():
            raise InstagramApiError(0, 'No Instagram session for this persona')
        self._wait()
        data = body if raw else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method.upper())
        for key, value in self._headers(headers).items():
            req.add_header(key, value)
        if not raw and body is not None:
            req.add_header('Content-Type', 'application/json')
        timeout = INSTAGRAM_UPLOAD_TIMEOUT if raw else INSTAGRAM_TIMEOUT
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
            logger.warning('instagram %s %s -> %s: %s', method, url, e.code, detail[:300])
            raise InstagramApiError(e.code, detail)
        except urllib.error.URLError as e:
            raise InstagramApiError(0, str(getattr(e, 'reason', e))[:200])

    # ── The calls the poster actually makes ──────────────────────────────────

    def me(self):
        return self.call('GET', f'{self.base}/accounts/current_user/?edit=true')

    def _upload(self, media_bytes, kind, width=0, height=0, duration_ms=0):
        """Hand over the bytes, get back an upload_id to configure into a post.

        One name, one set of rupload params — the shape every Instagram client
        uses whether the post ends up in the feed, a story or a reel; what
        differs is the configure call afterward. width/height/duration_ms are
        the real values for a video (read in the browser, not guessed here) —
        Instagram's clips/story configure calls reject zeros with "Missing
        info.", the same way a Reel with no length does.
        """
        upload_id = str(int(time.time() * 1000))
        is_video = kind == 'video'
        name = f'{upload_id}_0_{"video" if is_video else "photo"}'
        path = PATH_UPLOAD_VIDEO if is_video else PATH_UPLOAD_PHOTO
        url = f'{INSTAGRAM_WEB}{path}{name}'
        params = {'media_type': 2 if is_video else 1, 'upload_id': upload_id,
                  'upload_media_height': int(height or 0), 'upload_media_width': int(width or 0)}
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

    def post_feed(self, media_bytes, kind, caption='', width=0, height=0, duration_ms=0):
        upload_id = self._upload(media_bytes, kind, width, height, duration_ms)
        body = {'upload_id': upload_id, 'caption': caption or '',
                'source_type': '4'}
        return self.call('POST', f'{self.base}{PATH_CONFIGURE_FEED}', body=body)

    def post_story(self, media_bytes, kind, caption='', width=0, height=0, duration_ms=0):
        upload_id = self._upload(media_bytes, kind, width, height, duration_ms)
        body = {'upload_id': upload_id, 'caption': caption or '',
                'source_type': '4', 'configure_mode': 1}
        if kind == 'video' and duration_ms:
            body['length'] = round(duration_ms / 1000, 3)
        return self.call('POST', f'{self.base}{PATH_CONFIGURE_STORY}', body=body)

    def post_reel(self, media_bytes, caption='', width=0, height=0, duration_ms=0):
        upload_id = self._upload(media_bytes, 'video', width, height, duration_ms)
        length = round((duration_ms or 0) / 1000, 3)
        body = {'upload_id': upload_id, 'caption': caption or '',
                'source_type': '4', 'length': length,
                'clips': [{'length': length, 'source_type': '4'}],
                'poster_frame_index': 0, 'audio_muted': False}
        if width and height:
            body['width'] = int(width)
            body['height'] = int(height)
            body['extra'] = {'source_width': int(width), 'source_height': int(height)}
        return self.call('POST', f'{self.base}{PATH_CONFIGURE_REEL}', body=body)
