"""TikTok's Content Posting API, spoken with a bearer token.

This replaced a transport that drove tiktok.com's own web endpoints with a
captured session. That could not work: every web call carries a signature
TikTok's JavaScript computes, which a stored cookie cannot carry, so the calls
came back as empty 200s. Everything here is documented, declared and allowed --
the trade is that TikTok decides what an unaudited app may do, not us.

Two ways in, one code path:

  inbox   the video lands in her TikTok drafts and she taps publish in the app.
          Scope video.upload. No audit, no restrictions, and she picks the
          privacy herself -- which is why it is the default.
  direct  published straight to her profile. Scope video.publish, and only
          useful once TikTok has audited the app: before that every post is
          SELF_ONLY and at most five accounts a day may post at all.

Nothing here touches the database or the persona layer; app.py binds it to the
persona the way it binds reddit_rest.py.
"""
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

API = os.getenv('TIKTOK_API_BASE', 'https://open.tiktokapis.com/v2')
TIMEOUT = 30
# The upload is the one call carrying real file bytes, and 30s was timing out
# on it long before the small JSON calls ever would.
UPLOAD_TIMEOUT = int(os.getenv('TIKTOK_UPLOAD_TIMEOUT', '300'))

CAPTION_CAP = 2200
# TikTok's own chunking rules: a chunk is 5MB-64MB, a video under 5MB must go
# up whole, and only past 64MB does it have to be split at all.
CHUNK_MIN = 5 * 1024 * 1024
CHUNK_MAX = 64 * 1024 * 1024


class TikTokApiError(RuntimeError):
    """`code` is the HTTP status (or TikTok's own error code) and `detail` what
    it said, so a caller can tell a dead token from a rejected video."""

    def __init__(self, code, detail=''):
        self.code = code
        self.detail = detail
        super().__init__(f'TikTok API {code}: {detail}'.strip())


class Rest:
    """One account's side of the Content Posting API."""

    def __init__(self, session, base=API):
        session = session or {}
        self.token = session.get('access_token') or ''
        self.open_id = session.get('open_id') or ''
        self.base = base.rstrip('/')

    def configured(self):
        return bool(self.token)

    def call(self, method, path, body=None):
        if not self.configured():
            raise TikTokApiError(0, 'No TikTok token for this persona')
        url = path if path.startswith('http') else f'{self.base}{path}'
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method.upper())
        req.add_header('Authorization', f'Bearer {self.token}')
        req.add_header('Content-Type', 'application/json; charset=UTF-8')
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                got = json.loads(resp.read() or b'{}')
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = (e.read() or b'')[:400].decode('utf-8', 'replace')
            except Exception:
                pass
            logger.warning('tiktok %s %s -> %s: %s', method, path, e.code, detail[:300])
            raise TikTokApiError(e.code, detail)
        except urllib.error.URLError as e:
            raise TikTokApiError(0, str(getattr(e, 'reason', e))[:200])
        # TikTok answers 200 with the refusal inside the envelope, so the body
        # is what decides whether this worked.
        err = (got.get('error') or {}) if isinstance(got, dict) else {}
        if err.get('code') and err['code'] != 'ok':
            raise TikTokApiError(err['code'],
                                 str(err.get('message') or '')[:300])
        return got.get('data') or {}

    # ── Who she is ───────────────────────────────────────────────────────────

    def creator_info(self):
        """Her posting profile: the display name the console shows, and, for a
        direct post, which privacy levels her account actually allows. It
        doubles as the liveness check, because it is the cheapest authenticated
        call the posting API has."""
        got = self.call('POST', '/post/publish/creator_info/query/', body={})
        return {
            'username': got.get('creator_username') or '',
            'nickname': got.get('creator_nickname') or '',
            'privacy_options': got.get('privacy_level_options') or [],
            'max_seconds': int(got.get('max_video_post_duration_sec') or 0),
            'comment_off': bool(got.get('comment_disabled')),
            'duet_off': bool(got.get('duet_disabled')),
            'stitch_off': bool(got.get('stitch_disabled')),
        }

    # ── Posting ──────────────────────────────────────────────────────────────

    def _chunking(self, size):
        """(chunk_size, total_chunk_count) for a video of this size."""
        if size <= CHUNK_MAX:
            # Whole, in one PUT. Anything under 5MB *must* go this way, and
            # everything the library holds inline is well under 64MB.
            return size, 1
        count = size // CHUNK_MAX
        return CHUNK_MAX, max(1, int(count))

    def _privacy(self, wanted=''):
        allowed = self.creator_info().get('privacy_options') or []
        if wanted and wanted in allowed:
            return wanted
        for option in ('PUBLIC_TO_EVERYONE', 'MUTUAL_FOLLOW_FRIENDS',
                       'FOLLOWER_OF_CREATOR', 'SELF_ONLY'):
            if option in allowed:
                return option
        # An unaudited app is only ever given SELF_ONLY, and saying so plainly
        # beats sending a level TikTok will reject.
        return 'SELF_ONLY'

    def post_video(self, blob, caption='', direct=False, privacy=''):
        """Put one video up. Returns the publish id and which way it went.

        `direct=False` leaves it in her drafts for her to publish; `direct=True`
        posts it, and needs the audited scope to reach anyone but her.
        """
        if not blob:
            raise TikTokApiError(0, 'that video has no bytes')
        size = len(blob)
        chunk_size, chunks = self._chunking(size)
        source = {'source': 'FILE_UPLOAD', 'video_size': size,
                  'chunk_size': chunk_size, 'total_chunk_count': chunks}
        if direct:
            body = {'post_info': {'title': (caption or '')[:CAPTION_CAP],
                                  'privacy_level': self._privacy(privacy),
                                  'disable_comment': False,
                                  'disable_duet': False,
                                  'disable_stitch': False},
                    'source_info': source}
            path = '/post/publish/video/init/'
        else:
            # The caption rides with the video into her drafts, where she can
            # edit it before it goes out -- the inbox init takes no post_info.
            body = {'source_info': source}
            path = '/post/publish/inbox/video/init/'
        got = self.call('POST', path, body=body)
        publish_id = got.get('publish_id') or ''
        upload_url = got.get('upload_url') or ''
        if not (publish_id and upload_url):
            raise TikTokApiError(0, 'TikTok gave this upload nowhere to go')
        self._send(upload_url, blob, chunk_size, chunks)
        return {'publish_id': publish_id, 'mode': 'direct' if direct else 'inbox',
                'caption': (caption or '')[:CAPTION_CAP]}

    def _send(self, upload_url, blob, chunk_size, chunks):
        size = len(blob)
        for index in range(chunks):
            first = index * chunk_size
            # The last chunk carries whatever is left over, which is why it may
            # be larger than chunk_size rather than a short one.
            last = size - 1 if index == chunks - 1 else first + chunk_size - 1
            part = blob[first:last + 1]
            req = urllib.request.Request(upload_url, data=part, method='PUT')
            req.add_header('Content-Type', 'video/mp4')
            req.add_header('Content-Length', str(len(part)))
            req.add_header('Content-Range', f'bytes {first}-{last}/{size}')
            try:
                with urllib.request.urlopen(req, timeout=UPLOAD_TIMEOUT) as resp:
                    resp.read()
            except urllib.error.HTTPError as e:
                detail = ''
                try:
                    detail = (e.read() or b'')[:300].decode('utf-8', 'replace')
                except Exception:
                    pass
                raise TikTokApiError(e.code, f'the upload itself was refused: {detail}')
            except urllib.error.URLError as e:
                raise TikTokApiError(0, str(getattr(e, 'reason', e))[:200])

    def status(self, publish_id):
        """Where a post got to. TikTok processes after the upload finishes, so
        a video that uploaded fine can still fail here."""
        got = self.call('POST', '/post/publish/status/fetch/',
                        body={'publish_id': publish_id})
        return {'status': got.get('status') or '',
                'fail_reason': got.get('fail_reason') or '',
                'post_ids': got.get('publicaly_available_post_id') or []}
