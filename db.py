"""Database layer for conversation persistence.

Uses SQLite by default (local dev) and Postgres in production when DATABASE_URL
is set (e.g. Cloud SQL on Cloud Run). Same code path for both via SQLAlchemy.
"""
import os
import datetime
import uuid

from sqlalchemy import (
    create_engine, Column, String, Text, DateTime, ForeignKey, Index
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
    created_at = Column(DateTime, default=_now)


Index('ix_media_slug_purpose', PersonaMedia.slug, PersonaMedia.purpose)


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
    return session.query(PersonaMedia).filter_by(slug=slug).order_by(PersonaMedia.created_at).all()


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
    name = Column(String(120), default='')
    tier = Column(String(32), default='')          # '' until a payment clears
    status = Column(String(16), default='unpaid')  # unpaid | active | expired
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=_now)
    last_login = Column(DateTime)


class Payment(Base):
    """One row per Oxapay invoice, created at checkout and updated by webhook."""
    __tablename__ = 'payments'

    id = Column(String(32), primary_key=True, default=_uid)
    user_id = Column(String(32), ForeignKey('users.id'), nullable=False, index=True)
    tier = Column(String(32), nullable=False)
    amount = Column(String(32), default='')
    currency = Column(String(16), default='USD')
    order_id = Column(String(64), unique=True, index=True)
    track_id = Column(String(64), index=True)
    status = Column(String(24), default='pending')  # pending | Paying | Paid | expired
    created_at = Column(DateTime, default=_now)
    paid_at = Column(DateTime)


Index('ix_payments_user_created', Payment.user_id, Payment.created_at)


def get_user_by_email(session, email):
    return session.query(User).filter(
        User.email == (email or '').strip().lower()).first()


def create_user(session, email, password_hash, name=''):
    u = User(email=(email or '').strip().lower(),
             password_hash=password_hash, name=name or '')
    session.add(u)
    session.flush()
    return u


def get_payment_by_order(session, order_id):
    return session.query(Payment).filter(Payment.order_id == order_id).first()


def init_db():
    Base.metadata.create_all(engine)


def get_persona_images_row(session, slug):
    return session.get(PersonaImages, slug)


def set_persona_images_row(session, slug, images_json):
    row = session.get(PersonaImages, slug)
    if row is None:
        row = PersonaImages(slug=slug)
        session.add(row)
    row.images_json = images_json
    return row


def list_saved_personas(session):
    return session.query(SavedPersona).order_by(SavedPersona.name).all()


def get_saved_persona(session, slug):
    return session.get(SavedPersona, slug)


def upsert_saved_persona(session, slug, name, config_json, prompt):
    sp = session.get(SavedPersona, slug)
    if sp is None:
        sp = SavedPersona(slug=slug)
        session.add(sp)
    sp.name = name
    sp.config_json = config_json
    sp.prompt = prompt
    return sp


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
    return (session.query(XMessage)
            .filter(XMessage.persona == persona, XMessage.x_user_id == str(x_user_id))
            .order_by(XMessage.created_at).limit(limit).all())


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
