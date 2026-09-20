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
import json
import os
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone

STAGING_PREFIX = 'staging'
KEPT_PREFIX = 'kept'
STAGING_DAYS = 3

_EXT = {
    'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/png': '.png',
    'image/webp': '.webp', 'video/mp4': '.mp4', 'video/webm': '.webm',
}


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
    path = f'{prefix}/{slug}/{uuid.uuid4().hex}{ext}'
    mime = mime or 'application/octet-stream'
    if backend() == 'blob':
        _blob('PUT', '?pathname=' + urllib.parse.quote(path), body=data,
              headers={'x-content-type': mime, 'x-add-random-suffix': '0',
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


def signed_url(path, ttl=None):
    """A time-boxed read URL, or None when the backend cannot mint one. A
    private Blob object is 403 to anyone without the store token, and that
    token must never reach a browser, so there the caller serves the bytes
    itself rather than redirecting."""
    if backend() == 'blob':
        return None
    return _bucket().blob(path).generate_signed_url(
        version='v4', expiration=timedelta(seconds=ttl or signed_url_ttl()),
        method='GET')


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
