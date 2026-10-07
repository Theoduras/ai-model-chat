"""Object storage for generated media.

Generated stills and clips are too big for the data-URL column the vault has
always used — a 720p clip is tens of megabytes — and neither host keeps a disk,
so the bytes go to an object store.

Two backends behind one interface, because the two hosts have different ones to
hand: GCS on Cloud Run, which holds the service account already, and Vercel
Blob on Vercel, which has no GCP credentials at all. The backend is chosen by
whichever is configured rather than by naming the host, so a local run with
either set behaves like that host.

Two prefixes carry the whole keep-or-lose flow:

    staging/<slug>/...   what a generation just produced
    kept/<slug>/...      what the creator chose to keep

GCS expires staging/ with a bucket lifecycle rule, which costs nothing and
cannot be missed by a worker that was not running. Blob has no such thing, so
there `purge_staging()` does it and the cron has to call it — see
`_gen_ensure_lifecycle` in app.py.
"""
import base64
import io
import json
import logging
import os
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger('storage')

STAGING_PREFIX = 'staging'
KEPT_PREFIX = 'kept'
# The studio's example photos: a demo model, never anyone's media. Nothing
# that sends, posts or references a photo may read from here.
EXAMPLES_PREFIX = 'examples'
STAGING_DAYS = 3

_EXT = {
    'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/png': '.png',
    'image/webp': '.webp', 'video/mp4': '.mp4', 'video/webm': '.webm',
}


def is_example(path):
    return (path or '').startswith(EXAMPLES_PREFIX + '/')


def mime_of(path):
    ext = os.path.splitext(path or '')[1].lower()
    return next((m for m, e in _EXT.items() if e == ext), 'application/octet-stream')


BLOB_API = 'https://blob.vercel-storage.com'
BLOB_API_VERSION = '11'


def bucket_name():
    return (os.getenv('GCS_BUCKET') or '').strip()


def _blob_token():
    return (os.getenv('BLOB_READ_WRITE_TOKEN') or '').strip()


def backend():
    """Which store is configured. An explicit STORAGE_BACKEND wins, so a host
    holding credentials for both can still be pinned to one."""
    pick = (os.getenv('STORAGE_BACKEND') or '').strip().lower()
    if pick in ('gcs', 'blob'):
        return pick
    if bucket_name():
        return 'gcs'
    if _blob_token():
        return 'blob'
    return ''


def enabled():
    return bool(backend())


def _blob(method, path, body=None, headers=None, timeout=120):
    import requests
    h = {'authorization': 'Bearer ' + _blob_token(),
         'x-api-version': BLOB_API_VERSION}
    h.update(headers or {})
    resp = requests.request(method, BLOB_API + path, data=body, headers=h,
                            timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f'blob {method} {path.split("?")[0]} '
                           f'failed ({resp.status_code}): {resp.text[:200]}')
    return resp


def signed_url_ttl():
    try:
        return int(os.getenv('GCS_SIGNED_URL_TTL') or 3600)
    except ValueError:
        return 3600


_client = None


def _bucket():
    global _client
    name = bucket_name()
    if not name:
        raise RuntimeError('GCS_BUCKET is not set')
    if _client is None:
        from google.cloud import storage as gcs
        _client = gcs.Client()
    return _client.bucket(name)


def staging_expiry():
    return datetime.now(timezone.utc) + timedelta(days=STAGING_DAYS)


def put(slug, data, mime, prefix=STAGING_PREFIX):
    """Upload bytes and return the object path. Callers keep the path, never a
    URL: a URL either expires or, on a private Blob store, needs a credential
    the caller has no business holding."""
    ext = _EXT.get((mime or '').lower(), '.bin')
    return replace(f'{prefix}/{slug}/{uuid.uuid4().hex}{ext}', data, mime)


_COMPRESS = {'image/jpeg': ('JPEG', {'quality': 90, 'optimize': True, 'progressive': True}),
             'image/jpg': ('JPEG', {'quality': 90, 'optimize': True, 'progressive': True}),
             'image/png': ('PNG', {'optimize': True}),
             'image/webp': ('WEBP', {'quality': 90, 'method': 6})}


# Long-side cap for stored photos: sharp on any phone or DM, and a fraction of
# a camera original's bytes.
MAX_IMAGE_SIDE = 2048


_PIL_MIME = {'JPEG': 'image/jpeg', 'MPO': 'image/jpeg', 'PNG': 'image/png',
             'WEBP': 'image/webp'}


def _open_image(data):
    from PIL import Image
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    return Image.open(io.BytesIO(data))


def sniff_mime(data):
    """The image type the bytes really are, whatever they were stored as."""
    try:
        return _PIL_MIME.get(_open_image(data).format, '')
    except Exception:
        return ''


def shrink(data, mime=''):
    """The admin compress pass: (bytes, mime, note). Unlike compress_image this
    may change the format — an opaque PNG, HEIC or WebP photo becomes JPEG —
    so it is only for callers that store the returned mime with the bytes."""
    from PIL import Image, ImageOps
    if not data:
        return data, mime, 'empty'
    try:
        img = _open_image(data)
        img.load()
    except Exception:
        return data, mime, ('unsupported format ' + mime if (mime or '').startswith('image/')
                            else 'not an image')
    if getattr(img, 'is_animated', False):
        return data, mime, 'animated, left as is'
    src = img.format or '?'
    img = ImageOps.exif_transpose(img)
    notes, side = [], max(img.size)
    if side > MAX_IMAGE_SIDE:
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.LANCZOS)
        notes.append(f'resized {side}px → {MAX_IMAGE_SIDE}')
    alpha = img.mode in ('RGBA', 'LA', 'PA') or (img.mode == 'P' and 'transparency' in img.info)
    if alpha:
        try:
            alpha = img.convert('RGBA').getchannel('A').getextrema()[0] < 255
        except Exception:
            pass
    out = io.BytesIO()
    if alpha:
        img.save(out, 'PNG', optimize=True)
        new_mime = 'image/png'
    else:
        img.convert('RGB').save(out, 'JPEG', quality=85, optimize=True, progressive=True)
        new_mime = 'image/jpeg'
        if src not in ('JPEG', 'MPO'):
            notes.append(f'converted {src} → JPEG')
    out = out.getvalue()
    if len(out) >= len(data):
        return data, mime, 'already small'
    return out, new_mime, ', '.join(notes) or 're-saved smaller'


def compress_image(data, mime):
    """Smaller bytes in the same format, at most MAX_IMAGE_SIDE on the long side,
    with metadata (EXIF, GPS) dropped, or the original when that would not
    shrink it."""
    fmt = _COMPRESS.get((mime or '').lower())
    if not fmt and data:
        fmt = _COMPRESS.get(sniff_mime(data))
    if not fmt or not data:
        return data
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(data))
        if getattr(img, 'is_animated', False):
            return data
        img = ImageOps.exif_transpose(img)
        if max(img.size) > MAX_IMAGE_SIDE:
            img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.LANCZOS)
        if fmt[0] == 'JPEG' and img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img.info.pop('exif', None)
        img.info.pop('icc_profile', None) if fmt[0] == 'PNG' else None
        out = io.BytesIO()
        img.save(out, fmt[0], **fmt[1])
        out = out.getvalue()
        return out if len(out) < len(data) else data
    except Exception:
        logger.warning('image compress skipped', exc_info=True)
        return data


def compress_data_url(url):
    if not isinstance(url, str) or not url.startswith('data:image/') or ';base64,' not in url:
        return url
    head, b64 = url.split(',', 1)
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return url
    out = compress_image(raw, head[5:].split(';')[0])
    return url if out is raw else head + ',' + base64.b64encode(out).decode()


def replace(path, data, mime, compress=True):
    """Write bytes at a path this server already chose, overwriting it."""
    mime = mime or 'application/octet-stream'
    if compress:
        data = compress_image(data, mime)
    if backend() == 'blob':
        _blob('PUT', '?pathname=' + urllib.parse.quote(path), body=data,
              headers={'x-content-type': mime, 'x-add-random-suffix': '0',
                       'x-allow-overwrite': '1',
                       'x-vercel-blob-access': 'private'})
        return path
    _bucket().blob(path).upload_from_string(data, content_type=mime)
    return path


def get(path):
    if backend() == 'blob':
        # The bytes come from the store's own host, not the API — the API's
        # ?url= form answers with the blob's metadata, not its content.
        import requests
        resp = requests.get(_blob_url(path), timeout=120,
                            headers={'authorization': 'Bearer ' + _blob_token()})
        if resp.status_code >= 400:
            raise RuntimeError(f'blob download failed ({resp.status_code})')
        return resp.content
    return _bucket().blob(path).download_as_bytes()


def delete(path):
    try:
        if backend() == 'blob':
            _blob('POST', '/delete', body=json.dumps({'urls': [_blob_url(path)]}),
                  headers={'content-type': 'application/json'})
            return
        _bucket().blob(path).delete()
    except Exception:
        pass


_blob_host = [None]


def _blob_url(path):
    """A private Blob store serves from a host derived from its store id, which
    the token carries. Read it once from a list call rather than hard-coding a
    hostname that changes with the store."""
    if _blob_host[0] is None:
        body = _blob('GET', '?limit=1').json()
        blobs = body.get('blobs') or []
        if blobs:
            _blob_host[0] = urllib.parse.urlparse(blobs[0]['url']).netloc
        else:
            # Nothing stored yet: round-trip a marker to learn the host.
            r = _blob('PUT', '?pathname=.host-probe',
                      body=b'x', headers={'x-content-type': 'text/plain',
                                          'x-add-random-suffix': '0',
                                          'x-vercel-blob-access': 'private'})
            _blob_host[0] = urllib.parse.urlparse(r.json()['url']).netloc
    return f'https://{_blob_host[0]}/{path}'


def promote(path):
    """Move an object out of staging so nothing expires it any more. Returns
    the new path; a path already outside staging is returned as-is."""
    if not path or not path.startswith(STAGING_PREFIX + '/'):
        return path
    dest = KEPT_PREFIX + path[len(STAGING_PREFIX):]
    if backend() == 'blob':
        _blob('PUT', '?pathname=' + urllib.parse.quote(dest) +
              '&fromUrl=' + urllib.parse.quote(_blob_url(path)),
              headers={'x-add-random-suffix': '0',
                       'x-vercel-blob-access': 'private'})
        delete(path)
        return dest
    bucket = _bucket()
    src = bucket.blob(path)
    bucket.copy_blob(src, bucket, dest)
    try:
        src.delete()
    except Exception:
        pass
    return dest


_signer = {'checked': False, 'email': '', 'creds': None}


def _iam_signer():
    """Cloud Run's credentials come from the metadata server and carry no
    private key, so the library cannot sign a URL with them on its own. Passing
    the service account's own email and a fresh access token makes it sign
    through the IAM signBlob API instead, which needs
    roles/iam.serviceAccountTokenCreator on itself."""
    if not _signer['checked']:
        _signer['checked'] = True
        try:
            import google.auth
            from google.auth.transport.requests import Request
            creds, _ = google.auth.default()
            creds.refresh(Request())
            email = getattr(creds, 'service_account_email', '')
            if not email or email == 'default':
                # Compute credentials report 'default' rather than the address
                # signBlob needs; the metadata server knows the real one.
                import requests
                email = requests.get(
                    'http://metadata.google.internal/computeMetadata/v1/'
                    'instance/service-accounts/default/email',
                    headers={'Metadata-Flavor': 'Google'}, timeout=3).text.strip()
            if email and email != 'default':
                _signer.update(email=email, creds=creds)
        except Exception:
            logger.exception('could not prepare an IAM signer')
    return _signer['email'], _signer['creds']


def signed_url(path, ttl=None, disposition=None):
    """A time-boxed read URL, or None when the backend cannot mint one — the
    caller then serves the bytes itself. A private Blob object is 403 to anyone
    without the store token, and that token must never reach a browser. On GCS
    signing can fail for want of a key or the IAM role, and a redirect that
    cannot be built is not worth a 502 on an image that is right there."""
    if backend() == 'blob':
        return None
    blob = _bucket().blob(path)
    expiry = timedelta(seconds=ttl or signed_url_ttl())
    extra = {'response_disposition': disposition} if disposition else {}
    try:
        return blob.generate_signed_url(version='v4', expiration=expiry,
                                        method='GET', **extra)
    except Exception:
        email, creds = _iam_signer()
        if not email:
            return None
        try:
            return blob.generate_signed_url(
                version='v4', expiration=expiry, method='GET',
                service_account_email=email, access_token=creds.token, **extra)
        except Exception:
            logger.exception('could not sign a URL for %s', path)
            return None


def signed_upload_url(path, mime, ttl=None):
    """A time-boxed PUT URL the browser uploads to directly, or None when the
    backend cannot mint one.

    Cloud Run refuses a request body over 32 MiB before it reaches the app, so
    a clip of any real length cannot be posted through the server at all. This
    is the way around that: the bytes go from the browser to the bucket and the
    server only ever sees the path. The bucket must allow the site's origin in
    its CORS config for a browser to use it; a caller that gets None here posts
    through the server instead and lives with the cap.
    """
    if backend() != 'gcs':
        return None
    blob = _bucket().blob(path)
    expiry = timedelta(seconds=ttl or signed_url_ttl())
    kw = dict(version='v4', expiration=expiry, method='PUT',
              content_type=mime or 'application/octet-stream')
    try:
        return blob.generate_signed_url(**kw)
    except Exception:
        email, creds = _iam_signer()
        if not email:
            return None
        try:
            return blob.generate_signed_url(service_account_email=email,
                                            access_token=creds.token, **kw)
        except Exception:
            logger.exception('could not sign an upload URL for %s', path)
            return None


def reserve(slug, mime, prefix=STAGING_PREFIX):
    """The path an upload will land at, without uploading anything. The naming
    has to match put() exactly, because the two are the same object seen from
    either end."""
    ext = _EXT.get((mime or '').lower(), '.bin')
    return f'{prefix}/{slug}/{uuid.uuid4().hex}{ext}'


def read_range(path, start, length):
    """Some bytes out of a stored object. `start` may be negative, meaning that
    many bytes from the end — which is where an MP4 written without faststart
    keeps the header we need to read."""
    if backend() == 'blob':
        import requests
        if start < 0:
            rng = f'bytes={start}'
        else:
            rng = f'bytes={start}-{start + length - 1}'
        resp = requests.get(_blob_url(path), timeout=120,
                            headers={'authorization': 'Bearer ' + _blob_token(),
                                     'range': rng})
        if resp.status_code >= 400:
            raise RuntimeError(f'blob range read failed ({resp.status_code})')
        return resp.content
    blob = _bucket().blob(path)
    if start < 0:
        blob.reload()
        size = int(blob.size or 0)
        start = max(0, size + start)
        length = min(length, size - start)
        if length <= 0:
            return b''
    return blob.download_as_bytes(start=start, end=start + length - 1)


def size_of(path):
    if backend() == 'blob':
        import requests
        resp = requests.get(_blob_url(path), timeout=60, stream=True,
                            headers={'authorization': 'Bearer ' + _blob_token()})
        return int(resp.headers.get('content-length') or 0)
    blob = _bucket().blob(path)
    blob.reload()
    return int(blob.size or 0)


def all_sizes():
    """Every stored object's size by path, from one listing rather than a
    request per file."""
    if backend() == 'blob':
        out, cursor = {}, None
        while True:
            q = '?limit=1000' + ('&cursor=' + urllib.parse.quote(cursor) if cursor else '')
            body = _blob('GET', q).json()
            out.update({b['pathname']: int(b.get('size') or 0) for b in body.get('blobs') or []})
            cursor = body.get('cursor')
            if not body.get('hasMore') or not cursor:
                return out
    if backend() == 'gcs':
        return {b.name: int(b.size or 0) for b in _bucket().list_blobs()}
    return {}


def purge_staging(days=STAGING_DAYS):
    """Delete staged objects past their window. Only Blob needs this — GCS has
    a lifecycle rule doing it for free — so it is a no-op elsewhere, and it is
    the cron's job to call it because no loop is guaranteed to be running."""
    if backend() != 'blob':
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    gone, cursor = 0, None
    while True:
        q = '?prefix=' + urllib.parse.quote(STAGING_PREFIX + '/') + '&limit=500'
        if cursor:
            q += '&cursor=' + urllib.parse.quote(cursor)
        body = _blob('GET', q).json()
        stale = []
        for b in body.get('blobs') or []:
            at = (b.get('uploadedAt') or '').replace('Z', '+00:00')
            try:
                if datetime.fromisoformat(at) < cutoff:
                    stale.append(b['url'])
            except ValueError:
                continue
        if stale:
            _blob('POST', '/delete', body=json.dumps({'urls': stale}),
                  headers={'content-type': 'application/json'})
            gone += len(stale)
        cursor = body.get('cursor')
        if not body.get('hasMore') or not cursor:
            return gone


def ensure_lifecycle():
    """Install the staging auto-delete rule. Idempotent, and safe to call at
    boot: a bucket that already carries the rule is left alone. Blob has no
    lifecycle rules at all, so there this reports nothing to install and
    purge_staging carries the window instead."""
    if backend() != 'gcs':
        return False
    bucket = _bucket()
    bucket.reload()
    rules = list(bucket.lifecycle_rules or [])
    for rule in rules:
        cond = rule.get('condition') or {}
        if (rule.get('action') or {}).get('type') == 'Delete' \
                and cond.get('matchesPrefix') == [STAGING_PREFIX + '/']:
            if cond.get('age') == STAGING_DAYS:
                return False
            rules.remove(rule)
            break
    rules.append({'action': {'type': 'Delete'},
                  'condition': {'age': STAGING_DAYS,
                                'matchesPrefix': [STAGING_PREFIX + '/']}})
    bucket.lifecycle_rules = rules
    bucket.patch()
    return True
