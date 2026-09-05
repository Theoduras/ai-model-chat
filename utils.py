"""Shared helpers for app.py, which is long enough that new cross-cutting
concerns belong out here (see CLAUDE.md).

Imports from app are done inside the functions: app imports this module at
load time, so a module-level import would be circular.
"""
from functools import wraps

from flask import request, jsonify


def _is_operator():
    """True for the operator console. The admin password and the DB admin role
    are two disjoint mechanisms in this app; an operator is either.

    Deliberately *not* _check_admin(): that returns True for everyone when
    ADMIN_PASSWORD is unset, which is exactly the configuration where creators
    can reach the dashboard — so reusing it would make every scope check below
    vacuous on the deployment that needs them most.
    """
    import app
    if (app._current_user() or {}).get('is_admin'):
        return True
    return bool(app._admin_password()) and app.session.get('admin_authed') is True


def operator_only(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not _is_operator():
            return jsonify({'error': 'Unauthorized'}), 401
        return fn(*a, **kw)
    return wrapper


def request_persona():
    """The persona slug this request is about, from the body or the query."""
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return (request.args.get('persona') or '').strip()
    return ((request.get_json(silent=True) or {}).get('persona') or '').strip()


def platform_scoped(fn):
    """Operators pass. Everyone else may only act on a persona they own.

    Denials are 404 rather than 403, matching _guard_persona_writes, so the
    existence of another creator's slug is not confirmable.
    """
    @wraps(fn)
    def wrapper(*a, **kw):
        import app
        if _is_operator():
            return fn(*a, **kw)
        slug = request_persona()
        if not slug:
            return jsonify({'ok': False, 'error': 'persona required'}), 400
        if not app._can_edit_persona(slug, app._current_user()):
            app.logger.warning('PLATFORM DENIED slug=%s path=%s', slug, request.path)
            return jsonify({'error': 'Not found'}), 404
        return fn(*a, **kw)
    return wrapper


def owned_slugs():
    """Slugs the caller may see. None means "everything" (operator)."""
    import app
    if _is_operator():
        return None
    user = app._current_user()
    if not user:
        return set()
    return {p['slug'] for p in
            app.db_list_personas(owner_id=user.get('workspace_id') or user['id'])}
