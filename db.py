"""Database layer for conversation persistence.

Uses SQLite by default (local dev) and Postgres in production when DATABASE_URL
is set (e.g. Cloud SQL on Cloud Run). Same code path for both via SQLAlchemy.
"""
import os
import datetime
import uuid

from sqlalchemy import (
    create_engine, Column, String, Text, DateTime, ForeignKey, Index, Integer,
    func
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
    image_data = Column(Text, nullable=False)
    location = Column(String(120), default='')
    outfit = Column(String(120), default='')
    lighting = Column(String(60), default='')
    purpose = Column(String(60), default='')
    position = Column(Integer, default=0)   # manual sort order within an outfit
    created_at = Column(DateTime, default=_now)


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
    # NULL means this account is its own workspace. Set means it is a seat in
    # that owner's workspace: personas, plan and billing all come from there.
    team_owner_id = Column(String(32), index=True)
    tier = Column(String(32), default='')          # '' until a plan is chosen
    status = Column(String(16), default='unpaid')  # unpaid | active | expired
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=_now)
    last_login = Column(DateTime)

    # Stripe recurring billing. The customer id outlives any one subscription,
    # so it is kept even after a cancellation to reuse the saved card and to
    # open the billing portal. subscription_id is cleared when Stripe reports
    # the subscription gone; expires_at still runs to the end of the paid period.
    stripe_customer_id = Column(String(64), index=True)
    stripe_subscription_id = Column(String(64), index=True)

    # Creator profile, filled in after signup.
    brand = Column(String(120), default='')
    country = Column(String(80), default='')
    timezone = Column(String(64), default='')
    phone = Column(String(40), default='')
    website = Column(String(255), default='')
    bio = Column(Text, default='')
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
    provider = Column(String(16), default='oxapay')  # oxapay | stripe
    amount = Column(String(32), default='')
    currency = Column(String(16), default='USD')
    order_id = Column(String(64), unique=True, index=True)
    # Stripe checkout session ids run past 64 characters.
    track_id = Column(String(128), index=True)
    status = Column(String(24), default='pending')  # pending | Paying | Paid | expired
    created_at = Column(DateTime, default=_now)
    paid_at = Column(DateTime)


Index('ix_payments_user_created', Payment.user_id, Payment.created_at)


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


def init_db():
    Base.metadata.create_all(engine)
    for table, model in (('users', User), ('saved_personas', SavedPersona),
                         ('payments', Payment)):
        try:
            _sync_columns(table, model)
        except Exception:
            pass
    # Existing photos carry their outfit in a column; give each one a link so
    # the vault sees the same layout the outfits already show.
    try:
        s = SessionLocal()
        try:
            if backfill_media_links(s):
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


def count_x_messages(session, persona, x_user_id):
    return (session.query(XMessage)
            .filter(XMessage.persona == persona, XMessage.x_user_id == str(x_user_id))
            .count())


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
