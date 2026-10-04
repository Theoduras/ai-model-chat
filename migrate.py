"""One-off copy of everything off Google Cloud: every table into another
Postgres (Neon, for Vercel) and every bucket object into Vercel Blob.

It runs from the old Cloud Run service, because that is the only place holding
both the Cloud SQL socket and the bucket credentials. Both halves are
idempotent — rows go in with ON CONFLICT DO NOTHING and objects already in Blob
are skipped — so an interrupted run is finished by pressing Start again.
Objects keep their paths: the database stores paths, never URLs, so every
reference stays valid on the new store.
"""
import threading
import traceback
import urllib.parse

import requests
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

import db
import storage

BATCH = 100

state = {'running': False, 'step': '', 'tables': {}, 'media': {}, 'log': [],
         'error': '', 'done': False}
_lock = threading.Lock()


def _log(msg):
    state['log'] = (state['log'] + [msg])[-200:]


def _target_engine(url):
    url = url.strip()
    for old in ('postgres://', 'postgresql://'):
        if url.startswith(old):
            url = 'postgresql+psycopg2://' + url[len(old):]
    if not url.startswith('postgresql+psycopg2://'):
        raise ValueError('The target must be a Postgres URL')
    return create_engine(url, pool_pre_ping=True)


def copy_tables(dst):
    db.Base.metadata.create_all(dst)
    for table in db.Base.metadata.sorted_tables:
        state['step'] = 'table ' + table.name
        with db.engine.connect() as s:
            total = s.execute(select(func.count()).select_from(table)).scalar()
        info = state['tables'][table.name] = {'total': total, 'copied': 0}
        with dst.connect() as d:
            if d.execute(select(func.count()).select_from(table)).scalar() >= total:
                info['copied'] = total
                continue
        order = list(table.primary_key.columns) or list(table.columns)[:1]
        offset = 0
        while offset < total:
            with db.engine.connect() as s:
                rows = [dict(r._mapping) for r in s.execute(
                    select(table).order_by(*order).offset(offset).limit(BATCH))]
            if not rows:
                break
            with dst.begin() as d:
                d.execute(pg_insert(table).values(rows).on_conflict_do_nothing())
            offset += len(rows)
            info['copied'] = offset
        _log(f'{table.name}: {total} rows')

def _blob_existing(token):
    have, cursor = set(), None
    while True:
        q = '?limit=1000' + ('&cursor=' + urllib.parse.quote(cursor) if cursor else '')
        r = requests.get(storage.BLOB_API + q, timeout=60, headers={
            'authorization': 'Bearer ' + token,
            'x-api-version': storage.BLOB_API_VERSION})
        r.raise_for_status()
        body = r.json()
        have.update(b['pathname'] for b in body.get('blobs') or [])
        cursor = body.get('cursor')
        if not body.get('hasMore') or not cursor:
            return have


def copy_media(token):
    if storage.backend() != 'gcs':
        _log('No GCS bucket configured here, so no media to copy')
        return
    state['step'] = 'media'
    have = _blob_existing(token)
    objs = [b for b in storage._bucket().list_blobs() if not b.name.endswith('/')]
    info = state['media'] = {'total': len(objs), 'copied': 0, 'skipped': 0,
                             'failed': 0, 'bytes': 0}
    for b in objs:
        if b.name in have:
            info['skipped'] += 1
            continue
        try:
            data = b.download_as_bytes()
            r = requests.put(
                storage.BLOB_API + '?pathname=' + urllib.parse.quote(b.name),
                data=data, timeout=600, headers={
                    'authorization': 'Bearer ' + token,
                    'x-api-version': storage.BLOB_API_VERSION,
                    'x-content-type': b.content_type or storage.mime_of(b.name),
                    'x-add-random-suffix': '0', 'x-allow-overwrite': '1',
                    'x-vercel-blob-access': 'private'})
            r.raise_for_status()
            info['copied'] += 1
            info['bytes'] += len(data)
        except Exception as e:
            info['failed'] += 1
            _log(f'media {b.name}: {e}')


def _run(database_url, blob_token):
    try:
        if database_url:
            copy_tables(_target_engine(database_url))
        if blob_token:
            copy_media(blob_token)
        state['done'] = True
        state['step'] = 'finished'
    except Exception as e:
        state['error'] = str(e)
        _log(traceback.format_exc()[-1500:])
    finally:
        state['running'] = False


def start(database_url, blob_token):
    with _lock:
        if state['running']:
            return False
        state.update(running=True, step='starting', tables={}, media={},
                     error='', done=False)
    threading.Thread(target=_run, args=(database_url, blob_token),
                     daemon=True).start()
    return True
