"""Object storage for generated media.

Generated stills and clips are too big for the data-URL column the vault has
always used — a 720p clip is tens of megabytes — and Cloud Run's filesystem is
in memory, so the bytes go to GCS.

Two prefixes carry the whole keep-or-lose flow:

    staging/<slug>/...   what a generation just produced
    kept/<slug>/...      what the creator chose to keep

A bucket lifecycle rule deletes anything under staging/ after STAGING_DAYS, so
the auto-purge costs nothing and cannot be missed by a worker that was not
running. `ensure_lifecycle()` installs that rule; without it nothing expires.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

STAGING_PREFIX = 'staging'
KEPT_PREFIX = 'kept'
STAGING_DAYS = 3

_EXT = {
    'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/png': '.png',
    'image/webp': '.webp', 'video/mp4': '.mp4', 'video/webm': '.webm',
}


def bucket_name():
    return (os.getenv('GCS_BUCKET') or '').strip()


def enabled():
    return bool(bucket_name())


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
    URL: signed URLs expire and must be minted per request."""
    ext = _EXT.get((mime or '').lower(), '.bin')
    path = f'{prefix}/{slug}/{uuid.uuid4().hex}{ext}'
    blob = _bucket().blob(path)
    blob.upload_from_string(data, content_type=mime or 'application/octet-stream')
    return path


def get(path):
    return _bucket().blob(path).download_as_bytes()


def delete(path):
    try:
        _bucket().blob(path).delete()
    except Exception:
        pass


def promote(path):
    """Move an object out of staging so the lifecycle rule stops watching it.
    Returns the new path; a path already outside staging is returned as-is."""
    if not path or not path.startswith(STAGING_PREFIX + '/'):
        return path
    dest = KEPT_PREFIX + path[len(STAGING_PREFIX):]
    bucket = _bucket()
    src = bucket.blob(path)
    bucket.copy_blob(src, bucket, dest)
    try:
        src.delete()
    except Exception:
        pass
    return dest


def signed_url(path, ttl=None):
    """A time-boxed read URL. Needs a service account that can sign; on Cloud
    Run that is the IAM credentials API, which the runtime SA has by default."""
    return _bucket().blob(path).generate_signed_url(
        version='v4', expiration=timedelta(seconds=ttl or signed_url_ttl()),
        method='GET')


def ensure_lifecycle():
    """Install the staging auto-delete rule. Idempotent, and safe to call at
    boot: a bucket that already carries the rule is left alone."""
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
