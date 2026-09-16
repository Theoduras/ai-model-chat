"""Reddit REST transport for a user account, driven the same way
instagram_rest.py drives Instagram: a captured browser session, not a
registered app.

Reddit's official Data API stopped taking new registrations freely in late
2025 -- access now goes through a manual review -- and it has never been able
to reach Reddit Chat at all. So this speaks the endpoints reddit.com's own web
app speaks, with the cookie, modhash and bearer token of_connect.py takes off a
real signed-in session.

Posting media is the part with no documentation anywhere: a file goes to S3
under a lease Reddit issues, and only becomes postable once Reddit says it has
finished processing. Doing that in the wrong order is the single most common
way a submission is rejected, so _lease_upload below is deliberately explicit
about it.

Nothing here touches the database or the persona layer; app.py binds it to a
persona the way it binds instagram_rest.
"""
import json
import logging
import mimetypes
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

logger = logging.getLogger(__name__)

REDDIT_WEB = os.getenv('REDDIT_WEB_BASE', 'https://www.reddit.com')
REDDIT_OAUTH = os.getenv('REDDIT_OAUTH_BASE', 'https://oauth.reddit.com')
REDDIT_TIMEOUT = 30
# The bytes hop (app -> proxy -> S3) needs the same headroom Instagram's upload
# does; the small JSON calls would never come near it.
REDDIT_UPLOAD_TIMEOUT = int(os.getenv('REDDIT_UPLOAD_TIMEOUT', '120'))
# How long to wait for Reddit to finish processing an uploaded file before
# submitting it. Submitting early makes Reddit re-fetch the i.redd.it URL to
# validate the post, and fail it.
REDDIT_ASSET_WAIT = int(os.getenv('REDDIT_ASSET_WAIT', '60'))
DEFAULT_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

PATH_ASSET = os.getenv('REDDIT_ASSET_PATH', '/api/media/asset.json')
PATH_SUBMIT = os.getenv('REDDIT_SUBMIT_PATH', '/api/submit')
PATH_GALLERY = os.getenv('REDDIT_GALLERY_PATH', '/api/submit_gallery_post.json')
PATH_COMMENT = os.getenv('REDDIT_COMMENT_PATH', '/api/comment')


def _opener(proxy):
    """A URL opener pinned to this account's exit IP, same reasoning as
    instagram_rest._opener: an account that signs in from one country and
    posts from a datacentre in another is an account that gets checked."""
    if not (os.getenv('REDDIT_PROXY_TEMPLATE') or '').strip():
        proxy = ''
    if not proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))


def _encode_form(body):
    parts = {}
    for k, v in (body or {}).items():
        if isinstance(v, (dict, list)):
            parts[k] = json.dumps(v, separators=(',', ':'))
        elif isinstance(v, bool):
            parts[k] = 'true' if v else 'false'
        else:
            parts[k] = str(v)
    return urllib.parse.urlencode(parts).encode()


def _multipart(fields, filename, blob, mime):
    """S3 wants the lease fields as form parts, in order, with the file last.
    Sending them as headers is the mistake that makes the upload 403."""
    boundary = '----reddit' + uuid.uuid4().hex
    out = []
    for name, value in fields:
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
               f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
    out.append(blob)
    out.append(f'\r\n--{boundary}--\r\n'.encode())
    return b''.join(out), f'multipart/form-data; boundary={boundary}'


class RedditApiError(RuntimeError):
    """`code` is the HTTP status and `detail` what Reddit said, so a caller can
    tell a dead session (401/403) from a refused submission (400) or a
    subreddit's own rules turning the post away (200 with a jquery error)."""

    def __init__(self, code, detail=''):
        self.code = code
        self.detail = detail
        super().__init__(f'Reddit API {code}: {detail}'.strip())


def _api_errors(payload):
    """Reddit answers a refused submission with HTTP 200 and the reason buried
    in the legacy jquery envelope, so a 2xx is not on its own a success."""
    if not isinstance(payload, dict):
        return ''
    errors = ((payload.get('json') or {}).get('errors') or []) if payload.get('json') else []
    if errors:
        return '; '.join(str(e[1]) if isinstance(e, list) and len(e) > 1 else str(e)
                         for e in errors)[:300]
    for step in payload.get('jquery') or []:
        if isinstance(step, list) and len(step) > 3 and step[2] == 'call':
            arg = step[3]
            if isinstance(arg, list) and arg and isinstance(arg[0], str) and '.error.' in str(step):
                return arg[0][:300]
    return ''


class Rest:
    """One account's REST side. Holds the captured session and the rate-limit
    state, so a stood-down account slows down everywhere rather than per call
    site -- same reasoning as discord_rest.Rest."""

    def __init__(self, session, base=REDDIT_WEB):
        session = session or {}
        self.cookie = session.get('cookie') or ''
        self.modhash = session.get('modhash') or ''
        self.bearer = session.get('bearer') or ''
        self.username = session.get('username') or ''
        self.user_id = session.get('user_id') or ''
        self.user_agent = session.get('user_agent') or DEFAULT_UA
        self.proxy = session.get('proxy') or ''
        self.base = base.rstrip('/')
        self._lock = threading.Lock()
        self._until = 0.0

    def configured(self):
        return bool(self.cookie)

    def held(self):
        left = self._until - time.time()
        return int(left) + 1 if left > 0 else 0

    def _headers(self, extra=None):
        h = {
            'Cookie': self.cookie,
            'User-Agent': self.user_agent,
            'Origin': REDDIT_WEB,
            'Referer': REDDIT_WEB + '/',
            'Accept': 'application/json',
        }
        if self.modhash:
            h['X-Modhash'] = self.modhash
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

    def call(self, method, url, body=None, headers=None, raw=False, retries=1,
             content_type='', bare=False):
        """One request. `bare` sends no Reddit credentials at all -- the S3
        upload is a request to Amazon, and handing her session cookie to a
        third party because the helper happens to add it by default is how a
        credential leaks."""
        if not self.configured():
            raise RedditApiError(0, 'No Reddit session for this persona')
        self._wait()
        data = body if raw else (_encode_form(body) if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method.upper())
        sent = ({'User-Agent': self.user_agent, **(headers or {})} if bare
                else self._headers(headers))
        for key, value in sent.items():
            req.add_header(key, value)
        if data is not None:
            req.add_header('Content-Type',
                           content_type or 'application/x-www-form-urlencoded')
        timeout = REDDIT_UPLOAD_TIMEOUT if raw else REDDIT_TIMEOUT
        try:
            with _opener(self.proxy).open(req, timeout=timeout) as resp:
                out = resp.read()
                if not out:
                    return {}
                try:
                    return json.loads(out)
                except ValueError:
                    return {'_body': out[:2000].decode('utf-8', 'replace')}
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
                return self.call(method, url, body, headers, raw, retries - 1,
                                 content_type, bare)
            logger.warning('reddit %s %s -> %s: %s', method, url, e.code, detail[:300])
            raise RedditApiError(e.code, detail)
        except urllib.error.URLError as e:
            raise RedditApiError(0, str(getattr(e, 'reason', e))[:200])

    def _post(self, path, body):
        payload = self.call('POST', f'{self.base}{path}',
                            body=dict(body, api_type='json', raw_json=1))
        why = _api_errors(payload)
        if why:
            raise RedditApiError(400, why)
        return payload

    # ── Who she is ───────────────────────────────────────────────────────────

    def me(self):
        return self.call('GET', f'{self.base}/api/me.json').get('data') or {}

    # ── Media ────────────────────────────────────────────────────────────────

    def _lease_upload(self, blob, mime, filename=''):
        """Bytes in, a postable Reddit URL out.

        Three steps, in this order, because Reddit validates the URL by
        fetching it: ask for an S3 lease, push the file to S3 under that
        lease's own form fields, then wait until Reddit reports the asset
        processed. Skipping the wait is what makes a submission fail with a
        message about the image not being reachable.
        """
        mime = mime or 'application/octet-stream'
        filename = filename or f'{uuid.uuid4().hex}.{(mimetypes.guess_extension(mime) or ".bin").lstrip(".")}'
        lease = self.call('POST', f'{self.base}{PATH_ASSET}',
                          body={'filepath': filename, 'mimetype': mime})
        args = (lease or {}).get('args') or {}
        action = args.get('action') or ''
        if action.startswith('//'):
            action = 'https:' + action
        fields = [(f.get('name'), f.get('value')) for f in (args.get('fields') or [])
                  if f.get('name')]
        if not (action and fields):
            raise RedditApiError(0, 'Reddit issued no upload lease for that file')
        payload, content_type = _multipart(fields, filename, blob, mime)
        self.call('POST', action, body=payload, raw=True, content_type=content_type,
                  bare=True)
        key = dict(fields).get('key') or ''
        asset = (lease or {}).get('asset') or {}
        self._await_asset(asset.get('asset_id') or '')
        return f'{action.rstrip("/")}/{key}', asset.get('asset_id') or ''

    def _await_asset(self, asset_id):
        """Poll until Reddit has finished with the upload.

        The lease also hands back a websocket to listen on, but a poll needs no
        second protocol in the process and no socket held open across a
        Cloud Run restart -- and the answer is the same one.
        """
        if not asset_id:
            return
        deadline = time.time() + REDDIT_ASSET_WAIT
        while time.time() < deadline:
            try:
                status = self.call('GET', f'{self.base}/api/media/asset/{asset_id}')
            except RedditApiError as e:
                # The status route moves with Reddit's releases. Losing it means
                # we cannot see the answer, not that the answer was no -- so
                # give the upload a moment and let the submission report the
                # real verdict, rather than failing a post that would work.
                logger.warning('reddit asset status unavailable (%s); waiting instead', e.code)
                time.sleep(5)
                return
            state = str((status or {}).get('processing_state') or '').lower()
            if state in ('complete', 'valid', ''):
                return
            if state == 'failed':
                raise RedditApiError(0, 'Reddit could not process that file')
            time.sleep(2)
        logger.warning('reddit asset %s still processing after %ss', asset_id,
                       REDDIT_ASSET_WAIT)

    # ── Submitting ───────────────────────────────────────────────────────────

    def _submit(self, sub, title, kind, flair_id='', nsfw=True, spoiler=False,
                **extra):
        body = {'sr': str(sub).lstrip('/').removeprefix('r/'), 'title': title[:300],
                'kind': kind, 'nsfw': bool(nsfw), 'spoiler': bool(spoiler),
                'sendreplies': True, 'validate_on_submit': True}
        if flair_id:
            body['flair_id'] = flair_id
        body.update(extra)
        return self._post(PATH_SUBMIT, body)

    def submit_text(self, sub, title, body='', flair_id='', nsfw=True):
        return self._submit(sub, title, 'self', flair_id, nsfw, text=body or '')

    def submit_link(self, sub, title, url, flair_id='', nsfw=True):
        return self._submit(sub, title, 'link', flair_id, nsfw, url=url)

    def submit_image(self, sub, title, blob, mime='image/jpeg', flair_id='', nsfw=True):
        url, _ = self._lease_upload(blob, mime)
        return self._submit(sub, title, 'image', flair_id, nsfw, url=url)

    def submit_video(self, sub, title, blob, mime='video/mp4', cover=None,
                     cover_mime='image/jpeg', flair_id='', nsfw=True):
        """Reddit will not take a video without a poster frame, so one is
        uploaded alongside it; without a real cover it falls back to Reddit's
        own placeholder rather than refusing the post."""
        url, _ = self._lease_upload(blob, mime)
        poster = ''
        if cover:
            poster, _ = self._lease_upload(cover, cover_mime)
        return self._submit(sub, title, 'video', flair_id, nsfw, url=url,
                            video_poster_url=poster or
                            'https://www.redditstatic.com/mweb2x/img/video_thumbnail.png')

    def submit_gallery(self, sub, title, items, flair_id='', nsfw=True):
        """`items` is a list of (blob, mime, caption) -- Reddit's gallery
        endpoint takes the assets by id rather than by URL, unlike every other
        submission kind."""
        media = []
        for blob, mime, caption in items:
            _, asset_id = self._lease_upload(blob, mime or 'image/jpeg')
            media.append({'media_id': asset_id, 'caption': (caption or '')[:180],
                          'outbound_url': ''})
        body = {'sr': str(sub).lstrip('/').removeprefix('r/'), 'title': title[:300],
                'items': media, 'nsfw': bool(nsfw), 'spoiler': False,
                'sendreplies': True, 'validate_on_submit': True,
                'show_error_list': True, 'api_type': 'json'}
        if flair_id:
            body['flair_id'] = flair_id
        payload = self.call('POST', f'{self.base}{PATH_GALLERY}', body=body)
        why = _api_errors(payload)
        if why:
            raise RedditApiError(400, why)
        return payload

    # ── Reading and replying ─────────────────────────────────────────────────

    def flairs(self, sub):
        """What flairs this subreddit offers. Most NSFW subs auto-remove a post
        without one, so the console has to be able to show the real list."""
        sub = str(sub).lstrip('/').removeprefix('r/')
        got = self.call('GET', f'{self.base}/r/{sub}/api/link_flair_v2.json')
        rows = got if isinstance(got, list) else []
        return [{'id': r.get('id') or '', 'text': r.get('text') or '',
                 'editable': bool(r.get('text_editable'))} for r in rows]

    def comment(self, thing_id, text):
        return self._post(PATH_COMMENT, {'thing_id': thing_id, 'text': text})

    def my_posts(self, username='', limit=25):
        name = username or self.username
        if not name:
            return []
        got = self.call('GET', f'{self.base}/user/{name}/submitted.json?limit={int(limit)}')
        return [c.get('data') or {} for c in ((got or {}).get('data') or {}).get('children') or []]

    def post_comments(self, article_id, limit=100):
        """Every comment on one of her posts, flattened -- the tree shape does
        not matter when the question is only which ones are new."""
        article = str(article_id).removeprefix('t3_')
        got = self.call('GET', f'{self.base}/comments/{article}.json?limit={int(limit)}')
        out = []

        def walk(node):
            if not isinstance(node, dict):
                return
            data = node.get('data') or {}
            if node.get('kind') == 't1' and data.get('id'):
                out.append(data)
            children = data.get('children')
            replies = data.get('replies')
            if isinstance(replies, dict):
                walk(replies)
            for child in (children if isinstance(children, list) else []):
                walk(child)

        for listing in (got if isinstance(got, list) else []):
            walk(listing)
        return out

    def inbox_mentions(self, limit=25):
        """Username mentions and comment replies waiting in her inbox."""
        out = []
        for path in ('/message/mentions.json', '/message/unread.json'):
            try:
                got = self.call('GET', f'{self.base}{path}?limit={int(limit)}')
            except RedditApiError:
                continue
            for child in ((got or {}).get('data') or {}).get('children') or []:
                data = child.get('data') or {}
                if data.get('name'):
                    out.append(data)
        seen, rows = set(), []
        for row in out:
            if row['name'] not in seen:
                seen.add(row['name'])
                rows.append(row)
        return rows

    def mark_read(self, names):
        names = [n for n in (names or []) if n]
        if not names:
            return
        try:
            self.call('POST', f'{self.base}/api/read_message',
                      body={'id': ','.join(names)})
        except RedditApiError as e:
            logger.debug('reddit mark_read failed: %s', str(e)[:120])
