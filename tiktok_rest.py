"""TikTok REST transport for a user account, driven the same way
instagram_rest.py drives Instagram: a captured browser session, not an official
app. TikTok's Content Posting API needs a developer app that passes a separate
audit -- until it does every post it makes is private to the creator -- and it
has no comment API at all, so replying to the people under her videos is not
something any official client can do. This speaks the same undocumented
endpoints tiktok.com's own web app uses: cookies, tt_csrf_token, device id and
the browser-minted msToken, all taken off a real signed-in session by
of_connect.py.

These endpoints are not published anywhere and TikTok changes them without
notice, the same way Instagram moves its configure routes. Each one is
overridable by an env var so a drift can be fixed without a deploy; a 4xx here
is the first thing to check.

The one thing a captured session cannot carry is TikTok's per-request
signature (X-Bogus, and a_bogus/X-Gnarly on newer builds): it is computed by
their own JavaScript over the query string and the user agent, so it cannot be
replayed. `TIKTOK_SIGNER_URL` points at a signer that mints one for a URL;
without it calls go out unsigned, which some routes still answer and others
answer with an empty 200. That is the shape of the wall, not a bug to retry
through.

Nothing here touches the database or the persona layer; app.py binds it to the
persona the same way instagram_rest.py is bound.
"""
import binascii
import datetime
import hashlib
import hmac
import json
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

TIKTOK_WEB = os.getenv('TIKTOK_WEB_BASE', 'https://www.tiktok.com')
TIKTOK_API = f'{TIKTOK_WEB}/api'
TIKTOK_TIMEOUT = 30
# Same reasoning as Instagram's: the call carrying file bytes over the extra
# proxy hop times out long before the few-hundred-byte JSON ones would.
TIKTOK_UPLOAD_TIMEOUT = int(os.getenv('TIKTOK_UPLOAD_TIMEOUT', '180'))
# TikTok's own web app id -- every signed-out visitor sends the same one.
AID = os.getenv('TIKTOK_AID', '1988')
DEFAULT_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

# Where each step of a post actually goes, as names rather than inlined, so a
# rotation is one env var to set.
PATH_ME = os.getenv('TIKTOK_ME_PATH', '/passport/web/account/info/')
PATH_UPLOAD_AUTH = os.getenv('TIKTOK_UPLOAD_AUTH_PATH', '/api/v1/video/upload/auth/')
PATH_PROJECT_CREATE = os.getenv('TIKTOK_PROJECT_CREATE_PATH', '/api/v1/web/project/create/')
PATH_PROJECT_POST = os.getenv('TIKTOK_PROJECT_POST_PATH', '/api/v1/web/project/post/')
PATH_POSTS = os.getenv('TIKTOK_POSTS_PATH', '/api/post/item_list/')
PATH_COMMENTS = os.getenv('TIKTOK_COMMENTS_PATH', '/api/comment/list/')
PATH_COMMENT_PUBLISH = os.getenv('TIKTOK_COMMENT_PUBLISH_PATH', '/api/comment/publish/')

# ByteDance's own upload gateway, which is what the web app hands the bytes to.
# It is a different service from tiktok.com with its own SigV4-style auth, and
# the credentials for it come from PATH_UPLOAD_AUTH per upload.
VOD_HOST = os.getenv('TIKTOK_VOD_HOST', 'https://vod-us-east-1.bytevcloudapi.com')
VOD_REGION = os.getenv('TIKTOK_VOD_REGION', 'us-east-1')
VOD_SERVICE = os.getenv('TIKTOK_VOD_SERVICE', 'vod')
VOD_SPACE = os.getenv('TIKTOK_VOD_SPACE', 'tiktok')
# A signer that answers {"X-Bogus": "...", "a_bogus": "..."} for a url. Unset
# on purpose: there is no honest default, and pretending there is hides why a
# call came back empty.
SIGNER_URL = (os.getenv('TIKTOK_SIGNER_URL') or '').strip()

PHOTO_MAX = 35
CAPTION_CAP = 2200


class TikTokApiError(RuntimeError):
    """`code` is the HTTP status and `detail` what TikTok said, so a caller can
    tell a dead session (401/403, or TikTok's own status_code 8) from a
    rejected upload."""

    def __init__(self, code, detail=''):
        self.code = code
        self.detail = detail
        super().__init__(f'TikTok API {code}: {detail}'.strip())


def _opener(proxy):
    """A URL opener pinned to this account's exit IP, same reasoning as
    instagram_rest._opener: a session that signs in from a residential address
    and then posts from a datacentre is what gets an account flagged."""
    if not (os.getenv('TIKTOK_PROXY_TEMPLATE') or '').strip():
        proxy = ''
    if not proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))


def _cookie_value(cookie, name):
    for part in (cookie or '').split(';'):
        key, _, value = part.strip().partition('=')
        if key == name:
            return value
    return ''


def _sig_v4(method, url, body, creds, at=None):
    """Sign one upload-gateway call. The gateway is AWS-shaped -- the same
    canonical request, scope and Authorization header -- so this is that
    algorithm and nothing TikTok-specific."""
    parts = urllib.parse.urlsplit(url)
    stamp = (at or datetime.datetime.now(datetime.timezone.utc)).strftime('%Y%m%dT%H%M%SZ')
    day = stamp[:8]
    payload = hashlib.sha256(body or b'').hexdigest()
    headers = {'x-amz-date': stamp, 'x-amz-content-sha256': payload}
    if creds.get('session_token'):
        headers['x-amz-security-token'] = creds['session_token']
    signed = ';'.join(sorted(headers))
    canonical = '\n'.join([
        method.upper(), parts.path or '/', parts.query,
        ''.join(f'{k}:{headers[k]}\n' for k in sorted(headers)),
        signed, payload])
    scope = f'{day}/{VOD_REGION}/{VOD_SERVICE}/request'
    to_sign = '\n'.join(['AWS4-HMAC-SHA256', stamp, scope,
                         hashlib.sha256(canonical.encode()).hexdigest()])
    key = ('AWS4' + (creds.get('secret_acess_key')
                     or creds.get('secret_access_key') or '')).encode()
    for step in (day, VOD_REGION, VOD_SERVICE, 'request'):
        key = hmac.new(key, step.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers['Authorization'] = (
        f'AWS4-HMAC-SHA256 Credential={creds.get("access_key_id", "")}/{scope}, '
        f'SignedHeaders={signed}, Signature={signature}')
    return headers


class Rest:
    """One account's REST side. Holds the captured session and the rate-limit
    state, so a stood-down account slows down everywhere rather than per call
    site -- same reasoning as instagram_rest.Rest."""

    def __init__(self, session, base=TIKTOK_WEB):
        session = session or {}
        self.cookie = session.get('cookie') or ''
        self.csrftoken = (session.get('csrftoken')
                          or _cookie_value(self.cookie, 'tt_csrf_token'))
        self.ms_token = (session.get('ms_token')
                         or _cookie_value(self.cookie, 'msToken'))
        self.device_id = session.get('device_id') or ''
        self.user_id = str(session.get('user_id') or '')
        self.sec_uid = session.get('sec_uid') or ''
        self.username = session.get('username') or ''
        self.user_agent = session.get('user_agent') or DEFAULT_UA
        self.proxy = session.get('proxy') or ''
        self.base = base.rstrip('/')
        self._lock = threading.Lock()
        self._until = 0.0

    def configured(self):
        return bool(self.cookie and 'sessionid' in self.cookie)

    def held(self):
        left = self._until - time.time()
        return int(left) + 1 if left > 0 else 0

    # ── Requests ─────────────────────────────────────────────────────────────

    def _headers(self, extra=None):
        h = {
            'Cookie': self.cookie,
            'User-Agent': self.user_agent,
            'Referer': TIKTOK_WEB + '/',
            'Origin': TIKTOK_WEB,
            'Accept': 'application/json, text/plain, */*',
        }
        if self.csrftoken:
            h['x-secsdk-csrf-token'] = self.csrftoken
            h['tt-csrf-token'] = self.csrftoken
        if extra:
            h.update(extra)
        return h

    def _params(self, extra=None):
        """The query every tiktok.com web call carries. TikTok reads these as
        "which client is this" and answers an empty body when they disagree
        with the session, so they are sent on reads and writes alike."""
        p = {'aid': AID, 'app_name': 'tiktok_web', 'channel': 'tiktok_web',
             'device_platform': 'web_pc', 'app_language': 'en',
             'browser_language': 'en-US', 'browser_name': 'Mozilla',
             'browser_online': 'true', 'browser_platform': 'Win32',
             'browser_version': self.user_agent, 'cookie_enabled': 'true',
             'screen_height': '1080', 'screen_width': '1920', 'region': 'US',
             'priority_region': '', 'referer': '', 'os': 'windows'}
        if self.device_id:
            p['device_id'] = self.device_id
        if self.ms_token:
            p['msToken'] = self.ms_token
        if extra:
            p.update({k: v for k, v in extra.items() if v not in (None, '')})
        return p

    def _sign(self, url):
        """Ask the signer for this URL's anti-bot params. A signer that is not
        configured, or does not answer, leaves the URL as it is rather than
        failing the call -- some routes still answer unsigned, and a 200 with
        an empty body says more than an exception raised here would."""
        if not SIGNER_URL:
            return url
        try:
            req = urllib.request.Request(
                SIGNER_URL, method='POST',
                data=json.dumps({'url': url, 'user_agent': self.user_agent}).encode())
            req.add_header('Content-Type', 'application/json')
            with urllib.request.urlopen(req, timeout=10) as resp:
                got = json.loads(resp.read() or b'{}')
        except Exception as e:
            logger.warning('tiktok signer did not answer: %s', str(e)[:120])
            return url
        extra = {k: v for k, v in (got or {}).items()
                 if k in ('X-Bogus', 'a_bogus', 'x-bogus', '_signature') and v}
        if not extra:
            return url
        joiner = '&' if '?' in url else '?'
        return url + joiner + urllib.parse.urlencode(extra)

    def _wait(self):
        while True:
            with self._lock:
                left = self._until - time.time()
                if left <= 0:
                    return
            time.sleep(min(left, 5.0))

    def call(self, method, url, body=None, headers=None, raw=False, retries=1,
             sign=True):
        """One request. `body` is a JSON document unless `raw` -- an upload
        sends bytes and signs its own content headers instead, and gets the
        longer timeout."""
        if not self.configured():
            raise TikTokApiError(0, 'No TikTok session for this persona')
        self._wait()
        if sign:
            url = self._sign(url)
        data = body if raw else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method.upper())
        for key, value in self._headers(headers).items():
            req.add_header(key, value)
        if not raw and body is not None:
            req.add_header('Content-Type', 'application/json')
        timeout = TIKTOK_UPLOAD_TIMEOUT if raw else TIKTOK_TIMEOUT
        try:
            with _opener(self.proxy).open(req, timeout=timeout) as resp:
                out = resp.read()
                if not out:
                    # TikTok's own way of refusing: a 200 with nothing in it,
                    # which means the signature or the msToken was not accepted.
                    raise TikTokApiError(200, 'TikTok answered with an empty body '
                                              '(unsigned or stale session)')
                try:
                    got = json.loads(out)
                except ValueError:
                    raise TikTokApiError(200, out[:200].decode('utf-8', 'replace'))
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
                return self.call(method, url, body, headers, raw, retries - 1, sign)
            logger.warning('tiktok %s %s -> %s: %s', method, url.split('?')[0],
                           e.code, detail[:300])
            raise TikTokApiError(e.code, detail)
        except urllib.error.URLError as e:
            raise TikTokApiError(0, str(getattr(e, 'reason', e))[:200])
        status = got.get('status_code', got.get('statusCode', 0))
        if status:
            raise TikTokApiError(int(status), str(got.get('status_msg')
                                                 or got.get('message') or '')[:300])
        return got

    def _get(self, path, params=None):
        url = f'{self.base}{path}?' + urllib.parse.urlencode(self._params(params))
        return self.call('GET', url)

    # ── Who she is ───────────────────────────────────────────────────────────

    def me(self):
        got = self._get(PATH_ME) or {}
        data = got.get('data') or got
        return {'user_id': str(data.get('user_id_str') or data.get('user_id') or ''),
                'username': data.get('username') or data.get('unique_id') or '',
                'sec_uid': data.get('sec_uid') or ''}

    # ── Uploading bytes ──────────────────────────────────────────────────────

    def _upload_creds(self):
        got = self._get(PATH_UPLOAD_AUTH) or {}
        creds = (got.get('video_token_v5') or got.get('data')
                 or {}).get('video_token_v5', got.get('video_token_v5')) or {}
        if isinstance(creds, dict) and creds.get('access_key_id'):
            return creds
        creds = (got.get('data') or {}) if isinstance(got.get('data'), dict) else {}
        if creds.get('access_key_id'):
            return creds
        raise TikTokApiError(0, 'TikTok handed back no upload credentials')

    def _vod(self, method, action, creds, params=None, body=None):
        query = {'Action': action, 'Version': '2020-11-19', 'SpaceName': VOD_SPACE}
        query.update(params or {})
        url = f'{VOD_HOST}/top/v1?' + urllib.parse.urlencode(query)
        raw = json.dumps(body).encode() if body is not None else b''
        headers = _sig_v4(method, url, raw, creds)
        headers['User-Agent'] = self.user_agent
        if body is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=(raw or None), method=method.upper())
        for key, value in headers.items():
            req.add_header(key, value)
        try:
            with _opener(self.proxy).open(req, timeout=TIKTOK_TIMEOUT) as resp:
                return json.loads(resp.read() or b'{}')
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = (e.read() or b'')[:400].decode('utf-8', 'replace')
            except Exception:
                pass
            raise TikTokApiError(e.code, detail)
        except urllib.error.URLError as e:
            raise TikTokApiError(0, str(getattr(e, 'reason', e))[:200])

    def upload(self, media_bytes, kind='video'):
        """Hand over the bytes, get back the id a post is built from.

        Three calls, the same three the web app makes: ask the gateway where
        this file goes, PUT it there, then tell the gateway the upload is
        whole. The id that comes back is a video id for a clip and an image
        uri for a photo -- TikTok keys them differently in the post itself.
        """
        creds = self._upload_creds()
        file_type = 'image' if kind == 'image' else 'video'
        apply_got = self._vod('GET', 'ApplyUploadInner', creds, params={
            'FileType': file_type, 'IsInner': '1', 'FileSize': str(len(media_bytes)),
            's': f'{random.randint(0, 10 ** 11):011d}'})
        node = ((apply_got.get('Result') or apply_got.get('data')
                 or {}).get('InnerUploadAddress') or {}).get('UploadNodes') or []
        if not node:
            raise TikTokApiError(0, 'TikTok gave this upload nowhere to go')
        node = node[0]
        store = (node.get('StoreInfos') or [{}])[0]
        host = node.get('UploadHost') or ''
        uri = store.get('StoreUri') or ''
        if not (host and uri):
            raise TikTokApiError(0, 'TikTok gave this upload nowhere to go')
        crc = format(binascii.crc32(media_bytes) & 0xFFFFFFFF, 'x')
        put = urllib.request.Request(f'https://{host}/upload/v1/{uri}',
                                     data=media_bytes, method='POST')
        for key, value in {'Authorization': store.get('Auth') or '',
                           'Content-Type': 'application/octet-stream',
                           'Content-CRC32': crc,
                           'User-Agent': self.user_agent}.items():
            put.add_header(key, value)
        try:
            with _opener(self.proxy).open(put, timeout=TIKTOK_UPLOAD_TIMEOUT) as resp:
                resp.read()
        except urllib.error.HTTPError as e:
            raise TikTokApiError(e.code, 'the upload itself was refused')
        except urllib.error.URLError as e:
            raise TikTokApiError(0, str(getattr(e, 'reason', e))[:200])
        commit = self._vod('POST', 'CommitUploadInner', creds,
                           body={'SessionKey': node.get('SessionKey') or ''})
        results = ((commit.get('Result') or commit.get('data') or {})
                   .get('Results') or [{}])
        vid = results[0].get('Vid') or results[0].get('Uri') or uri
        return {'kind': file_type, 'id': vid, 'uri': uri}

    # ── Posting ──────────────────────────────────────────────────────────────

    def _project(self):
        url = f'{self.base}{PATH_PROJECT_CREATE}?' + urllib.parse.urlencode(self._params())
        got = self.call('POST', url, body={'type': 1}) or {}
        project = (got.get('project') or got.get('data') or {})
        return str(project.get('project_id') or project.get('id') or '')

    def _post(self, caption, uploaded, schedule_at=0):
        """One post, video or photo. `uploaded` is what upload() handed back --
        one clip, or the stills of a photo post in the order they appear."""
        caption = (caption or '')[:CAPTION_CAP]
        creation_id = f'{int(time.time() * 1000)}{random.randint(100, 999)}'
        photos = [u for u in uploaded if u.get('kind') == 'image']
        one = {'batch_index': 0, 'text': caption, 'markup_text': caption,
               'text_extra': [], 'poster_delay': 0, 'visibility_type': 0,
               'allow_comment': 1, 'allow_duet': 1, 'allow_stitch': 1}
        if photos:
            one['post_type'] = 'photo'
            one['image_post_info'] = {
                'images': [{'image_id': p['id'], 'uri': p.get('uri') or p['id']}
                           for p in photos[:PHOTO_MAX]]}
        else:
            one['video_id'] = uploaded[0]['id']
        if schedule_at:
            one['schedule_time'] = int(schedule_at)
        body = {'post_common_info': {'creation_id': creation_id,
                                     'enter_post_page_from': 1,
                                     'post_type': 3},
                'feature_common_info_list': [{'geofencing_regions': [],
                                              'playlist_name': '', 'playlist_id': '',
                                              'tcm_params': '{"commerce_toggle_info":{}}',
                                              'aigc_info': {'aigc_label_type': 0}}],
                'single_post_req_list': [one]}
        project_id = self._project()
        if project_id:
            body['post_common_info']['project_id'] = project_id
        url = f'{self.base}{PATH_PROJECT_POST}?' + urllib.parse.urlencode(self._params())
        got = self.call('POST', url, body=body) or {}
        listed = (got.get('single_post_resp_list') or [{}])[0]
        return {'item_id': str(listed.get('item_id') or ''),
                'caption': caption,
                'kind': 'photo' if photos else 'video',
                'result': got}

    def post_video(self, media_bytes, caption='', schedule_at=0):
        return self._post(caption, [self.upload(media_bytes, 'video')], schedule_at)

    def post_photos(self, images, caption='', schedule_at=0):
        if not images:
            raise TikTokApiError(0, 'a photo post needs at least one still')
        uploaded = [self.upload(blob, 'image') for blob in images[:PHOTO_MAX]]
        return self._post(caption, uploaded, schedule_at)

    # ── Her own posts, and the people under them ─────────────────────────────

    def posts(self, count=12, cursor=0):
        got = self._get(PATH_POSTS, {'secUid': self.sec_uid, 'count': str(count),
                                     'cursor': str(cursor)}) or {}
        rows = []
        for item in (got.get('itemList') or [])[:count]:
            stats = item.get('stats') or {}
            rows.append({'id': str(item.get('id') or ''),
                         'caption': (item.get('desc') or '')[:200],
                         'at': int(item.get('createTime') or 0),
                         'comments': int(stats.get('commentCount') or 0),
                         'likes': int(stats.get('diggCount') or 0),
                         'views': int(stats.get('playCount') or 0)})
        return rows

    def comments(self, item_id, count=20, cursor=0):
        got = self._get(PATH_COMMENTS, {'aweme_id': str(item_id), 'count': str(count),
                                        'cursor': str(cursor)}) or {}
        rows = []
        for one in (got.get('comments') or [])[:count]:
            user = one.get('user') or {}
            rows.append({'id': str(one.get('cid') or ''),
                         'text': (one.get('text') or '')[:500],
                         'at': int(one.get('create_time') or 0),
                         'likes': int(one.get('digg_count') or 0),
                         'replies': int(one.get('reply_comment_total') or 0),
                         'mine': str(user.get('uid') or '') == self.user_id,
                         'user': user.get('unique_id') or user.get('nickname') or ''})
        return rows

    def reply(self, item_id, text, reply_id=''):
        """Answer one comment under one of her posts. TikTok treats a reply as
        a comment carrying the id of the one it answers, which is why there is
        no separate route for it."""
        params = self._params({'aweme_id': str(item_id), 'text': (text or '')[:150],
                               'text_extra': '[]', 'is_self_see': '0'})
        if reply_id:
            params['reply_id'] = str(reply_id)
            params['reply_to_reply_id'] = '0'
        url = f'{self.base}{PATH_COMMENT_PUBLISH}?' + urllib.parse.urlencode(params)
        got = self.call('POST', url, body={}) or {}
        comment = got.get('comment') or {}
        return {'id': str(comment.get('cid') or ''), 'text': text}
