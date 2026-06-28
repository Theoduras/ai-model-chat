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
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///' + os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'data.db'))

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


def init_db():
    Base.metadata.create_all(engine)


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
