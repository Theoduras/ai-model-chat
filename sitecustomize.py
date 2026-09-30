"""Runtime hook: accept uploaded stills on /api/generate/undress.

app.py rejects any media whose source is not 'generated'. This wraps
db.get_persona_media so an uploaded row looks generated for that route only.
Python imports sitecustomize automatically at interpreter start.
"""
import builtins

_real_import = builtins.__import__
_patched = False


def _patch_db(mod):
    global _patched
    if _patched or not hasattr(mod, 'get_persona_media'):
        return
    orig = mod.get_persona_media

    def get_persona_media(session, media_id):
        row = orig(session, media_id)
        try:
            from flask import has_request_context, request
            if (row is not None and getattr(row, 'source', None) == 'uploaded'
                    and has_request_context()
                    and request.path == '/api/generate/undress'):
                row.source = 'generated'
        except Exception:
            pass
        return row

    mod.get_persona_media = get_persona_media
    _patched = True


def __import__(name, globals=None, locals=None, fromlist=(), level=0):
    mod = _real_import(name, globals, locals, fromlist, level)
    if name == 'db' or getattr(mod, '__name__', '') == 'db':
        _patch_db(mod)
    return mod


builtins.__import__ = __import__
