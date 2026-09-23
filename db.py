"""Database layer for conversation persistence.

Uses SQLite by default (local dev) and Postgres in production when DATABASE_URL
is set (e.g. Cloud SQL on Cloud Run). Same code path for both via SQLAlchemy.
"""
import os
import datetime
import uuid

from sqlalchemy import (
    create_engine, Boolean, Column, String, Text, DateTime, Float, ForeignKey, Index,
    Integer, case, func, or_
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# DATABASE_URL example (Postgres): postgresql+psycopg2://user:pass@host/dbname
# Default: a local SQLite file, so the app runs with zero setup.
def _build_database_url():
    import urllib.parse as _up
    explicit = (os.getenv('DATABASE_URL') or '').strip()
    if explicit:
        url = explicit
    else:
        # Cloud SQL (Postgres) via the Unix socket Cloud Run mounts at
        # /cloudsql/<INSTANCE_CONNECTION_NAME> — configured with simple env vars.
        # Strip whitespace: pasted values often carry a stray tab/newline.
        inst = (os.getenv('CLOUD_SQL_CONNECTION_NAME') or os.getenv('INSTANCE_CONNECTION_NAME') or '').strip()
        user = (os.getenv('DB_USER') or '').strip()
        if inst and user:
            pw = _up.quote_plus((os.getenv('DB_PASS') or '').strip())
            name = (os.getenv('DB_NAME') or 'postgres').strip()
            return (f'postgresql+psycopg2://{_up.quote_plus(user)}:{pw}@/'
                    f'{name}?host=/cloudsql/{inst}')
        # Fallback: local SQLite. On Cloud Run the app dir is read-only, so use a
        # writable location (/tmp or DATA_DIR) — ephemeral, not for production.
        data_dir = (os.getenv('DATA_DIR') or '').strip()
        if not data_dir:
            app_dir = os.path.dirname(os.path.abspath(__file__))
            data_dir = app_dir if os.access(app_dir, os.W_OK) else '/tmp'
        return 'sqlite:///' + os.path.join(data_dir, 'data.db')
    # Normalize managed-Postgres URL forms to the SQLAlchemy+psycopg2 driver.
    if url.startswith('postgres://'):
        url = 'postgresql+psycopg2://' + url[len('postgres://'):]
    elif url.startswith('postgresql://'):
        url = 'postgresql+psycopg2://' + url[len('postgresql://'):]
    return url


DATABASE_URL = _build_database_url()

_connect_args = {'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _epoch(dt):
    """Seconds since the epoch for a stored datetime. SQLite gives these back
    naive and Postgres gives them back aware, so both have to be handled or one
    host reads every timestamp as 1970."""
    if not dt:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def _uid():
    return uuid.uuid4().hex


class Conversation(Base):
    __tablename__ = 'conversations'

    id = Column(String(32), primary_key=True, default=_uid)
    client_id = Column(String(128), index=True)   # caller's end-user identifier
    persona = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    messages = relationship(
        'Message', back_populates='conversation',
        order_by='Message.created_at', cascade='all, delete-orphan')


class Message(Base):
    __tablename__ = 'messages'

    id = Column(String(32), primary_key=True, default=_uid)
    conversation_id = Column(String(32), ForeignKey('conversations.id'), nullable=False)
    role = Column(String(16), nullable=False)     # 'user' or 'model'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_now)

    conversation = relationship('Conversation', back_populates='messages')


Index('ix_messages_conv_created', Message.conversation_id, Message.created_at)


class SavedPersona(Base):
    """Creator-made persona copies. Premade personas stay as repo files;
    these live in the DB so they survive refreshes and redeploys."""
    __tablename__ = 'saved_personas'

    slug = Column(String(64), primary_key=True)
    name = Column(String(120))
    config_json = Column(Text, nullable=False)   # JSON-encoded builder config
    prompt = Column(Text, nullable=False)
    # Which customer owns this persona. NULL means it predates per-user
    # ownership (or is a house persona) and is visible to admins only.
    owner_id = Column(String(32), index=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class PersonaImages(Base):
    """Up to 5 images per persona, stored as a JSON list of data URLs. Works as
    an overlay for any persona (premade originals included) so photos persist in
    the DB without editing the read-only original config files."""
    __tablename__ = 'persona_images'

    slug = Column(String(64), primary_key=True)
    images_json = Column(Text, nullable=False, default='[]')
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class PersonaNsfwImages(Base):
    """Up to 5 NSFW images per persona, same shape as PersonaImages. Kept as a
    wholly separate pool rather than a flag on PersonaMedia: these photos must
    never reach a normal send, and a dedicated table makes that structural
    rather than something a query can get wrong."""
    __tablename__ = 'persona_nsfw_images'

    slug = Column(String(64), primary_key=True)
    images_json = Column(Text, nullable=False, default='[]')
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class PersonaMedia(Base):
    """Tagged media items for a persona. Each image is tagged with location,
    outfit, lighting, and purpose so the chat engine can pick the right photo
    to send based on conversation context."""
    __tablename__ = 'persona_media'

    id = Column(String(32), primary_key=True, default=_uid)
    slug = Column(String(64), nullable=False, index=True)
    # Inline bytes as a data URL. Images always live here; a short clip may too.
    # Anything longer is referenced by source_url instead, because a video in a
    # text column is a row nobody can afford to read.
    image_data = Column(Text, nullable=False, default='')
    kind = Column(String(8), default='image')      # image | video
    mime = Column(String(60), default='')
    source_url = Column(String(600), default='')   # externally hosted, if any
    # A video's poster frame, grabbed in the browser when the file was
    # uploaded. Instagram refuses a Reel with no cover, and the queue worker
    # has no browser to pull a frame out of a video itself.
    poster_data = Column(Text, default='')
    location = Column(String(120), default='')
    outfit = Column(String(120), default='')
    lighting = Column(String(60), default='')
    purpose = Column(String(60), default='')
    position = Column(Integer, default=0)   # manual sort order within an outfit
    created_at = Column(DateTime, default=_now)
    # Generated media lives in GCS rather than image_data: a clip does not fit
    # in a text column. Uploads keep using image_data, so both are read through
    # _media_bytes and neither knows about the other.
    gcs_path = Column(String(400), default='')
    poster_gcs_path = Column(String(400), default='')
    # Set while a generation sits in the staging prefix. The bucket's lifecycle
    # rule is what actually deletes it; this is for showing a countdown and for
    # filtering, never for enforcement.
    expires_at = Column(DateTime)
    # False until the creator keeps it. An unreviewed generation must never be
    # picked for a fan, so every send path filters on this.
    approved = Column(Boolean, default=True)
    # Gallery fields. `approved_for_training` starts true because an upload is
    # approved on arrival; a generation clears it alongside `approved`, so
    # nothing unreviewed can become training material either.
    is_favourite = Column(Boolean, default=False)
    tags = Column(String(300), default='')
    approved_for_training = Column(Boolean, default=True)
    # Safe-work or explicit. '' is an item nobody has judged yet; it is treated
    # as safe when a channel asks, because the cost of guessing wrong the other
    # way is a banned account.
    rating = Column(String(8), default='')   # '' | sfw | nsfw
    # Two separate exclusions, because the two surfaces are separate decisions:
    # a photo fine for a paying fan in a DM is often exactly the one that must
    # never go on a public feed. Both stop the *automatic* picks only -- the
    # creator can still attach either by hand.
    no_posts = Column(Boolean, default=False)
    no_dms = Column(Boolean, default=False)
    source = Column(String(12), default='upload')   # upload | generated
    # The clip an extension continues. There is no ffmpeg in the image, so an
    # extension cannot be joined onto its source: it is delivered as its own
    # vault clip and this is what says which clip it carries on from.
    parent_media = Column(String(32), default='')
    # The other version of a filtered generation (phone look on or off), so
    # the creator can switch it back without generating again.
    original_path = Column(String(400), default='')


Index('ix_media_slug_purpose', PersonaMedia.slug, PersonaMedia.purpose)


class MediaOutfitLink(Base):
    """Places a vault photo in an outfit. A photo can be in several outfits, and
    removing it from one leaves the photo itself untouched."""
    __tablename__ = 'media_outfit_links'

    id = Column(String(32), primary_key=True, default=_uid)
    slug = Column(String(64), nullable=False, index=True)
    media_id = Column(String(32), ForeignKey('persona_media.id'), nullable=False, index=True)
    outfit = Column(String(120), nullable=False)
    position = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now)


Index('ix_link_slug_outfit', MediaOutfitLink.slug, MediaOutfitLink.outfit,
      MediaOutfitLink.position)


class ModelReferenceSet(Base):
    """Which gallery photos drive a persona on one generation model.

    References are model-scoped, not global: a slot filled on Seedream is not a
    slot on Wan, and every model caps how many it will accept. A global list
    could not express either, which is why this is its own table rather than a
    column."""
    __tablename__ = 'model_reference_sets'

    id = Column(String(32), primary_key=True, default=_uid)
    slug = Column(String(64), nullable=False, index=True)
    model_key = Column(String(64), nullable=False, index=True)
    media_id = Column(String(32), ForeignKey('persona_media.id'),
                      nullable=False, index=True)
    # Face and body are separate groups: a face reference is there to hold her
    # identity, a body reference to hold her shape and styling. They are filled
    # from different photos and a generation weights them differently, so one
    # flat list could not say which a photo was chosen to be.
    role = Column(String(8), default='face')        # face | body
    slot_index = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now)


Index('ix_modelref_slug_model', ModelReferenceSet.slug,
      ModelReferenceSet.model_key, ModelReferenceSet.role,
      ModelReferenceSet.slot_index)


class VideoSource(Base):
    """A clip a creator uploaded to be swapped into.

    The row exists for one reason: the price of a swap is per second, so the
    duration has to be a number the server measured, not one the browser sent
    with the submit. It is read out of the file's own header at upload and
    pinned here; the submit only names the row.
    """
    __tablename__ = 'video_sources'

    id = Column(String(32), primary_key=True, default=_uid)
    slug = Column(String(64), nullable=False, index=True)
    gcs_path = Column(String(400), nullable=False)
    poster_gcs_path = Column(String(400), default='')
    mime = Column(String(60), default='video/mp4')
    seconds = Column(Integer, default=0)
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    size_bytes = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now)


class AudioReference(Base):
    """Voice or ambience a video model can be conditioned on.

    Deliberately not part of the media vault: audio is never training material
    and never reaches a fan, so it has no approval or staging lifecycle."""
    __tablename__ = 'audio_references'

    id = Column(String(32), primary_key=True, default=_uid)
    slug = Column(String(64), nullable=False, index=True)
    gcs_path = Column(String(400), default='')
    fmt = Column(String(8), default='mp3')          # mp3 | wav
    size_bytes = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now)


class Character(Base):
    """The fixed look of one model: a checked face and a set of body views that
    every generation for her linked persona is conditioned on."""
    __tablename__ = 'characters'

    id = Column(String(32), primary_key=True, default=_uid)
    workspace_id = Column(String(32), nullable=False, index=True)
    owner_id = Column(String(32), index=True)
    # Storage path segment and the job slug. A character may exist before it
    # has a persona, and a generation job cannot be stored without a slug.
    key = Column(String(64), nullable=False, unique=True)
    # The linked persona. Unique: one character per persona, one persona per
    # character.
    slug = Column(String(64), unique=True)
    name = Column(String(120), default='')
    age = Column(Integer, default=24)
    body_type = Column(String(16), default='female')
    nsfw_level = Column(String(12), default='sfw')
    sheet_json = Column(Text, default='{}')
    notes = Column(String(400), default='')
    status = Column(String(12), default='draft')      # draft | complete
    version = Column(Integer, default=0)
    attested_at = Column(DateTime)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class CharacterImage(Base):
    """A reference or the approved image for one view of a character.

    Deliberately not PersonaMedia: these are identity material, anatomical
    close-ups included, and keeping them out of the vault makes it structural
    that no send, post or pick can ever reach one."""
    __tablename__ = 'character_images'

    id = Column(String(32), primary_key=True, default=_uid)
    character_id = Column(String(32), nullable=False, index=True)
    view = Column(String(32), default='')             # '' = general reference
    role = Column(String(12), default='reference')    # reference | candidate | canonical | wardrobe | outfit
    source = Column(String(12), default='upload')     # upload | generated
    rating = Column(String(12), default='sfw')        # the view's, never the client's
    gcs_path = Column(String(400), default='')
    mime = Column(String(60), default='image/jpeg')
    checks_json = Column(Text, default='')
    job_id = Column(String(32), default='')
    outfit = Column(String(80), default='')             # the outfit a full-body photo was made in
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=_now)


Index('ix_charimg_char_view', CharacterImage.character_id, CharacterImage.view,
      CharacterImage.role)


class CharacterVersion(Base):
    """Each approval freezes the approved set, so a generation can record which
    look it was made from and an edit never silently rewrites that history."""
    __tablename__ = 'character_versions'

    id = Column(String(32), primary_key=True, default=_uid)
    character_id = Column(String(32), nullable=False, index=True)
    number = Column(Integer, nullable=False)
    snapshot_json = Column(Text, default='{}')
    created_at = Column(DateTime, default=_now)


class CharacterView(Base):
    """Where one view of a character stands in the view tree. Locked and
    outdated are derived from the parents by characters.resolve_status, never
    stored, so re-approving a parent needs no write to its children."""
    __tablename__ = 'character_views'

    id = Column(String(32), primary_key=True, default=_uid)
    character_id = Column(String(32), nullable=False, index=True)
    view_key = Column(String(32), nullable=False)
    status = Column(String(12), default='not_started')  # not_started | generating | review | approved
    mode = Column(String(12))                           # crop | reference; None = registry default
    crop_box_json = Column(Text)                        # {x, y, w, h}, normalised 0-1
    strength = Column(Float)                            # None = characters.STRENGTH[mode]
    result_image_id = Column(String(32))
    job_id = Column(String(32))                         # the open generation, while generating
    pending_jobs = Column(Text, default='')             # every open generation: one per outfit
    version = Column(Integer, default=0)
    parent_versions_json = Column(Text, default='{}')   # {parent_key: version} at generation
    updated_at = Column(DateTime, default=_now, onupdate=_now)


Index('ix_charview_char_key', CharacterView.character_id, CharacterView.view_key,
      unique=True)


class ViewReference(Base):
    """A reference image a view is generated from. The approved parents are the
    default rows; any other approved view can be added."""
    __tablename__ = 'view_references'

    id = Column(String(32), primary_key=True, default=_uid)
    view_id = Column(String(32), nullable=False, index=True)
    ref_image_id = Column(String(32), nullable=False)
    weight = Column(Float, default=1.0)


Index('ix_link_media_outfit', MediaOutfitLink.media_id, MediaOutfitLink.outfit,
      unique=True)


class Visit(Base):
    """One row per page view on the site — used for the visitor log
    (who's on the dev page: IP, geolocation, time, path)."""
    __tablename__ = 'visits'

    id = Column(String(32), primary_key=True, default=_uid)
    created_at = Column(DateTime, default=_now, index=True)
    ip = Column(String(64), index=True)
    path = Column(String(255))
    user_agent = Column(String(400))
    referrer = Column(String(400))
    country = Column(String(80))
    country_code = Column(String(8))
    region = Column(String(120))
    city = Column(String(120))


Index('ix_visits_created', Visit.created_at)


class XEvent(Base):
    """One row per X (Twitter) action taken through the site — who connected or
    operated which persona's X account, from which IP/geo, and when."""
    __tablename__ = 'x_events'

    id = Column(String(32), primary_key=True, default=_uid)
    created_at = Column(DateTime, default=_now, index=True)
    action = Column(String(40))       # connect_start, connect_complete, follow, comment, ...
    persona = Column(String(64))
    detail = Column(String(300))      # target handle / post / hint
    x_username = Column(String(120))  # the connected X account, when known
    ip = Column(String(64), index=True)
    user_agent = Column(String(400))
    country = Column(String(80))
    country_code = Column(String(8))
    region = Column(String(120))
    city = Column(String(120))


Index('ix_xevents_created', XEvent.created_at)


class XMessage(Base):
    """One row per DM exchanged on a connected X account — both incoming (from a
    fan) and outgoing (the persona's reply/opener), so full conversations can be
    reconstructed and viewed. Pruned after a retention window."""
    __tablename__ = 'x_messages'

    id = Column(String(32), primary_key=True, default=_uid)
    created_at = Column(DateTime, default=_now, index=True)
    persona = Column(String(64), index=True)
    x_user_id = Column(String(64), index=True)   # the fan's numeric X id
    x_username = Column(String(120))
    direction = Column(String(8))                # 'in' (from fan) or 'out' (from persona)
    text = Column(Text)


Index('ix_xmsg_thread', XMessage.persona, XMessage.x_user_id, XMessage.created_at)


class PpvDrop(Base):
    """One paid unlock sent to one fan: what went out, and whether it was ever
    bought. The settings blobs track what was *sent*; this is the only record of
    what was *paid for*, so it is never pruned and never cleared by a progress
    reset. read_at/paid_at only ever go from null to a timestamp, except when a
    refund reverses one."""
    __tablename__ = 'ppv_drops'

    id = Column(String(32), primary_key=True, default=_uid)
    created_at = Column(DateTime, default=_now, index=True)
    persona = Column(String(64), index=True)
    fan_uuid = Column(String(64), index=True)
    set_id = Column(String(64))
    set_name = Column(String(64))
    tier_index = Column(Integer)
    price_cents = Column(Integer)
    media_uuids = Column(Text)                   # JSON array
    message_uuid = Column(String(64), index=True)  # returned by the send call
    read_at = Column(DateTime)
    paid_at = Column(DateTime)
    invoice_id = Column(String(64), index=True)  # Fanvue's FV-nnnnn join key
    paid_source = Column(String(16))             # webhook | poll | earnings | manual


Index('ix_ppv_drops_fan', PpvDrop.persona, PpvDrop.fan_uuid, PpvDrop.created_at)


class AppSetting(Base):
    """Small persistent key/value store (e.g. the X app Client ID + redirect URI
    so connecting/refreshing doesn't need them re-entered each time)."""
    __tablename__ = 'app_settings'

    key = Column(String(64), primary_key=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


GRANDFATHER_FLAG = 'entitlements_grandfather_backfill'
OF_GRANDFATHER_FLAG = 'onlyfans_starter_grandfather_backfill'


def get_app_setting(session, key, default=None):
    row = session.get(AppSetting, key)
    return row.value if row else default


def set_app_setting(session, key, value):
    row = session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key)
        session.add(row)
    row.value = value
    return row


def delete_app_setting(session, key):
    """Drop one setting row. True when there was one to drop."""
    row = session.get(AppSetting, key)
    if row is None:
        return False
    session.delete(row)
    return True


class XOpener(Base):
    """Permanent record of every fan a persona has sent an opening DM to. Never
    pruned, so an opener is never sent to the same person twice — even after the
    conversation log ages out."""
    __tablename__ = 'x_openers'

    id = Column(String(32), primary_key=True, default=_uid)
    created_at = Column(DateTime, default=_now)
    persona = Column(String(64), index=True)
    x_user_id = Column(String(64), index=True)


Index('ix_xopener_persona_user', XOpener.persona, XOpener.x_user_id)


def list_persona_media(session, slug):
    # coalesce: rows created before `position` existed have NULL, and NULLs sort
    # differently on SQLite and Postgres.
    return (session.query(PersonaMedia).filter_by(slug=slug)
            .order_by(func.coalesce(PersonaMedia.position, 0),
                      PersonaMedia.created_at).all())


def list_media_links(session, slug):
    return (session.query(MediaOutfitLink).filter_by(slug=slug)
            .order_by(func.coalesce(MediaOutfitLink.position, 0),
                      MediaOutfitLink.created_at).all())


def link_media_to_outfit(session, slug, media_id, outfit):
    """Idempotent: linking the same photo to the same outfit twice is a no-op."""
    outfit = str(outfit)
    existing = (session.query(MediaOutfitLink)
                .filter_by(media_id=media_id, outfit=outfit).first())
    if existing:
        return existing
    last = (session.query(func.max(MediaOutfitLink.position))
            .filter_by(slug=slug, outfit=outfit).scalar())
    link = MediaOutfitLink(slug=slug, media_id=media_id, outfit=outfit,
                           position=(last or 0) + 1)
    session.add(link)
    return link


def unlink_media_from_outfit(session, media_id, outfit):
    rows = session.query(MediaOutfitLink).filter_by(
        media_id=media_id, outfit=str(outfit)).all()
    for r in rows:
        session.delete(r)
    return len(rows)


def delete_media_links(session, media_id):
    for r in session.query(MediaOutfitLink).filter_by(media_id=media_id).all():
        session.delete(r)


def reorder_media_links(session, slug, entries):
    """entries: [{'media_id':..., 'outfit':...}, ...] in display order. Links
    not mentioned for an outfit are removed, so a drag out of an outfit sticks."""
    existing = {(l.media_id, l.outfit): l
                for l in session.query(MediaOutfitLink).filter_by(slug=slug).all()}
    seen = set()
    for i, e in enumerate(entries):
        mid, outfit = e.get('media_id'), str(e.get('outfit', ''))
        if not mid or not outfit:
            continue
        key = (mid, outfit)
        seen.add(key)
        link = existing.get(key)
        if link is None:
            link = MediaOutfitLink(slug=slug, media_id=mid, outfit=outfit)
            session.add(link)
        link.position = i
    for key, link in existing.items():
        if key not in seen:
            session.delete(link)
    return len(seen)


def sync_legacy_outfit(session, media_ids):
    """Mirror each photo's first link back into PersonaMedia.outfit.

    The chat sender picks photos by that column, so a photo placed through the
    vault would never be sent if it were left empty.
    """
    for mid in set(media_ids or []):
        row = session.get(PersonaMedia, mid)
        if row is None:
            continue
        link = (session.query(MediaOutfitLink).filter_by(media_id=mid)
                .order_by(func.coalesce(MediaOutfitLink.position, 0)).first())
        row.outfit = link.outfit if link else ''


def backfill_media_links(session):
    """One-time: turn each photo's legacy outfit column into a link. Safe to
    run repeatedly — it only adds links that are missing."""
    have = {(l.media_id, l.outfit)
            for l in session.query(MediaOutfitLink.media_id,
                                   MediaOutfitLink.outfit).all()}
    added = 0
    rows = session.query(PersonaMedia).filter(
        PersonaMedia.outfit != '', PersonaMedia.outfit.isnot(None)).all()
    for r in rows:
        if (r.id, str(r.outfit)) in have:
            continue
        session.add(MediaOutfitLink(slug=r.slug, media_id=r.id,
                                    outfit=str(r.outfit),
                                    position=r.position or 0))
        added += 1
    return added


def reorder_persona_media(session, slug, ordered_ids):
    """Apply a new order, and move items between outfits, in one pass.
    ordered_ids is [{'id': ..., 'outfit': ...}, ...] in display order."""
    rows = {r.id: r for r in session.query(PersonaMedia).filter_by(slug=slug).all()}
    changed = 0
    for i, entry in enumerate(ordered_ids):
        row = rows.get(entry.get('id'))
        if row is None:
            continue
        row.position = i
        outfit = entry.get('outfit')
        if outfit is not None:
            row.outfit = str(outfit)[:120]
        changed += 1
    return changed


def get_persona_media(session, media_id):
    return session.get(PersonaMedia, media_id)


def delete_persona_media(session, media_id):
    row = session.get(PersonaMedia, media_id)
    if row:
        session.delete(row)
    return row


class User(Base):
    """A paying customer of the platform (a creator), as opposed to a fan."""
    __tablename__ = 'users'

    id = Column(String(32), primary_key=True, default=_uid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    # Google account id (the OAuth "sub"). Set for users who signed in with
    # Google; they have no usable password_hash.
    google_sub = Column(String(64), index=True)
    name = Column(String(120), default='')
    # user | manager | chatter | support | admin. 'user' is a workspace owner;
    # manager and chatter are seats inside someone else's workspace.
    role = Column(String(16), default='user')
    # Set for accounts that were active when per-tier limits were introduced:
    # until this date they keep the old unlimited entitlements, so enforcement
    # is not a retroactive downgrade mid-subscription. Admin-editable.
    grandfathered_until = Column(DateTime)
    # Superseded by the memberships table, which lets one account belong to
    # several workspaces. Kept only as the input to backfill_workspaces, which
    # runs once; nothing reads it at request time. Safe to drop a release after
    # every deployment has run the backfill.
    team_owner_id = Column(String(32), index=True)
    tier = Column(String(32), default='')          # '' until a plan is chosen
    # eur | usd | gbp. Euro is the base currency every price is set in, so an
    # account that never picks one sees the prices as they were decided.
    currency = Column(String(3), default='eur')
    status = Column(String(16), default='unpaid')  # unpaid | active | expired
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=_now)
    last_login = Column(DateTime)

    # Forgot-password flow. Single-use, cleared on consumption or replaced by
    # a fresh request; reset_token_expires makes an old, unclaimed link inert.
    reset_token = Column(String(64), index=True)
    reset_token_expires = Column(DateTime)

    # Stripe recurring billing. The customer id outlives any one subscription,
    # so it is kept even after a cancellation to reuse the saved card and to
    # open the billing portal. subscription_id is cleared when Stripe reports
    # the subscription gone; expires_at still runs to the end of the paid period.
    stripe_customer_id = Column(String(64), index=True)
    stripe_subscription_id = Column(String(64), index=True)

    # Referrals. referral_code is minted the first time a paid member opens
    # /referrals; referred_by records the code that brought this account in, so
    # the 5% commission is paid once, on their first subscription.
    referral_code = Column(String(16), unique=True, index=True)
    referred_by = Column(String(16), index=True)

    # OnlyFans moved to Pro and up. Accounts that had it connected before that
    # keep it on their existing plan, so the change is not a disconnection.
    onlyfans_grandfathered = Column(Boolean, default=False)

    # Set when an admin hands out a time-boxed trial. One per account, ever:
    # its presence is what refuses a second one.
    trial_at = Column(DateTime)

    # Creator profile, filled in after signup.
    brand = Column(String(120), default='')
    country = Column(String(80), default='')
    timezone = Column(String(64), default='')
    phone = Column(String(40), default='')
    website = Column(String(255), default='')
    bio = Column(Text, default='')
    # Data URL, sized down in the browser before it is posted.
    avatar = Column(Text, default='')
    onboarded_at = Column(DateTime)

    # Guided persona setup, keyed by persona slug:
    # {"aria": {"done": true, "seen": ["basics", "personality"]}}
    # On the user rather than the persona so progress follows the creator
    # across browsers.
    setup_json = Column(Text, default='')


class Payment(Base):
    """One row per checkout attempt (Oxapay invoice or Stripe session), created
    at checkout and updated by that provider's webhook."""
    __tablename__ = 'payments'

    id = Column(String(32), primary_key=True, default=_uid)
    user_id = Column(String(32), ForeignKey('users.id'), nullable=False, index=True)
    tier = Column(String(32), nullable=False)
    # A token checkout reuses this whole table and both webhooks; `tier` then
    # records the tier the pack was priced at rather than one being bought.
    kind = Column(String(16), default='subscription')  # subscription | tokens
    tokens = Column(Integer, default=0)
    provider = Column(String(16), default='oxapay')  # oxapay | stripe
    amount = Column(String(32), default='')
    currency = Column(String(16), default='USD')
    order_id = Column(String(64), unique=True, index=True)
    # Stripe checkout session ids run past 64 characters.
    track_id = Column(String(128), index=True)
    status = Column(String(24), default='pending')  # pending | Paying | Paid | expired
    # The referral code in play at checkout, so the webhook can pay the
    # commission without trusting anything the browser sends back.
    ref_code = Column(String(16), index=True)
    created_at = Column(DateTime, default=_now)
    paid_at = Column(DateTime)


Index('ix_payments_user_created', Payment.user_id, Payment.created_at)


class Workspace(Base):
    """A billing tenant: the personas, platform connections and plan one team
    works out of. A user can belong to several.

    The plan itself still lives on the owner's User row — tier, status,
    expires_at and Stripe ids — so one account is billed once no matter how
    many workspaces it is a member of.
    """
    __tablename__ = 'workspaces'

    # For every workspace that existed before this table, the id IS the owner's
    # user id. SavedPersona.owner_id already held that value, so the backfill
    # needs no data migration and no downtime.
    id = Column(String(32), primary_key=True, default=_uid)
    name = Column(String(120), default='')
    owner_id = Column(String(32), nullable=False, index=True)
    created_at = Column(DateTime, default=_now)


class Membership(Base):
    """A user's seat in a workspace, and what they may do in it."""
    __tablename__ = 'memberships'

    id = Column(String(32), primary_key=True, default=_uid)
    workspace_id = Column(String(32), nullable=False, index=True)
    user_id = Column(String(32), nullable=False, index=True)
    role = Column(String(16), default='chatter')   # owner | manager | chatter
    created_at = Column(DateTime, default=_now)


Index('ix_membership_unique', Membership.workspace_id, Membership.user_id,
      unique=True)


def list_memberships(session, user_id):
    """Every workspace this user can open, owned ones first then by age."""
    rows = (session.query(Membership, Workspace)
            .filter(Membership.user_id == user_id,
                    Workspace.id == Membership.workspace_id)
            .all())
    rows.sort(key=lambda r: (r[0].role != 'owner', r[1].created_at or _now()))
    return rows


def get_membership(session, workspace_id, user_id):
    return session.query(Membership).filter(
        Membership.workspace_id == workspace_id,
        Membership.user_id == user_id).first()


def list_workspace_members(session, workspace_id):
    rows = (session.query(Membership, User)
            .filter(Membership.workspace_id == workspace_id,
                    User.id == Membership.user_id).all())
    rows.sort(key=lambda r: (r[0].role != 'owner', r[0].created_at or _now()))
    return rows


def count_workspace_members(session, workspace_id):
    return session.query(Membership).filter(
        Membership.workspace_id == workspace_id).count()


def create_workspace(session, owner_id, name, workspace_id=None):
    ws = Workspace(owner_id=owner_id, name=(name or '')[:120])
    if workspace_id:
        ws.id = workspace_id
    session.add(ws)
    session.add(Membership(workspace_id=ws.id, user_id=owner_id, role='owner'))
    return ws


WORKSPACE_FLAG = 'workspaces_backfill'


def backfill_workspaces(session):
    """One-off: give every existing account a workspace whose id is its own user
    id, so the owner_id already on saved personas keeps resolving, and turn the
    old single-team column into memberships."""
    if get_app_setting(session, WORKSPACE_FLAG):
        return 0
    n = 0
    # Everyone gets their own workspace, including someone who was a seat in
    # another team: they may hold a plan and personas of their own, and can
    # now be in both places at once.
    for u in session.query(User).all():
        if session.get(Workspace, u.id) is None:
            create_workspace(session, u.id, u.brand or u.name or u.email, u.id)
            n += 1
    # The sessionmaker has autoflush off, so the workspaces just added are not
    # findable by get() until they are flushed — and the next loop looks them up.
    session.flush()
    for u in session.query(User).filter(User.team_owner_id.isnot(None)).all():
        if session.get(Workspace, u.team_owner_id) is None:
            continue
        if get_membership(session, u.team_owner_id, u.id) is None:
            session.add(Membership(workspace_id=u.team_owner_id, user_id=u.id,
                                   role=(u.role if u.role in ('manager', 'chatter')
                                         else 'chatter')))
    set_app_setting(session, WORKSPACE_FLAG, _now().isoformat())
    session.commit()
    return n


class Invite(Base):
    """A pending seat in a workspace. There is no mail sender in this app, so
    the token is handed to the owner as a link to pass on however they like —
    which also means it is a bearer credential and expires."""
    __tablename__ = 'invites'

    token = Column(String(64), primary_key=True)
    workspace_id = Column(String(32), nullable=False, index=True)
    # Optional: when set, only this address may accept, so a forwarded link is
    # useless to anyone else.
    email = Column(String(255), default='')
    role = Column(String(16), default='chatter')
    created_by = Column(String(32))
    created_at = Column(DateTime, default=_now)
    expires_at = Column(DateTime)
    accepted_at = Column(DateTime)
    accepted_by = Column(String(32))


def list_invites(session, workspace_id, pending_only=True):
    q = session.query(Invite).filter(Invite.workspace_id == workspace_id)
    if pending_only:
        q = q.filter(Invite.accepted_at.is_(None))
    return q.order_by(Invite.created_at.desc()).all()


def count_pending_invites(session, workspace_id):
    """Outstanding invites hold a seat each: without this two invites could be
    sent for one free seat and both accepted."""
    return session.query(Invite).filter(
        Invite.workspace_id == workspace_id,
        Invite.accepted_at.is_(None),
        Invite.expires_at > _now()).count()


class UsageCounter(Base):
    """Metered usage per workspace per calendar month, so a plan's allowance
    (AI image generations) can be enforced and shown back to the creator."""
    __tablename__ = 'usage_counters'

    id = Column(String(32), primary_key=True, default=_uid)
    workspace_id = Column(String(32), nullable=False, index=True)
    metric = Column(String(48), nullable=False)
    period = Column(String(7), nullable=False)     # 'YYYY-MM'
    count = Column(Integer, default=0)
    updated_at = Column(DateTime, default=_now)


Index('ix_usage_unique', UsageCounter.workspace_id, UsageCounter.metric,
      UsageCounter.period, unique=True)


class TokenLedger(Base):
    """Every token movement, append-only. The balance is the sum of the rows,
    never a column: people pay for these, so a mutable counter that a crash or a
    race can corrupt is not an option — with a ledger, any disputed balance can
    be reconstructed from what actually happened."""
    __tablename__ = 'token_ledger'

    id = Column(String(32), primary_key=True, default=_uid)
    workspace_id = Column(String(32), nullable=False, index=True)
    delta = Column(Integer, nullable=False)        # + granted/bought, - spent
    kind = Column(String(16), nullable=False)      # grant|purchase|spend|refund|adjust
    # What caused it: 'YYYY-MM' for a grant, a Payment id, a GenerationJob id.
    source = Column(String(64), default='', index=True)
    # Monthly grants expire at the end of their period; bought tokens never do.
    expires_at = Column(DateTime)
    note = Column(String(200), default='')
    created_at = Column(DateTime, default=_now, index=True)


Index('ix_token_ws_created', TokenLedger.workspace_id, TokenLedger.created_at)
# One grant per workspace per period, and one purchase per payment: the guard
# against a replayed webhook or a double-fired monthly grant is the database's,
# not the caller's.
Index('ix_token_source_kind', TokenLedger.workspace_id, TokenLedger.kind,
      TokenLedger.source, unique=True,
      sqlite_where=TokenLedger.kind.in_(('grant', 'purchase')),
      postgresql_where=TokenLedger.kind.in_(('grant', 'purchase')))


class GenerationJob(Base):
    """One AI image or video generation. Providers take minutes and answer by
    polling, so the request that starts one cannot be the request that finishes
    it."""
    __tablename__ = 'generation_jobs'

    id = Column(String(32), primary_key=True, default=_uid)
    workspace_id = Column(String(32), nullable=False, index=True)
    slug = Column(String(64), nullable=False, index=True)
    kind = Column(String(8), default='image')      # image | video
    provider = Column(String(24), default='')
    provider_job_id = Column(String(200), default='', index=True)
    spec_json = Column(Text, default='{}')
    status = Column(String(12), default='queued')  # queued|running|done|failed
    tokens = Column(Integer, default=0)            # reserved at submit
    result_media_ids = Column(Text, default='')    # CSV of PersonaMedia ids
    error = Column(String(500), default='')
    created_at = Column(DateTime, default=_now, index=True)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)


Index('ix_genjob_status_created', GenerationJob.status, GenerationJob.created_at)


class DemoEvent(Base):
    """What a demo account did: when it started, every time it hit the paywall
    the demo puts in front of Fanvue, and the moment it turned into a paying
    plan. Kept separate from the user row so a demo that converts still leaves
    the trail that shows how it got there."""
    __tablename__ = 'demo_events'

    id = Column(String(32), primary_key=True, default=_uid)
    user_id = Column(String(32), nullable=False, index=True)
    email = Column(String(255), default='')
    kind = Column(String(24), nullable=False, index=True)  # signin|blocked|converted
    detail = Column(String(120), default='')
    # The demo is one shared login, so the user row cannot tell two prospects
    # apart. visitor_id is a per-browser cookie and ref is the tag on the link
    # they were sent (/login?ref=jane) — together they answer "who was this".
    visitor_id = Column(String(32), default='', index=True)
    ref = Column(String(64), default='')
    ip = Column(String(64), default='')
    country = Column(String(80), default='')
    user_agent = Column(String(300), default='')
    created_at = Column(DateTime, default=_now, index=True)


def record_demo_event(session, user_id, email, kind, detail='', ip='',
                      user_agent='', visitor_id='', ref=''):
    e = DemoEvent(user_id=user_id, email=(email or '')[:255], kind=kind,
                  detail=(detail or '')[:120], ip=(ip or '')[:64],
                  user_agent=(user_agent or '')[:300],
                  visitor_id=(visitor_id or '')[:32], ref=(ref or '')[:64])
    session.add(e)
    return e


def set_demo_event_geo(session, event_id, country):
    e = session.get(DemoEvent, event_id)
    if e is not None:
        e.country = (country or '')[:80]
    return e


def list_demo_events(session, user_id=None, limit=500):
    q = session.query(DemoEvent)
    if user_id:
        q = q.filter(DemoEvent.user_id == user_id)
    return q.order_by(DemoEvent.created_at.desc()).limit(limit).all()


def demo_country_for_ip(session, ip):
    """A country already resolved for this IP, so a repeat visitor costs no
    second lookup."""
    if not ip:
        return ''
    row = (session.query(DemoEvent)
           .filter(DemoEvent.ip == ip, DemoEvent.country != '')
           .order_by(DemoEvent.created_at.desc()).first())
    return (row.country if row else '') or ''


def demo_event_summary(session):
    """Per demo user: how many of each kind, and when they were last seen."""
    out = {}
    for e in session.query(DemoEvent).order_by(DemoEvent.created_at.asc()).all():
        row = out.setdefault(e.user_id, {'email': e.email, 'counts': {},
                                         'first': None, 'last': None})
        row['counts'][e.kind] = row['counts'].get(e.kind, 0) + 1
        row['first'] = row['first'] or e.created_at
        row['last'] = e.created_at
        if e.email:
            row['email'] = e.email
    return out


def get_user_by_email(session, email):
    return session.query(User).filter(
        User.email == (email or '').strip().lower()).first()


def create_user(session, email, password_hash, name='', google_sub=None):
    u = User(email=(email or '').strip().lower(),
             password_hash=password_hash or '', name=name or '',
             google_sub=google_sub)
    session.add(u)
    session.flush()
    return u


def get_user_by_stripe_customer(session, customer_id):
    """The user a Stripe webhook is about. Renewal invoices carry the customer
    id but not our metadata, so this is the join key for them."""
    if not customer_id:
        return None
    return (session.query(User)
            .filter(User.stripe_customer_id == customer_id).first())


def get_user_by_google_sub(session, sub):
    if not sub:
        return None
    return session.query(User).filter(User.google_sub == sub).first()


def get_payment_by_order(session, order_id):
    return session.query(Payment).filter(Payment.order_id == order_id).first()


def list_users(session, limit=500):
    return session.query(User).order_by(User.created_at.desc()).limit(limit).all()


def list_team_members(session, owner_id):
    return session.query(User).filter(User.team_owner_id == owner_id).order_by(
        User.created_at.asc()).all()


def count_team_members(session, owner_id):
    """Seats in use, counting the owner."""
    return 1 + session.query(User).filter(User.team_owner_id == owner_id).count()


def get_usage(session, workspace_id, metric, period):
    row = session.query(UsageCounter).filter(
        UsageCounter.workspace_id == workspace_id,
        UsageCounter.metric == metric,
        UsageCounter.period == period).first()
    return int(row.count or 0) if row else 0


def bump_usage(session, workspace_id, metric, period, by=1):
    """Increment and return the new count. Two requests racing here can both
    read the same row; the overcount is at most the number of in-flight calls,
    which is cheaper than locking a row on every image generation."""
    row = session.query(UsageCounter).filter(
        UsageCounter.workspace_id == workspace_id,
        UsageCounter.metric == metric,
        UsageCounter.period == period).first()
    if row is None:
        row = UsageCounter(workspace_id=workspace_id, metric=metric,
                           period=period, count=0)
        session.add(row)
    row.count = int(row.count or 0) + by
    row.updated_at = _now()
    session.commit()
    return int(row.count)


# ── Tokens ───────────────────────────────────────────────────────────────────

def token_balance(session, workspace_id, at=None):
    """Spendable tokens: every row that has not expired. An expired monthly
    grant is left in place rather than deleted — the history is the point."""
    now = at or _now()
    total = (session.query(func.coalesce(func.sum(TokenLedger.delta), 0))
             .filter(TokenLedger.workspace_id == workspace_id,
                     or_(TokenLedger.expires_at.is_(None),
                         TokenLedger.expires_at > now))
             .scalar())
    return int(total or 0)


def token_post(session, workspace_id, delta, kind, source='', expires_at=None,
                note=''):
    """Append one movement. Returns the row, or None when a unique grant or
    purchase for this source already exists — that duplicate is the whole
    defence against a replayed webhook, so it is a no-op and not an error."""
    if kind in ('grant', 'purchase'):
        dupe = session.query(TokenLedger).filter(
            TokenLedger.workspace_id == workspace_id,
            TokenLedger.kind == kind,
            TokenLedger.source == (source or '')).first()
        if dupe:
            return None
    row = TokenLedger(workspace_id=workspace_id, delta=int(delta), kind=kind,
                       source=(source or '')[:64], expires_at=expires_at,
                       note=(note or '')[:200])
    session.add(row)
    try:
        session.commit()
    except Exception:
        session.rollback()
        if kind in ('grant', 'purchase'):
            return None
        raise
    return row


def token_grant(session, workspace_id, amount, period, expires_at, note=''):
    """The monthly allowance. Keyed on the period so firing twice is harmless."""
    return token_post(session, workspace_id, abs(int(amount)), 'grant',
                       source=period, expires_at=expires_at, note=note)


def token_purchase(session, workspace_id, amount, payment_id, note=''):
    return token_post(session, workspace_id, abs(int(amount)), 'purchase',
                       source=payment_id, note=note)


def expiring_balance(session, workspace_id, at=None):
    """The part of the balance that lapses at period end, and when. A spend has
    to be charged against this first — otherwise an unexpiring row cancels a
    bought token and the allowance silently lapses unused, which is the
    creator paying twice for the same generation."""
    now = at or _now()
    rows = (session.query(TokenLedger)
            .filter(TokenLedger.workspace_id == workspace_id,
                    TokenLedger.expires_at.isnot(None),
                    TokenLedger.expires_at > now).all())
    total = sum(int(r.delta) for r in rows)
    soonest = min((r.expires_at for r in rows if r.delta > 0), default=None)
    return max(0, total), soonest


def token_debit(session, workspace_id, amount, source, note=''):
    """Reserve tokens for a job. Returns False without posting anything when
    the balance will not cover it, so an unaffordable job never reaches the
    provider.

    The charge is split so it drains the expiring allowance before anything
    bought: the expiring part carries the grant's own expiry, so when the month
    turns, the grant and what it paid for lapse together and the purchased
    balance is left whole."""
    amount = abs(int(amount))
    if amount and token_balance(session, workspace_id) < amount:
        return False
    if not amount:
        return True
    expiring, when = expiring_balance(session, workspace_id)
    from_grant = min(amount, expiring) if when else 0
    if from_grant:
        token_post(session, workspace_id, -from_grant, 'spend', source=source,
                    expires_at=when, note=note)
    if amount - from_grant:
        token_post(session, workspace_id, -(amount - from_grant), 'spend',
                    source=source, note=note)
    return True


def token_refund_part(session, workspace_id, source, amount, note=''):
    """Give back part of what was reserved against `source`.

    For a job that delivered less than it was quoted -- a model that decides
    the output length itself, handed a longer clip than it will produce. The
    whole reservation is not owed back, but the difference is, and it returns
    to the bucket it left for the same reason a full refund does.
    """
    amount = int(amount or 0)
    if amount <= 0:
        return 0
    rows = (session.query(TokenLedger)
            .filter(TokenLedger.workspace_id == workspace_id,
                    TokenLedger.source == source,
                    TokenLedger.kind.in_(('spend', 'refund'))).all())
    owed = {}
    for r in rows:
        owed[r.expires_at] = owed.get(r.expires_at, 0) + int(r.delta)
    given = 0
    # Soonest-expiring first, mirroring the order a spend drains them in.
    for expires_at in sorted(owed, key=lambda e: (e is None, e)):
        if given >= amount:
            break
        outstanding = -owed[expires_at]
        if outstanding <= 0:
            continue
        give = min(outstanding, amount - given)
        token_post(session, workspace_id, give, 'refund', source=source,
                    expires_at=expires_at, note=note)
        given += give
    return given


def token_settle(session, workspace_id, source, owed, note=''):
    """Bring what `source` has cost down to `owed`, refunding the rest.

    Stated as a target rather than an amount so it is safe to run twice: a
    second settle for the same job finds nothing above `owed` and posts
    nothing, where a second partial refund would pay the difference again."""
    charged = -sum(int(r.delta) for r in session.query(TokenLedger).filter(
        TokenLedger.workspace_id == workspace_id,
        TokenLedger.source == source,
        TokenLedger.kind.in_(('spend', 'refund'))).all())
    return token_refund_part(session, workspace_id, source,
                             charged - max(0, int(owed)), note=note)


def token_refund(session, workspace_id, source, note=''):
    """Give back whatever was reserved against `source`, once. A job that fails
    or is swept must not cost anything, and a double refund must not pay out.

    Each refund row mirrors the expiry of the spend it reverses, so credits
    come back into the bucket they left — refunding an allowance charge as a
    permanent token would mint balance out of a failed job."""
    rows = (session.query(TokenLedger)
            .filter(TokenLedger.workspace_id == workspace_id,
                    TokenLedger.source == source,
                    TokenLedger.kind.in_(('spend', 'refund'))).all())
    owed = {}
    for r in rows:
        owed[r.expires_at] = owed.get(r.expires_at, 0) + int(r.delta)
    total = 0
    for expires_at, delta in owed.items():
        if delta >= 0:
            continue
        token_post(session, workspace_id, -delta, 'refund', source=source,
                    expires_at=expires_at, note=note)
        total += -delta
    return total


def token_history(session, workspace_id, limit=50):
    return (session.query(TokenLedger)
            .filter(TokenLedger.workspace_id == workspace_id)
            .order_by(TokenLedger.created_at.desc()).limit(limit).all())


# ── Generation jobs ───────────────────────────────────────────────────────────

def queue_generation(session, workspace_id, slug, kind, spec_json, tokens=0,
                     provider=''):
    row = GenerationJob(workspace_id=workspace_id, slug=slug, kind=kind,
                        spec_json=spec_json, tokens=int(tokens or 0),
                        provider=provider or '')
    session.add(row)
    session.commit()
    return row


def get_generation(session, job_id):
    return session.query(GenerationJob).filter(GenerationJob.id == job_id).first()


def list_generations(session, workspace_id, slug=None, limit=40):
    q = session.query(GenerationJob).filter(
        GenerationJob.workspace_id == workspace_id)
    if slug:
        q = q.filter(GenerationJob.slug == slug)
    return q.order_by(GenerationJob.created_at.desc()).limit(limit).all()


def delete_generation(session, job_id):
    """Drop one job row. The caller deals with its media first — a job is a
    record of work, not the owner of what the work produced."""
    row = get_generation(session, job_id)
    if not row:
        return False
    session.delete(row)
    session.commit()
    return True


def stale_generations(session, cutoff, limit=200):
    """Finished jobs old enough that nobody is still reading them. Whether a
    done one can actually go depends on its media, which the caller checks."""
    return (session.query(GenerationJob)
            .filter(GenerationJob.status.in_(('failed', 'done')))
            .filter(GenerationJob.created_at < cutoff)
            .order_by(GenerationJob.created_at).limit(limit).all())


def open_generations(session, limit=50):
    """Jobs the poller still owes an answer for."""
    return (session.query(GenerationJob)
            .filter(GenerationJob.status.in_(('queued', 'running')))
            .order_by(GenerationJob.created_at).limit(limit).all())


def update_generation(session, job_id, **fields):
    row = get_generation(session, job_id)
    if not row:
        return None
    for key, value in fields.items():
        setattr(row, key, value)
    if fields.get('status') in ('done', 'failed') and not row.finished_at:
        row.finished_at = _now()
    if fields.get('status') == 'running' and not row.started_at:
        row.started_at = _now()
    session.commit()
    return row


# ── Model reference sets and audio ────────────────────────────────────────────

def model_references(session, slug, model_key, role=None):
    """The gallery photos assigned to one model, in slot order. Without a role
    this returns both groups, face first, which is the order a generation wants
    them in."""
    q = (session.query(ModelReferenceSet)
         .filter(ModelReferenceSet.slug == slug,
                 ModelReferenceSet.model_key == model_key))
    if role:
        q = q.filter(ModelReferenceSet.role == role)
    return q.order_by(ModelReferenceSet.role.desc(),
                      ModelReferenceSet.slot_index).all()


def set_model_references(session, slug, model_key, media_ids, role='face'):
    """Replace one group for one model. Replacing rather than merging keeps slot
    order the caller's to decide, and the per-model cap is enforced by the
    caller that knows the model — this only stores what it is given."""
    (session.query(ModelReferenceSet)
     .filter(ModelReferenceSet.slug == slug,
             ModelReferenceSet.model_key == model_key,
             ModelReferenceSet.role == role).delete())
    for index, media_id in enumerate(media_ids):
        session.add(ModelReferenceSet(slug=slug, model_key=model_key,
                                      media_id=media_id, role=role,
                                      slot_index=index))
    session.commit()
    return len(media_ids)


def drop_model_references(session, media_id):
    """Every model slot holding this photo. Called when the photo is deleted:
    a slot pointing at a gone photo is a generation that fails at the provider."""
    n = (session.query(ModelReferenceSet)
         .filter(ModelReferenceSet.media_id == media_id).delete())
    session.commit()
    return n


def list_audio_references(session, slug):
    return (session.query(AudioReference)
            .filter(AudioReference.slug == slug)
            .order_by(AudioReference.created_at).all())


def add_audio_reference(session, slug, gcs_path, fmt='mp3', size_bytes=0):
    row = AudioReference(slug=slug, gcs_path=gcs_path, fmt=fmt,
                         size_bytes=int(size_bytes or 0))
    session.add(row)
    session.commit()
    return row


def delete_audio_reference(session, audio_id):
    row = (session.query(AudioReference)
           .filter(AudioReference.id == audio_id).first())
    if not row:
        return None
    path = row.gcs_path or ''
    session.delete(row)
    session.commit()
    return path


def _sync_columns(table_name, model):
    """create_all() only creates whole tables, so a model column added — or
    widened — after the table exists needs an explicit ALTER. Postgres enforces
    VARCHAR limits where SQLite silently ignores them, so a too-narrow column
    only fails in production."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if table_name not in insp.get_table_names():
        return
    have = {c['name']: c for c in insp.get_columns(table_name)}
    for col in model.__table__.columns:
        ddl = col.type.compile(engine.dialect)
        existing = have.get(col.name)
        if existing is None:
            stmt = f'ALTER TABLE {table_name} ADD COLUMN {col.name} {ddl}'
        else:
            want = getattr(col.type, 'length', None)
            got = getattr(existing['type'], 'length', None)
            if not (want and got and got < want):
                continue
            stmt = f'ALTER TABLE {table_name} ALTER COLUMN {col.name} TYPE {ddl}'
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception:
            pass


def grandfather_existing_users(session):
    """One-off at the cutover: everyone already paying keeps the old unlimited
    entitlements until the period they have already paid for runs out. Guarded
    by a setting so a later cleared date is not silently restored."""
    if get_app_setting(session, GRANDFATHER_FLAG):
        return 0
    n = 0
    for u in session.query(User).filter(User.status == 'active',
                                        User.expires_at.isnot(None),
                                        User.grandfathered_until.is_(None)).all():
        u.grandfathered_until = u.expires_at
        n += 1
    set_app_setting(session, GRANDFATHER_FLAG, _now().isoformat())
    session.commit()
    return n


def grandfather_onlyfans_users(session):
    """One-off at the cutover: an account that already had an OnlyFans account
    connected keeps OnlyFans on whatever plan it is on. Guarded by a setting so
    a later revocation is not silently undone."""
    if get_app_setting(session, OF_GRANDFATHER_FLAG):
        return 0
    prefix = 'onlyfans_account_'
    slugs = [r.key[len(prefix):] for r in session.query(AppSetting)
             .filter(AppSetting.key.like(prefix + '%')).all()
             if (r.value or '').strip() and not r.key.startswith(prefix + 'meta_')]
    n = 0
    if slugs:
        # A persona's owner_id is its workspace; the plan sits on the owner.
        ws_ids = {sp.owner_id for sp in session.query(SavedPersona)
                  .filter(SavedPersona.slug.in_(slugs)).all() if sp.owner_id}
        if ws_ids:
            uids = set(ws_ids) | {w.owner_id for w in session.query(Workspace)
                                  .filter(Workspace.id.in_(ws_ids)).all() if w.owner_id}
            for u in session.query(User).filter(User.id.in_(uids)).all():
                if not u.onlyfans_grandfathered:
                    u.onlyfans_grandfathered = True
                    n += 1
    set_app_setting(session, OF_GRANDFATHER_FLAG, _now().isoformat())
    session.commit()
    return n


# ── PPV funnels, fan scoring and adaptive testing ─────────────────────────────
# One fan per row per persona: who they are, what the classifier made of them,
# and the two scores everything else keys off. Written nightly and on every
# purchase, so it is a cache of derivable facts — losing it costs accuracy, not
# money. The ppv_drops ledger remains the authority on what was actually paid.

class FanProfile(Base):
    __tablename__ = 'fan_profiles'

    id = Column(String(32), primary_key=True, default=_uid)
    persona = Column(String(64), index=True)
    fan_uuid = Column(String(64), index=True)
    handle = Column(String(64))
    platform = Column(String(16), default='fanvue')

    fan_type = Column(String(4))                 # GF CO DO SU FL LU WH SK
    type_confidence = Column(Integer, default=0)  # 0-100
    type_updated_at = Column(DateTime)
    type_msgs_at = Column(Integer, default=0)     # msg count when last classified

    subscribed_at = Column(DateTime)
    last_msg_at = Column(DateTime)
    churned_at = Column(DateTime)
    lifetime_spend = Column(Integer, default=0)   # cents
    spend_30d = Column(Integer, default=0)        # cents
    tips_total = Column(Integer, default=0)       # cents

    frs = Column(Integer, default=0)              # 0-100 fan rank score
    crs = Column(Integer, default=0)              # 0-100 churn risk score
    tier = Column(String(1), default='C')         # S A B C D
    tier_since = Column(DateTime)
    tier_below_since = Column(DateTime)           # hysteresis: 14d before demotion
    scores_updated_at = Column(DateTime)

    chargeback_at = Column(DateTime)              # FRS frozen at 0 for 90 days
    review_reason = Column(String(64))            # queued for a human, why
    review_at = Column(DateTime)
    notes = Column(Text)                          # JSON scratch (signals, vectors)

    source = Column(String(40))                   # channel they arrived from
    source_medium = Column(String(40))
    source_campaign = Column(String(40))
    source_at = Column(DateTime)


Index('ix_fan_profiles_key', FanProfile.persona, FanProfile.fan_uuid, unique=True)
Index('ix_fan_profiles_rank', FanProfile.persona, FanProfile.frs)


class FanEvent(Base):
    """Append-only log of everything that happened to a fan. The bandit's reward
    and every churn signal are derived from this, so it is never rewritten —
    only inserted into."""
    __tablename__ = 'fan_events'

    id = Column(String(32), primary_key=True, default=_uid)
    ts = Column(DateTime, default=_now, index=True)
    persona = Column(String(64), index=True)
    fan_uuid = Column(String(64), index=True)
    assignment_id = Column(String(32), index=True)
    kind = Column(String(16))     # msg_in msg_out ppv_sent ppv_opened ppv_bought
                                  # tip unsub resub chargeback reward pitch_ignored
    amount = Column(Integer, default=0)           # cents where money is involved
    content_id = Column(String(64))
    detail = Column(Text)


Index('ix_fan_events_fan', FanEvent.persona, FanEvent.fan_uuid, FanEvent.ts)


class FunnelAssignment(Base):
    """Which funnel a fan is in, and the sub-arm variant being tested. Closed
    with an exit_reason; the 7-day reward window is measured from assigned_at."""
    __tablename__ = 'funnel_assignments'

    id = Column(String(32), primary_key=True, default=_uid)
    persona = Column(String(64), index=True)
    fan_uuid = Column(String(64), index=True)
    funnel_id = Column(String(4))                 # F1..F10
    fan_type = Column(String(4))                  # context at assignment time
    variant_json = Column(Text)                   # {price_tier, delay, opener, preview}
    assigned_at = Column(DateTime, default=_now, index=True)
    exited_at = Column(DateTime)
    exit_reason = Column(String(32))
    pitches_sent = Column(Integer, default=0)
    pitches_ignored = Column(Integer, default=0)
    revenue_cents = Column(Integer, default=0)    # settled inside the reward window
    rewarded_at = Column(DateTime)                # when the posterior was updated


Index('ix_funnel_assign_fan', FunnelAssignment.persona, FunnelAssignment.fan_uuid,
      FunnelAssignment.assigned_at)


class FunnelPosterior(Base):
    """Beta posterior per (fan_type × funnel × variant). Thompson sampling draws
    from these; the priors in the spec's matrix seed alpha."""
    __tablename__ = 'funnel_posteriors'

    id = Column(String(32), primary_key=True, default=_uid)
    persona = Column(String(64), index=True)
    fan_type = Column(String(4))
    funnel_id = Column(String(4))
    variant_key = Column(String(64), default='')
    alpha = Column(Integer, default=1)            # successes + 1
    beta = Column(Integer, default=1)             # failures + 1
    n = Column(Integer, default=0)
    revenue_cents = Column(Integer, default=0)
    churn_n = Column(Integer, default=0)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


Index('ix_posterior_key', FunnelPosterior.persona, FunnelPosterior.fan_type,
      FunnelPosterior.funnel_id, FunnelPosterior.variant_key, unique=True)


class FanReward(Base):
    """A loyalty reward that was delivered, with the spend either side of it so
    a track that does not pay for itself can be killed."""
    __tablename__ = 'fan_rewards'

    id = Column(String(32), primary_key=True, default=_uid)
    persona = Column(String(64), index=True)
    fan_uuid = Column(String(64), index=True)
    track = Column(String(16))                    # tenure | spend | engagement
    milestone = Column(String(32))
    delivered_at = Column(DateTime, default=_now)
    spend_30d_before = Column(Integer, default=0)
    spend_30d_after = Column(Integer)


Index('ix_fan_rewards_key', FanReward.persona, FanReward.fan_uuid,
      FanReward.track, FanReward.milestone, unique=True)


def set_fan_source(session, persona, fan_uuid, source, medium='', campaign=''):
    """Record where a fan arrived from. First touch wins: a later link click
    must not overwrite the channel that actually found them."""
    if not source:
        return None
    row = get_fan_profile(session, persona, fan_uuid, create=True)
    if row is None or row.source:
        return row
    row.source = source[:40]
    row.source_medium = (medium or '')[:40]
    row.source_campaign = (campaign or '')[:40]
    row.source_at = _now()
    return row


def fan_stats_by_source(session, persona):
    """{source: {fans, payers, spend, subs}} for one persona, with the fans that
    carry no tag under ''. Spend rides along because a channel is worth what its
    fans go on to pay, not how many of them arrive."""
    paid = case((FanProfile.lifetime_spend > 0, 1), else_=0)
    subbed = case((FanProfile.subscribed_at.isnot(None), 1), else_=0)
    rows = (session.query(FanProfile.source,
                          func.count(FanProfile.id),
                          func.sum(paid),
                          func.sum(FanProfile.lifetime_spend),
                          func.sum(subbed))
            .filter(FanProfile.persona == persona)
            .group_by(FanProfile.source).all())
    return {(src or ''): {'fans': int(fans or 0), 'payers': int(payers or 0),
                          'spend': int(spend or 0), 'subs': int(subs or 0)}
            for src, fans, payers, spend, subs in rows}


class ScheduledPost(Base):
    """One post waiting to go out. The queue lives in the database rather than
    in a worker's memory because the workers are restarted whenever Cloud Run
    feels like it, and a post that misses its slot is worse than no queue."""
    __tablename__ = 'scheduled_posts'

    id = Column(String(32), primary_key=True, default=_uid)
    persona = Column(String(64), index=True)
    platform = Column(String(16))                  # x | threads
    text = Column(Text, nullable=False)
    run_at = Column(DateTime, index=True)          # UTC
    status = Column(String(12), default='queued')  # queued sending posted failed cancelled
    attempts = Column(Integer, default=0)
    posted_at = Column(DateTime)
    external_id = Column(String(64))               # the tweet or thread it became
    error = Column(String(300))
    # One item from the persona's library. A reference rather than a copy, so
    # editing the photo edits every post still waiting to use it.
    # A Fanvue vault item is carried as 'fv:{uuid}', which is 39 characters —
    # Postgres refuses the write at VARCHAR(32) where SQLite silently truncates,
    # so this has to be wide enough for the longest id, not the library's own.
    media_id = Column(String(64), default='')
    # Every file on the post, comma-separated in display order. media_id holds
    # the first, so rows written before a post could carry more than one still
    # read correctly.
    media_ids = Column(Text, default='')
    # Fanvue feed posts carry a visibility and an optional unlock price; every
    # other channel ignores both.
    audience = Column(String(40), default='')
    price_cents = Column(Integer, default=0)
    # Instagram alone has three places a post can land. Empty means a feed
    # post, which is what every other channel's single destination amounts to.
    ig_kind = Column(String(8), default='')        # '' | post | story | reel
    # Reddit needs a destination the other channels do not have: which
    # subreddit, which flair (most NSFW subs auto-remove a post without one)
    # and which submission kind. A crosspost is several rows rather than a list
    # in one -- each subreddit gets its own slot, title and flair, which is
    # also what keeps a fan-out from reading as the same post spammed six times.
    rd_sub = Column(String(64), default='')
    rd_flair = Column(String(64), default='')
    rd_kind = Column(String(8), default='')        # '' | image | video | text | link
    created_at = Column(DateTime, default=_now)


Index('ix_scheduled_due', ScheduledPost.status, ScheduledPost.run_at)
Index('ix_scheduled_persona', ScheduledPost.persona, ScheduledPost.run_at)


def queue_post(session, persona, platform, text, run_at, media_id='', status='queued',
               audience='', price_cents=0, media_ids=None, ig_kind='',
               rd_sub='', rd_flair='', rd_kind=''):
    ids = [str(m) for m in (media_ids or []) if m] or ([media_id] if media_id else [])
    row = ScheduledPost(persona=persona, platform=platform, text=text,
                        run_at=run_at, media_id=(ids[0] if ids else ''), status=status,
                        media_ids=','.join(ids),
                        audience=audience or '', price_cents=int(price_cents or 0),
                        ig_kind=ig_kind or '', rd_sub=rd_sub or '',
                        rd_flair=rd_flair or '', rd_kind=rd_kind or '')
    session.add(row)
    session.flush()
    return row


def due_posts(session, now, limit=20):
    return (session.query(ScheduledPost)
            .filter(ScheduledPost.status == 'queued', ScheduledPost.run_at <= now)
            .order_by(ScheduledPost.run_at).limit(limit).all())


def claim_post(session, post_id):
    """Take ownership of one queued post. The status check is part of the UPDATE,
    so two workers reaching the same post cannot both publish it — the loser
    updates no rows and skips. Sessions here outlive their commits, so the
    update refreshes what they already hold rather than leaving it stale."""
    n = (session.query(ScheduledPost)
         .filter(ScheduledPost.id == post_id, ScheduledPost.status == 'queued')
         .update({'status': 'sending', 'attempts': ScheduledPost.attempts + 1},
                 synchronize_session='fetch'))
    session.commit()
    return bool(n)


def finish_post(session, post_id, external_id='', error='', retry_at=None):
    """Bank the outcome. A failure goes back in the queue when a retry time is
    given, and is only marked failed once there is nothing left to try."""
    row = session.query(ScheduledPost).filter(ScheduledPost.id == post_id).first()
    if row is None:
        return None
    if error:
        row.error = error[:300]
        if retry_at is not None:
            row.status = 'queued'
            row.run_at = retry_at
        else:
            row.status = 'failed'
    else:
        row.status = 'posted'
        row.posted_at = _now()
        row.external_id = (external_id or '')[:64]
        row.error = None
    return row


def list_posts(session, persona, limit=50, since=None, until=None):
    """The persona's posts, newest slot first. `since` and `until` narrow it to
    one stretch of the calendar — the planner asks for a week, where the newest
    fifty would silently cut a busy one short."""
    q = session.query(ScheduledPost).filter(ScheduledPost.persona == persona)
    if since is not None:
        q = q.filter(ScheduledPost.run_at >= since)
    if until is not None:
        q = q.filter(ScheduledPost.run_at < until)
    return q.order_by(ScheduledPost.run_at.desc()).limit(limit).all()


# A post the creator may still act on. Kept here rather than imported from
# growth so the data layer does not depend on the content layer.
_EDITABLE = ('queued', 'manual')


def cancel_post(session, persona, post_id):
    """Cancel a post that has not gone out. A post already sending is left
    alone: the worker owns it, and the send may already be away."""
    n = (session.query(ScheduledPost)
         .filter(ScheduledPost.id == post_id, ScheduledPost.persona == persona,
                 ScheduledPost.status.in_(_EDITABLE))
         .update({'status': 'cancelled'}, synchronize_session='fetch'))
    return bool(n)


def delete_post(session, persona, post_id):
    """Remove a post from the calendar for good. Anything the worker is holding
    is left alone — cancelling is the way to stop one of those — but a post that
    already went out, failed or was called off is only clutter by then, so it
    deletes like any other."""
    n = (session.query(ScheduledPost)
         .filter(ScheduledPost.id == post_id, ScheduledPost.persona == persona,
                 ScheduledPost.status != 'sending')
         .delete(synchronize_session='fetch'))
    return bool(n)


def post_media_ids(row):
    """Every file on a post, in order. Reads the list where one was written and
    falls back to the single id, so a row from either era answers the same."""
    raw = str(getattr(row, 'media_ids', '') or '')
    if raw:
        return [m for m in raw.split(',') if m]
    one = str(getattr(row, 'media_id', '') or '')
    return [one] if one else []


def update_post(session, persona, post_id, text=None, run_at=None, media_id=None,
                audience=None, price_cents=None, media_ids=None, ig_kind=None,
                rd_sub=None, rd_flair=None, rd_kind=None):
    """Edit a post that has not gone out yet. Like cancel_post, only a `queued`
    row is the caller's to touch: once the worker has claimed it the send may
    already be away, and once it has posted the text is history rather than a
    draft. Returns whether anything was changed.

    `media_id` of '' detaches the media; None leaves it alone. They are
    different answers, so an empty string cannot mean "unchanged" here.

    A by-hand post edits the same way: nothing has claimed it either."""
    fields = {}
    if text is not None:
        fields['text'] = text
    if run_at is not None:
        fields['run_at'] = run_at
    if media_ids is not None:
        # '' detaches everything here too, the same answer media_id gives.
        ids = [str(m) for m in media_ids if m]
        fields['media_ids'] = ','.join(ids)
        fields['media_id'] = ids[0] if ids else ''
    elif media_id is not None:
        fields['media_id'] = media_id
        fields['media_ids'] = media_id or ''
    if audience is not None:
        fields['audience'] = audience
    if price_cents is not None:
        fields['price_cents'] = int(price_cents or 0)
    if ig_kind is not None:
        fields['ig_kind'] = ig_kind or ''
    if rd_sub is not None:
        fields['rd_sub'] = rd_sub or ''
    if rd_flair is not None:
        fields['rd_flair'] = rd_flair or ''
    if rd_kind is not None:
        fields['rd_kind'] = rd_kind or ''
    if not fields:
        return False
    n = (session.query(ScheduledPost)
         .filter(ScheduledPost.id == post_id, ScheduledPost.persona == persona,
                 ScheduledPost.status.in_(_EDITABLE))
         .update(fields, synchronize_session='fetch'))
    return bool(n)


class LinkClick(Base):
    """One row per click on a tracked link. This replaces a boolean on the fan,
    which could only ever answer "did they click" — a channel is judged on how
    many clicks it sends and what those turn into, and one flag per fan cannot
    say that. The fan is optional because most clicks arrive from a bio, where
    nobody is identified until they start a conversation."""
    __tablename__ = 'link_clicks'

    id = Column(String(32), primary_key=True, default=_uid)
    persona = Column(String(64), index=True)
    source = Column(String(40), index=True)      # the channel tag on the link
    kind = Column(String(16))                    # chat | paid | trial | custom
    target = Column(String(500))
    fan_key = Column(String(80), index=True)     # 'tg:123' / 'x:456', '' when unknown
    referrer = Column(String(300))
    user_agent = Column(String(300))
    created_at = Column(DateTime, default=_now, index=True)


Index('ix_clicks_persona_at', LinkClick.persona, LinkClick.created_at)
Index('ix_clicks_persona_source', LinkClick.persona, LinkClick.source)


def record_click(session, persona, source, kind, target, fan_key='',
                 referrer='', user_agent=''):
    row = LinkClick(persona=persona, source=(source or '')[:40], kind=(kind or '')[:16],
                    target=(target or '')[:500], fan_key=(fan_key or '')[:80],
                    referrer=(referrer or '')[:300], user_agent=(user_agent or '')[:300])
    session.add(row)
    return row


def click_stats(session, persona, since=None):
    """{source: {clicks, fans, last}} for one persona. `fans` counts the distinct
    identified clickers, which is always fewer than the clicks — a bio link is
    anonymous until the conversation starts, and the same fan clicks twice."""
    q = session.query(LinkClick.source, func.count(LinkClick.id),
                      func.count(func.distinct(LinkClick.fan_key)),
                      func.max(LinkClick.created_at)).filter(LinkClick.persona == persona)
    if since is not None:
        q = q.filter(LinkClick.created_at >= since)
    out = {}
    for source, clicks, fans, last in q.group_by(LinkClick.source).all():
        # The distinct count includes the empty key when any click was anonymous,
        # so it would otherwise report one fan for a channel that identified none.
        anon = (session.query(func.count(LinkClick.id))
                .filter(LinkClick.persona == persona, LinkClick.source == source,
                        or_(LinkClick.fan_key == '', LinkClick.fan_key.is_(None)))
                .scalar() or 0)
        out[source or ''] = {'clicks': int(clicks or 0),
                             'fans': max(0, int(fans or 0) - (1 if anon else 0)),
                             'anon': int(anon),
                             'last': _epoch(last)}
    return out


def recent_clicks(session, persona, limit=20):
    rows = (session.query(LinkClick).filter(LinkClick.persona == persona)
            .order_by(LinkClick.created_at.desc()).limit(limit).all())
    return [{'id': r.id, 'source': r.source or '', 'kind': r.kind or '',
             'target': r.target or '', 'fan_key': r.fan_key or '',
             'at': _epoch(r.created_at)}
            for r in rows]


def get_fan_profile(session, persona, fan_uuid, handle=None, create=True):
    row = (session.query(FanProfile)
           .filter(FanProfile.persona == persona, FanProfile.fan_uuid == fan_uuid)
           .first())
    if row is None and create:
        row = FanProfile(persona=persona, fan_uuid=fan_uuid, handle=handle or '')
        session.add(row)
        try:
            session.flush()
        except Exception:
            # Two reply workers can reach the same new fan at once; the unique
            # index settles it and the loser re-reads the winner's row.
            session.rollback()
            row = (session.query(FanProfile)
                   .filter(FanProfile.persona == persona,
                           FanProfile.fan_uuid == fan_uuid).first())
            if row is None:
                return None
    if row is not None and handle and row.handle != handle:
        row.handle = handle
    return row


def log_fan_event(session, persona, fan_uuid, kind, amount=0, assignment_id=None,
                  content_id=None, detail=''):
    ev = FanEvent(persona=persona, fan_uuid=fan_uuid, kind=kind[:16],
                  amount=int(amount or 0), assignment_id=assignment_id,
                  content_id=(content_id or '')[:64], detail=(detail or '')[:2000])
    session.add(ev)
    return ev


def fan_events(session, persona, fan_uuid, since=None, kinds=None, limit=500):
    q = (session.query(FanEvent)
         .filter(FanEvent.persona == persona, FanEvent.fan_uuid == fan_uuid))
    if since:
        q = q.filter(FanEvent.ts >= since)
    if kinds:
        q = q.filter(FanEvent.kind.in_(list(kinds)))
    return q.order_by(FanEvent.ts.desc()).limit(limit).all()


def open_assignment(session, persona, fan_uuid):
    return (session.query(FunnelAssignment)
            .filter(FunnelAssignment.persona == persona,
                    FunnelAssignment.fan_uuid == fan_uuid,
                    FunnelAssignment.exited_at.is_(None))
            .order_by(FunnelAssignment.assigned_at.desc()).first())


def close_assignment(session, assignment, reason, when=None):
    if assignment is None or assignment.exited_at:
        return assignment
    assignment.exited_at = when or _now()
    assignment.exit_reason = (reason or '')[:32]
    return assignment


def get_posterior(session, persona, fan_type, funnel_id, variant_key='',
                  prior_alpha=1):
    row = (session.query(FunnelPosterior)
           .filter(FunnelPosterior.persona == persona,
                   FunnelPosterior.fan_type == fan_type,
                   FunnelPosterior.funnel_id == funnel_id,
                   FunnelPosterior.variant_key == (variant_key or ''))
           .first())
    if row is None:
        row = FunnelPosterior(persona=persona, fan_type=fan_type,
                              funnel_id=funnel_id, variant_key=(variant_key or ''),
                              alpha=max(1, int(prior_alpha)), beta=1)
        session.add(row)
        session.flush()
    return row


def list_posteriors(session, persona):
    return (session.query(FunnelPosterior)
            .filter(FunnelPosterior.persona == persona).all())


def due_assignments(session, persona, before):
    """Closed-or-old assignments whose 7-day reward window has passed and whose
    posterior has not been updated yet."""
    return (session.query(FunnelAssignment)
            .filter(FunnelAssignment.persona == persona,
                    FunnelAssignment.rewarded_at.is_(None),
                    FunnelAssignment.assigned_at <= before)
            .order_by(FunnelAssignment.assigned_at).limit(200).all())


def top_fans(session, persona, limit=100):
    return (session.query(FanProfile)
            .filter(FanProfile.persona == persona)
            .order_by(FanProfile.frs.desc()).limit(limit).all())


def fan_reward_given(session, persona, fan_uuid, track, milestone):
    return (session.query(FanReward)
            .filter(FanReward.persona == persona, FanReward.fan_uuid == fan_uuid,
                    FanReward.track == track, FanReward.milestone == milestone)
            .first())

class ReferralClick(Base):
    """One visit to a referral link. Anonymous by design — the fan-out from a
    link to a signup is what the counter shows, so a click is not tied to an
    account it does not have yet."""
    __tablename__ = 'referral_clicks'

    id = Column(String(32), primary_key=True, default=_uid)
    code = Column(String(16), index=True)
    visitor_id = Column(String(64), index=True)
    ip = Column(String(64))
    country = Column(String(8))
    user_agent = Column(String(300))
    referrer = Column(String(300))
    created_at = Column(DateTime, default=_now, index=True)


Index('ix_refclicks_code_at', ReferralClick.code, ReferralClick.created_at)


def record_referral_click(session, code, visitor_id='', ip='', country='',
                          user_agent='', referrer=''):
    row = ReferralClick(code=(code or '')[:16], visitor_id=(visitor_id or '')[:64],
                        ip=(ip or '')[:64], country=(country or '')[:8],
                        user_agent=(user_agent or '')[:300],
                        referrer=(referrer or '')[:300])
    session.add(row)
    return row


def referral_click_stats(session, code):
    """{clicks, visitors, last} for one referral code."""
    clicks, visitors, last = (session.query(
        func.count(ReferralClick.id),
        func.count(func.distinct(ReferralClick.visitor_id)),
        func.max(ReferralClick.created_at))
        .filter(ReferralClick.code == code).one())
    return {'clicks': int(clicks or 0), 'visitors': int(visitors or 0),
            'last': _epoch(last)}


class ReferralEarning(Base):
    """5% of a referred account's first subscription payment.

    Money, so it is a row of its own rather than a running total on the user:
    the credit against the referrer's own Stripe invoice can fail and be retried
    without ever paying the same referral twice (payment_id is unique)."""
    __tablename__ = 'referral_earnings'

    id = Column(String(32), primary_key=True, default=_uid)
    referrer_id = Column(String(32), ForeignKey('users.id'), nullable=False, index=True)
    referred_user_id = Column(String(32), ForeignKey('users.id'), index=True)
    payment_id = Column(String(32), unique=True, index=True)
    amount_cents = Column(Integer, default=0)
    currency = Column(String(8), default='EUR')
    status = Column(String(16), default='pending')   # pending | credited
    created_at = Column(DateTime, default=_now, index=True)
    credited_at = Column(DateTime)


def add_referral_earning(session, referrer_id, referred_user_id, payment_id,
                         amount_cents, currency='EUR'):
    """The earning, or None when this payment already paid a commission."""
    if session.query(ReferralEarning).filter(
            ReferralEarning.payment_id == payment_id).first():
        return None
    row = ReferralEarning(referrer_id=referrer_id, referred_user_id=referred_user_id,
                          payment_id=payment_id, amount_cents=int(amount_cents),
                          currency=currency)
    session.add(row)
    return row


def list_referral_earnings(session, referrer_id, limit=100):
    return (session.query(ReferralEarning)
            .filter(ReferralEarning.referrer_id == referrer_id)
            .order_by(ReferralEarning.created_at.desc()).limit(limit).all())


def pending_referral_earnings(session, referrer_id):
    return (session.query(ReferralEarning)
            .filter(ReferralEarning.referrer_id == referrer_id,
                    ReferralEarning.status == 'pending').all())


class TrialInvite(Base):
    """A link an admin sends out for a time-boxed trial plan.

    Good for any number of new accounts until it is cancelled or expires — one
    link goes to a whole group. What stops a second week is the trial_at stamp
    on the account that redeems it, not the link."""
    __tablename__ = 'trial_invites'

    id = Column(String(32), primary_key=True, default=_uid)
    code = Column(String(32), unique=True, index=True)
    tier = Column(String(32), default='starter')
    days = Column(Integer, default=7)
    note = Column(String(200), default='')
    created_by = Column(String(32), index=True)
    created_at = Column(DateTime, default=_now, index=True)
    expires_at = Column(DateTime)
    clicks = Column(Integer, default=0)
    revoked_at = Column(DateTime)
    # Superseded by trial_redemptions, which holds every redemption rather than
    # the first one. Kept so links issued before the link became multi-use
    # still show who used them.
    used_by = Column(String(32), index=True)
    used_at = Column(DateTime)


class TrialRedemption(Base):
    """One account taking up one trial link."""
    __tablename__ = 'trial_redemptions'

    id = Column(String(32), primary_key=True, default=_uid)
    code = Column(String(32), index=True)
    invite_id = Column(String(32), index=True)
    user_id = Column(String(32), ForeignKey('users.id'), index=True)
    created_at = Column(DateTime, default=_now, index=True)


def record_trial_redemption(session, invite, user_row):
    row = TrialRedemption(code=invite.code, invite_id=invite.id,
                          user_id=user_row.id)
    session.add(row)
    return row


def trial_redemptions_by_code(session, codes):
    """{code: [{email, at}]} for the admin list, newest first."""
    codes = list(codes or [])
    if not codes:
        return {}
    rows = (session.query(TrialRedemption, User.email)
            .outerjoin(User, User.id == TrialRedemption.user_id)
            .filter(TrialRedemption.code.in_(codes))
            .order_by(TrialRedemption.created_at.desc()).all())
    out = {}
    for r, email in rows:
        out.setdefault(r.code, []).append({'email': email or '(deleted)',
                                           'at': r.created_at})
    return out


def get_trial_invite(session, code):
    return (session.query(TrialInvite)
            .filter(TrialInvite.code == (code or '')).first())


def list_trial_invites(session, limit=200):
    return (session.query(TrialInvite)
            .order_by(TrialInvite.created_at.desc()).limit(limit).all())


def init_db():
    Base.metadata.create_all(engine)
    for table, model in (('users', User), ('saved_personas', SavedPersona),
                         ('payments', Payment), ('invites', Invite),
                         ('demo_events', DemoEvent), ('fan_profiles', FanProfile),
                         ('fan_events', FanEvent),
                         ('funnel_assignments', FunnelAssignment),
                         ('funnel_posteriors', FunnelPosterior),
                         ('fan_rewards', FanReward),
                         ('scheduled_posts', ScheduledPost),
                         ('persona_media', PersonaMedia),
                         ('link_clicks', LinkClick),
                         ('referral_clicks', ReferralClick),
                         ('referral_earnings', ReferralEarning),
                         ('trial_invites', TrialInvite),
                         ('trial_redemptions', TrialRedemption),
                         ('credit_ledger', TokenLedger),
                         ('generation_jobs', GenerationJob),
                         ('model_reference_sets', ModelReferenceSet),
                         ('audio_references', AudioReference),
                         ('video_sources', VideoSource),
                         ('characters', Character),
                         ('character_images', CharacterImage),
                         ('character_versions', CharacterVersion),
                         ('character_views', CharacterView),
                         ('view_references', ViewReference)):
        try:
            _sync_columns(table, model)
        except Exception:
            pass
    try:
        s = SessionLocal()
        try:
            grandfather_existing_users(s)
            backfill_workspaces(s)
            grandfather_onlyfans_users(s)
        finally:
            s.close()
    except Exception:
        pass
    # Existing photos carry their outfit in a column; give each one a link so
    # the vault sees the same layout the outfits already show.
    try:
        s = SessionLocal()
        try:
            if backfill_media_links(s):
                s.commit()
            # Swapped clips were stored under their job's kind, which nothing
            # that renders or sends media recognises: they showed as a blank
            # card. Their bytes were always video.
            if s.query(PersonaMedia).filter_by(kind='swap').update(
                    {'kind': 'video'}, synchronize_session=False):
                s.commit()
        finally:
            s.close()
    except Exception:
        pass


def get_persona_images_row(session, slug):
    return session.get(PersonaImages, slug)


def set_persona_images_row(session, slug, images_json):
    row = session.get(PersonaImages, slug)
    if row is None:
        row = PersonaImages(slug=slug)
        session.add(row)
    row.images_json = images_json
    return row


def get_persona_nsfw_images_row(session, slug):
    return session.get(PersonaNsfwImages, slug)


def set_persona_nsfw_images_row(session, slug, images_json):
    row = session.get(PersonaNsfwImages, slug)
    if row is None:
        row = PersonaNsfwImages(slug=slug)
        session.add(row)
    row.images_json = images_json
    return row


def list_saved_personas(session):
    return session.query(SavedPersona).order_by(SavedPersona.name).all()


def get_saved_persona(session, slug):
    return session.get(SavedPersona, slug)


def upsert_saved_persona(session, slug, name, config_json, prompt, owner_id=None):
    sp = session.get(SavedPersona, slug)
    if sp is None:
        sp = SavedPersona(slug=slug, owner_id=owner_id)
        session.add(sp)
    elif owner_id and not sp.owner_id:
        # First write by a real owner claims a previously unowned persona.
        sp.owner_id = owner_id
    sp.name = name
    sp.config_json = config_json
    sp.prompt = prompt
    return sp


def reassign_persona_owner(session, slug, owner_id):
    """Point a saved persona at a different owner. Returns True when it moved."""
    sp = session.get(SavedPersona, slug)
    if sp is None or (sp.owner_id or '') == (owner_id or ''):
        return False
    sp.owner_id = owner_id
    return True


def first_admin(session):
    """The account house personas belong to. The oldest admin, so the answer
    does not change as more admins are promoted."""
    return (session.query(User).filter(User.role == 'admin')
            .order_by(User.created_at.asc()).first())


def list_saved_personas_for_owner(session, owner_id):
    return (session.query(SavedPersona)
            .filter(SavedPersona.owner_id == owner_id)
            .order_by(SavedPersona.name).all())


def add_visit(session, ip, path, user_agent='', referrer='',
              country=None, country_code=None, region=None, city=None):
    v = Visit(ip=ip, path=path, user_agent=(user_agent or '')[:400],
              referrer=(referrer or '')[:400], country=country,
              country_code=country_code, region=region, city=city)
    session.add(v)
    return v


def list_visits(session, limit=500):
    return session.query(Visit).order_by(Visit.created_at.desc()).limit(limit).all()


def set_visit_geo(session, visit_id, country, country_code, region, city):
    v = session.get(Visit, visit_id)
    if v:
        v.country = country
        v.country_code = country_code
        v.region = region
        v.city = city
    return v


def add_x_event(session, action, ip, persona='', detail='', x_username='', user_agent=''):
    e = XEvent(action=action, ip=ip, persona=persona, detail=(detail or '')[:300],
               x_username=x_username, user_agent=(user_agent or '')[:400])
    session.add(e)
    return e


def list_x_events(session, limit=500):
    return session.query(XEvent).order_by(XEvent.created_at.desc()).limit(limit).all()


def set_x_event_geo(session, event_id, country, country_code, region, city):
    e = session.get(XEvent, event_id)
    if e:
        e.country = country
        e.country_code = country_code
        e.region = region
        e.city = city
    return e


def add_x_message(session, persona, x_user_id, x_username, direction, text):
    m = XMessage(persona=persona, x_user_id=str(x_user_id or ''),
                 x_username=x_username or '', direction=direction,
                 text=(text or '')[:2000])
    session.add(m)
    return m


def list_x_messages(session, persona, x_user_id, limit=500):
    """The most recent `limit` messages of a thread, oldest first. Ordered
    descending before the limit so a thread longer than the window keeps moving
    — ascending would pin it to the first `limit` messages it ever had."""
    rows = (session.query(XMessage)
            .filter(XMessage.persona == persona, XMessage.x_user_id == str(x_user_id))
            .order_by(XMessage.created_at.desc()).limit(limit).all())
    rows.reverse()
    return rows


def list_x_conversations(session, limit=200):
    """Newest message per (persona, fan), most recent thread first."""
    rows = session.query(XMessage).order_by(XMessage.created_at.desc()).limit(3000).all()
    seen, out = {}, []
    for m in rows:
        key = (m.persona, m.x_user_id)
        if key in seen:
            seen[key]['count'] += 1
            continue
        d = {'persona': m.persona, 'x_user_id': m.x_user_id,
             'x_username': m.x_username, 'last': m.text, 'last_dir': m.direction,
             'time': m.created_at, 'count': 1}
        seen[key] = d
        out.append(d)
        if len(out) >= limit:
            break
    return out


def list_conversations(session, persona, prefixes, limit=200):
    """Newest message per fan for one persona, restricted to fan-key prefixes.

    The fan key carries the platform: 'fv:' is Fanvue, 'tg:'/'tgu:' Telegram,
    a bare id is X. An empty string in `prefixes` therefore means "bare ids",
    and is matched by excluding every prefixed key instead of by a LIKE.
    """
    q = session.query(XMessage).filter(XMessage.persona == persona)
    prefixes = tuple(prefixes or ())
    likes = [XMessage.x_user_id.like(p.replace('%', r'\%') + '%') for p in prefixes if p]
    if '' in prefixes:
        # Bare ids: everything that is not one of the known prefixed shapes.
        likes.append(~XMessage.x_user_id.contains(':'))
    if likes:
        q = q.filter(or_(*likes))
    elif prefixes:
        return []

    seen, out = {}, []
    # Newest first, then folded per fan — one pass, so a busy persona does not
    # cost one query per conversation.
    for m in q.order_by(XMessage.created_at.desc()).limit(4000).all():
        d = seen.get(m.x_user_id)
        if d is not None:
            d['count'] += 1
            if m.x_username and not d['x_username']:
                d['x_username'] = m.x_username
            continue
        d = {'persona': m.persona, 'x_user_id': m.x_user_id,
             'x_username': m.x_username or '', 'last': m.text,
             'last_dir': m.direction, 'time': m.created_at, 'count': 1}
        seen[m.x_user_id] = d
        out.append(d)
    return out[:limit]


def record_x_opener(session, persona, x_user_id):
    uid = str(x_user_id or '')
    if not uid:
        return None
    existing = session.query(XOpener).filter_by(persona=persona, x_user_id=uid).first()
    if existing:
        return existing
    o = XOpener(persona=persona, x_user_id=uid)
    session.add(o)
    return o


def list_x_opener_ids(session, persona):
    """All fan ids the persona has ever sent an opener to (never pruned)."""
    rows = session.query(XOpener.x_user_id).filter(
        XOpener.persona == persona).distinct().all()
    return {r[0] for r in rows if r[0]}


def list_x_known_user_ids(session, persona):
    """Distinct fan ids the persona already has any DM with — used to avoid
    opening a fresh chat with someone there's already a conversation with."""
    rows = session.query(XMessage.x_user_id).filter(
        XMessage.persona == persona).distinct().all()
    return {r[0] for r in rows if r[0]}


def prune_x_data(session, days=14):
    """Delete X events and visits older than the retention window. X DMs are also
    pruned, but Fanvue conversation history (x_user_id like 'fv:%') is kept
    permanently so the bot never forgets what a fan already told it."""
    cutoff = _now() - datetime.timedelta(days=days)
    deleted = 0
    deleted += session.query(XMessage).filter(
        XMessage.created_at < cutoff,
        ~XMessage.x_user_id.like('fv:%')).delete(synchronize_session=False)
    for model in (XEvent, Visit):
        deleted += session.query(model).filter(model.created_at < cutoff).delete(
            synchronize_session=False)
    return deleted


def record_ppv_drop(session, persona, fan_uuid, set_id, set_name, tier_index,
                    price_cents, media_uuids, message_uuid=None):
    """Log a paid unlock at the moment it is sent, so the sale has a record even
    if every later signal about it is lost."""
    import json as _json
    d = PpvDrop(persona=persona, fan_uuid=str(fan_uuid or ''), set_id=str(set_id or ''),
                set_name=(set_name or '')[:64], tier_index=int(tier_index or 0),
                price_cents=int(price_cents or 0),
                media_uuids=_json.dumps(list(media_uuids or [])),
                message_uuid=str(message_uuid or '') or None)
    session.add(d)
    return d


def list_ppv_drops(session, persona, fan_uuid=None, limit=200):
    """Drops newest first, for one fan or the whole persona."""
    q = session.query(PpvDrop).filter(PpvDrop.persona == persona)
    if fan_uuid:
        q = q.filter(PpvDrop.fan_uuid == str(fan_uuid))
    return q.order_by(PpvDrop.created_at.desc()).limit(limit).all()


def open_ppv_drops(session, persona, since=None, limit=500):
    """Drops still awaiting payment — what a reconciliation sweep tries to
    settle against Fanvue's own ledger."""
    q = session.query(PpvDrop).filter(PpvDrop.persona == persona,
                                      PpvDrop.paid_at.is_(None))
    if since:
        q = q.filter(PpvDrop.created_at >= since)
    return q.order_by(PpvDrop.created_at.desc()).limit(limit).all()


def mark_ppv_paid(session, drop_id, invoice_id=None, source='webhook', when=None):
    d = session.get(PpvDrop, drop_id)
    if d is None or d.paid_at:
        return d
    d.paid_at = when or _now()
    d.invoice_id = (invoice_id or None)
    d.paid_source = (source or '')[:16]
    return d


def mark_ppv_read(session, drop_id, when=None):
    d = session.get(PpvDrop, drop_id)
    if d is not None and not d.read_at:
        d.read_at = when or _now()
    return d


def ppv_set_stats(session, persona):
    """Per (set, tier): how many went out and how many were bought — what the
    builder shows on each tier pill."""
    rows = (session.query(PpvDrop.set_id, PpvDrop.tier_index,
                          func.count(PpvDrop.id),
                          func.count(PpvDrop.paid_at))
            .filter(PpvDrop.persona == persona)
            .group_by(PpvDrop.set_id, PpvDrop.tier_index).all())
    return {f'{r[0]}:{r[1]}': {'sent': int(r[2] or 0), 'bought': int(r[3] or 0)}
            for r in rows}


def fan_ppv_spend(session, persona, fan_uuid):
    """Total cents this fan has actually paid for unlocks."""
    v = (session.query(func.sum(PpvDrop.price_cents))
         .filter(PpvDrop.persona == persona, PpvDrop.fan_uuid == str(fan_uuid),
                 PpvDrop.paid_at.isnot(None)).scalar())
    return int(v or 0)


def count_x_messages(session, persona, x_user_id, direction=''):
    q = (session.query(XMessage)
         .filter(XMessage.persona == persona, XMessage.x_user_id == str(x_user_id)))
    if direction:
        q = q.filter(XMessage.direction == direction)
    return q.count()


def count_x_messages_by_fan(session, persona, direction=''):
    """Message counts per fan for one persona, in one query — the per-fan count
    is wanted for a whole list at a time, and one query per row does not scale."""
    q = (session.query(XMessage.x_user_id, func.count(XMessage.id))
         .filter(XMessage.persona == persona))
    if direction:
        q = q.filter(XMessage.direction == direction)
    return {row[0]: row[1] for row in q.group_by(XMessage.x_user_id).all()}


def delete_saved_persona(session, slug):
    sp = session.get(SavedPersona, slug)
    if sp:
        session.delete(sp)
    return bool(sp)


def get_or_create_conversation(session, conversation_id, persona, client_id=None):
    conv = None
    if conversation_id:
        conv = session.get(Conversation, conversation_id)
    if conv is None:
        conv = Conversation(persona=persona, client_id=client_id)
        session.add(conv)
        session.flush()
    return conv


def add_message(session, conversation_id, role, content):
    msg = Message(conversation_id=conversation_id, role=role, content=content)
    session.add(msg)
    return msg


def history_as_dicts(conv):
    """Return conversation messages as [{role, content}, ...] for the LLM."""
    return [{'role': m.role, 'content': m.content} for m in conv.messages]
