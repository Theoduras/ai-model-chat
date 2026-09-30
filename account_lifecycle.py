"""What happens to a creator's stuff when the subscription lapses.

A lapsed account keeps everything for GRACE_DAYS and can download all of it;
after that the content, history and saved files are deleted. Paying again
before then changes nothing. The account row, personas' configs, payments and
the token ledger are never touched: people bought those.
"""
import datetime
import io
import json
import logging
import os
import re
import tempfile
import zipfile

import db
import storage

logger = logging.getLogger(__name__)

GRACE_DAYS = 14
_GCS_COLUMNS = ('gcs_path', 'poster_gcs_path')


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def purge_enabled():
    """Off until an operator turns it on: the sweep logs what it would delete."""
    return os.getenv('LAPSE_PURGE', '0') == '1'


def owned_workspaces(session, user_id):
    return {w.id for w in session.query(db.Workspace)
            .filter(db.Workspace.owner_id == user_id).all()} | {user_id}


def owned_slugs(session, user_id):
    return [sp.slug for sp in session.query(db.SavedPersona)
            .filter(db.SavedPersona.owner_id.in_(owned_workspaces(session, user_id))).all()]


def days_left(user):
    """Days until deletion for a lapsed User row, or None when not lapsed."""
    if not user.expires_at or user.expires_at >= _now() or user.content_purged_at:
        return None
    return max(0, GRACE_DAYS - (_now() - user.expires_at).days)


def _delete_rows(session, model, condition, paths):
    q = session.query(model).filter(condition)
    for g in _GCS_COLUMNS:
        if hasattr(model, g):
            paths += [p for (p,) in q.with_entities(getattr(model, g)).all() if p]
    return q.delete(synchronize_session=False)


def purge_content(session, slugs, workspaces=()):
    """Delete a persona set's media, history and generated files. Returns the
    number of rows removed."""
    removed = 0
    paths = []
    chars = [c for (c,) in session.query(db.Character.id)
             .filter(db.Character.workspace_id.in_(list(workspaces))).all()] if workspaces else []
    if chars:
        views = [v for (v,) in session.query(db.CharacterView.id)
                 .filter(db.CharacterView.character_id.in_(chars)).all()]
        if views:
            removed += _delete_rows(session, db.ViewReference,
                                    db.ViewReference.view_id.in_(views), paths)
        for model in (db.CharacterImage, db.CharacterVersion, db.CharacterView):
            removed += _delete_rows(session, model, model.character_id.in_(chars), paths)
        removed += _delete_rows(session, db.Character, db.Character.id.in_(chars), paths)
    if workspaces:
        removed += _delete_rows(session, db.GenerationJob,
                                db.GenerationJob.workspace_id.in_(list(workspaces)), paths)
    if not slugs:
        session.commit()
        for p in paths:
            storage.delete(p)
        return removed
    convs = [c.id for c in session.query(db.Conversation.id)
             .filter(db.Conversation.persona.in_(slugs)).all()]
    if convs:
        removed += session.query(db.Message).filter(
            db.Message.conversation_id.in_(convs)).delete(synchronize_session=False)
        removed += session.query(db.Conversation).filter(
            db.Conversation.id.in_(convs)).delete(synchronize_session=False)
    # Outfit links point at media, so they go first.
    for model in (db.MediaOutfitLink, db.PersonaMedia, db.ModelReferenceSet,
                  db.VideoSource, db.AudioReference, db.PersonaImages,
                  db.PersonaNsfwImages):
        removed += _delete_rows(session, model, model.slug.in_(slugs), paths)
    removed += _delete_rows(session, db.XMessage, db.XMessage.persona.in_(slugs), paths)
    session.commit()
    for p in paths:
        storage.delete(p)
    return removed


def lapsed_owners(session):
    cutoff = _now() - datetime.timedelta(days=GRACE_DAYS)
    return (session.query(db.User)
            .filter(db.User.role == 'user', db.User.expires_at.isnot(None),
                    db.User.expires_at < cutoff, db.User.content_purged_at.is_(None))
            .all())


def sweep(session, notify=None):
    """One daily pass. `notify(user, days_left)` sends the heads-up email once
    per lapse; a paid-up account has its flag cleared so the next lapse warns
    again."""
    now = _now()
    session.query(db.User).filter(
        db.User.expires_at >= now, db.User.lapse_notified_at.isnot(None)).update(
        {'lapse_notified_at': None}, synchronize_session=False)
    if notify:
        for u in (session.query(db.User)
                  .filter(db.User.role == 'user', db.User.expires_at.isnot(None),
                          db.User.expires_at < now, db.User.content_purged_at.is_(None),
                          db.User.lapse_notified_at.is_(None)).all()):
            if u.grandfathered_until and u.grandfathered_until > now:
                continue
            if notify(u, days_left(u)):
                u.lapse_notified_at = now
    session.commit()
    done = 0
    for u in lapsed_owners(session):
        if u.grandfathered_until and u.grandfathered_until > now:
            continue
        slugs = owned_slugs(session, u.id)
        if not purge_enabled():
            logger.info('LAPSE PURGE (dry run) user=%s personas=%s', u.email, slugs)
            continue
        n = purge_content(session, slugs, owned_workspaces(session, u.id))
        u.content_purged_at = now
        session.commit()
        logger.info('LAPSE PURGE user=%s personas=%d rows=%d', u.email, len(slugs), n)
        done += 1
    return done


def _decode_data_url(url):
    m = re.match(r'^data:([^;,]+)?(;base64)?,(.*)$', url or '', re.S)
    if not m:
        return None, b''
    import base64
    from urllib.parse import unquote_to_bytes
    data = base64.b64decode(m.group(3)) if m.group(2) else unquote_to_bytes(m.group(3))
    return m.group(1) or '', data


def _ext(mime, path=''):
    ext = os.path.splitext(path)[1]
    return ext or ('.' + (mime or 'bin').split('/')[-1].split('+')[0])


def build_export(session, user_id):
    """Everything the account owns, as a zip in a temp file (not memory: the
    vault can be large). Returns the open file, positioned at the start."""
    out = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
    slugs = owned_slugs(session, user_id)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for sp in session.query(db.SavedPersona).filter(db.SavedPersona.slug.in_(slugs)):
            z.writestr(f'{sp.slug}/config.json', sp.config_json or '{}')
            z.writestr(f'{sp.slug}/prompt.txt', sp.prompt or '')
        for slug in slugs:
            chats = []
            for c in (session.query(db.Conversation)
                      .filter(db.Conversation.persona == slug).all()):
                chats.append({'id': c.id, 'started': str(c.created_at), 'messages': [
                    {'role': m.role, 'text': m.content, 'at': str(m.created_at)}
                    for m in c.messages]})
            z.writestr(f'{slug}/conversations.json', json.dumps(chats, indent=1))
            for m in session.query(db.PersonaMedia).filter(db.PersonaMedia.slug == slug):
                name = f'{slug}/media/{m.id}'
                data, mime = b'', m.mime
                if m.gcs_path:
                    try:
                        data = storage.get(m.gcs_path)
                    except Exception:
                        logger.warning('EXPORT could not read %s', m.gcs_path)
                if not data and m.image_data:
                    mime, data = _decode_data_url(m.image_data)
                if data:
                    z.writestr(name + _ext(mime or m.mime, m.gcs_path or ''), data)
                z.writestr(name + '.json', json.dumps({
                    'kind': m.kind, 'location': m.location, 'outfit': m.outfit,
                    'lighting': m.lighting, 'purpose': m.purpose}))
    out.seek(0)
    return out
