"""One-off copy of everything off Google Cloud: every table into another
Postgres (Neon, for Vercel) and every bucket object into Vercel Blob.

It runs from the old Cloud Run service, because that is the only place holding
both the Cloud SQL socket and the bucket credentials. Both halves are
idempotent — rows go in with ON CONFLICT DO NOTHING and objects already in Blob
are skipped — so an interrupted run is finished by pressing Start again.
Objects keep their paths: the database stores paths, never URLs, so every
reference stays valid on the new store.
"""
import datetime
import threading
import traceback
import urllib.parse

import requests
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

import db
import storage

BATCH = 100
KEEP_LOG_DAYS = 30
# History only: nothing reads rows this old, so trimming them is what brings
# the database under Neon's free 512 MB.
LOG_TABLES = {'activity_log': 'created_at', 'visits': 'created_at',
              'demo_events': 'created_at', 'link_clicks': 'created_at',
              'referral_clicks': 'created_at', 'x_events': 'created_at',
              'fan_events': 'ts'}

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


def _keep(table, trim):
    col = LOG_TABLES.get(table.name) if trim else None
    if not col:
        return None
    return table.c[col] >= db._now() - datetime.timedelta(days=KEEP_LOG_DAYS)


def copy_tables(dst, trim=True):
    db.Base.metadata.create_all(dst)
    for table in db.Base.metadata.sorted_tables:
        state['step'] = 'table ' + table.name
        keep = _keep(table, trim)
        with db.engine.connect() as s:
            q = select(func.count()).select_from(table)
            total = s.execute(q if keep is None else q.where(keep)).scalar()
        info = state['tables'][table.name] = {'total': total, 'copied': 0}
        with dst.connect() as d:
            if d.execute(select(func.count()).select_from(table)).scalar() >= total:
                info['copied'] = total
                continue
        order = list(table.primary_key.columns) or list(table.columns)[:1]
        offset = 0
        while offset < total:
            with db.engine.connect() as s:
                q = select(table) if keep is None else select(table).where(keep)
                rows = [dict(r._mapping) for r in s.execute(
                    q.order_by(*order).offset(offset).limit(BATCH))]
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
    objs = [b for b in storage._bucket().list_blobs()
            if not b.name.endswith('/')
            and not b.name.startswith(storage.STAGING_PREFIX + '/')]
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


NEON_FREE = 512 * 1024 ** 2
BLOB_FREE = 1024 ** 3


def sizes():
    """How much there is to copy, against the free tiers, so the choice of
    plan is made before the copy rather than when it stops halfway."""
    out = {'database': {}, 'media': {}}
    tables, after = {}, 0
    pg = db.engine.dialect.name == 'postgresql'
    with db.engine.connect() as s:
        for table in db.Base.metadata.sorted_tables:
            tables[table.name] = s.execute(select(func.count()).select_from(table)).scalar()
        dbytes = s.execute(text('SELECT pg_database_size(current_database())')).scalar() if pg else 0
        largest = []
        if pg:
            largest = [{'table': r[0], 'bytes': r[1]} for r in s.execute(text(
                "SELECT relname, pg_total_relation_size(relid) FROM pg_catalog.pg_statio_user_tables "
                "ORDER BY 2 DESC LIMIT 10"))]
            # Live rows only, as stored (compressed): what a copy actually writes,
            # plus a third for indexes.
            for table in db.Base.metadata.sorted_tables:
                col = LOG_TABLES.get(table.name)
                where = (f" WHERE {col} >= now() - interval '{KEEP_LOG_DAYS} days'"
                         if col else '')
                after += int(s.execute(text(
                    f'SELECT COALESCE(SUM(pg_column_size(t.*)), 0) FROM {table.name} t{where}'
                )).scalar() * 1.33)
    out['database'] = {'bytes': dbytes, 'after_copy_bytes': after,
                       'fits_free': (after or dbytes) <= NEON_FREE,
                       'largest': largest, 'rows': tables}
    if storage.backend() == 'gcs':
        kept = staging = n = 0
        for b in storage._bucket().list_blobs():
            if b.name.startswith(storage.STAGING_PREFIX + '/'):
                staging += b.size or 0
            else:
                kept += b.size or 0
                n += 1
        out['media'] = {'files': n, 'bytes': kept, 'staging_bytes_skipped': staging,
                        'fits_free': kept <= BLOB_FREE}
    else:
        out['media'] = {'note': 'No bucket configured here'}
    return out


def _run(database_url, blob_token, trim, compress):
    try:
        if compress:
            state['step'] = 'compressing images'
            compress()
        if database_url:
            copy_tables(_target_engine(database_url), trim)
        if blob_token:
            copy_media(blob_token)
        state['done'] = True
        state['step'] = 'finished'
    except Exception as e:
        state['error'] = str(e)
        _log(traceback.format_exc()[-1500:])
    finally:
        state['running'] = False


def start(database_url, blob_token, trim=True, compress=None):
    with _lock:
        if state['running']:
            return False
        state.update(running=True, step='starting', tables={}, media={},
                     error='', done=False)
    threading.Thread(target=_run, args=(database_url, blob_token, trim, compress),
                     daemon=True).start()
    return True
