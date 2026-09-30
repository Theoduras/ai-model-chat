"""Gunicorn target. Undress accepts generated or uploaded persona photos."""
import re
import db
import credits as CR
import imagegen
import app as A
from flask import request, jsonify


def api_generate_undress():
    blocked = A._require_active()
    if blocked:
        return blocked
    user = A._current_user()
    body = request.get_json(silent=True) or {}
    slug = (body.get('persona') or '').strip().lower()
    if not re.match(r'^[a-z0-9_-]+$', slug or ''):
        return jsonify({'ok': False, 'error': 'Invalid persona'}), 400
    mine = A.owned_slugs()
    if mine is not None and slug not in mine:
        return jsonify({'ok': False, 'error': 'Not your persona'}), 403
    media_id = str(body.get('media') or '').strip()
    s = A._db_session()
    try:
        row = db.get_persona_media(s, media_id) if media_id else None
        ok = (row is not None and row.slug == slug and (row.kind or 'image') == 'image'
              and row.source in ('generated', 'uploaded') and bool(row.gcs_path))
        size = A._undress_aspect(row) if ok else ''
    finally:
        s.close()
    if not ok:
        return jsonify({'ok': False, 'error': 'Need a saved photo for this persona.'}), 400
    spec = {'kind': 'image', 'slug': slug, 'job': '', 'undress': True,
            'model': imagegen.EXPLICIT_MODEL, 'resolution': '2k', 'aspect': size,
            'batch': 1, 'shot': 'nude', 'explicit': True,
            'reference_media': media_id, 'addons': []}
    return A._gen_submit(user, slug, spec, CR.quote(spec))


A.app.view_functions['api_generate_undress'] = api_generate_undress
A.api_generate_undress = api_generate_undress
app = A.app
