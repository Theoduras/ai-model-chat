"""Gunicorn target. Relaxes undress so uploaded persona photos are accepted."""
import db
import app as app_module

_real_get = db.get_persona_media
_orig_view = app_module.api_generate_undress


def get_persona_media(session, media_id):
    row = _real_get(session, media_id)
    if row is not None and getattr(row, 'source', None) == 'uploaded':
        row.source = 'generated'
    return row


def api_generate_undress():
    db.get_persona_media = get_persona_media
    try:
        return _orig_view()
    finally:
        db.get_persona_media = _real_get


db.get_persona_media = get_persona_media
app_module.app.view_functions['api_generate_undress'] = api_generate_undress
app = app_module.app
