"""The OnlyFans session vault.

A connected account is a browser session the creator handed us by signing in:
the `sess` cookie OnlyFans issued, the `x-bc` device token its page put in
localStorage, and the exact user-agent both were issued to. Those three are the
account — anyone holding them can act as the creator — so they are encrypted at
rest and never leave this module in the clear except to the request signer.

Storage is lent by app.py through `store_hooks()`, so this module stays free of
Flask and of the database and can be tested on a dict.
"""
import base64
import json
import logging
import os
import time

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

# The fields that make up an account. Anything else a caller passes is dropped,
# so a stray page object can never be persisted alongside the credentials.
FIELDS = ('user_id', 'username', 'name', 'cookie', 'x_bc', 'user_agent',
          'proxy', 'connected_at', 'status', 'checked_at', 'last_error',
          'verified')

STATUS_LIVE = 'live'
STATUS_EXPIRED = 'expired'
STATUS_PAUSED = 'paused'

_load = _save = _delete = _keys = None
_fernet = None


class SessionError(RuntimeError):
    """The vault could not be read or written."""


def store_hooks(load, save, delete, keys):
    """Lend the vault its storage. `load(account)` returns the ciphertext it was
    given or '', `save(account, blob)` keeps one, `delete(account)` removes it,
    `keys()` lists the accounts held."""
    global _load, _save, _delete, _keys
    _load, _save, _delete, _keys = load, save, delete, keys


def _key():
    """The encryption key: an explicit one if set, else derived from the app
    secret so a deployment that never set one still encrypts."""
    global _fernet
    if _fernet is not None:
        return _fernet
    raw = (os.getenv('ONLYFANS_SESSION_KEY') or '').strip()
    if raw:
        try:
            _fernet = Fernet(raw.encode())
            return _fernet
        except (ValueError, TypeError) as e:
            raise SessionError(f'ONLYFANS_SESSION_KEY is not a valid Fernet key: {e}')
    secret = (os.getenv('SECRET_KEY') or '').strip()
    if not secret:
        raise SessionError(
            'no ONLYFANS_SESSION_KEY and no SECRET_KEY, so OnlyFans sessions '
            'cannot be encrypted. Set one before connecting an account.')
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=b'onlyfans-session',
                   info=b'v1').derive(secret.encode())
    _fernet = Fernet(base64.urlsafe_b64encode(derived))
    return _fernet


def reset_key():
    """Forget the derived key. Only the tests and a key rotation need this."""
    global _fernet
    _fernet = None


def _encrypt(record):
    return _key().encrypt(json.dumps(record).encode()).decode()


def _decrypt(blob):
    try:
        return json.loads(_key().decrypt(blob.encode()).decode())
    except InvalidToken:
        raise SessionError('stored OnlyFans session could not be decrypted — '
                           'the encryption key changed')


def put(account, session):
    """Store or replace an account's session. Returns what was stored, with the
    credentials stripped out, so the caller can log or return it safely."""
    if not account:
        raise SessionError('no account id')
    record = {k: session.get(k) for k in FIELDS if session.get(k) not in (None, '')}
    missing = [k for k in ('cookie', 'user_agent', 'user_id') if not record.get(k)]
    if missing:
        raise SessionError('incomplete OnlyFans session, missing: ' + ', '.join(missing))
    record.setdefault('connected_at', int(time.time()))
    record.setdefault('status', STATUS_LIVE)
    if not _save:
        raise SessionError('no session store configured')
    _save(account, _encrypt(record))
    return public(record, account)


def get(account):
    """The full session, credentials included. Only the signer should call it."""
    if not (account and _load):
        return {}
    blob = _load(account) or ''
    return _decrypt(blob) if blob else {}


def update(account, **fields):
    """Change a few fields without the caller having to hold the credentials."""
    record = get(account)
    if not record:
        return {}
    record.update({k: v for k, v in fields.items() if k in FIELDS})
    _save(account, _encrypt(record))
    return public(record, account)


def mark_expired(account, why=''):
    return update(account, status=STATUS_EXPIRED, last_error=str(why)[:200],
                  checked_at=int(time.time()))


def mark_live(account):
    return update(account, status=STATUS_LIVE, last_error='',
                  checked_at=int(time.time()))


def drop(account):
    if account and _delete:
        _delete(account)


def public(record, account=''):
    """An account as the dashboard may see it: no cookie, no token, no UA."""
    record = record or {}
    return {'account': account, 'user_id': str(record.get('user_id') or ''),
            'username': record.get('username') or '',
            'name': record.get('name') or '',
            'proxy_label': _proxy_label(record.get('proxy') or ''),
            'connected_at': record.get('connected_at'),
            'checked_at': record.get('checked_at'),
            'status': record.get('status') or STATUS_LIVE,
            'last_error': record.get('last_error') or '',
            'verified': bool(record.get('verified'))}


def describe(account):
    return public(get(account), account)


def accounts():
    """Every connected account, dashboard-safe."""
    return [describe(a) for a in (_keys() if _keys else [])]


def live(account):
    return (get(account).get('status') or STATUS_LIVE) == STATUS_LIVE


def _proxy_label(proxy):
    """A proxy URL as somewhere to show a creator — host only, no credentials."""
    if not proxy:
        return ''
    tail = proxy.rsplit('@', 1)[-1]
    return tail.split('://')[-1]


def cookie_string(jar):
    """The cookie header from a browser's cookie list.

    Only the cookies OnlyFans authenticates with are kept: the rest are
    analytics that change constantly and would age the session record for
    nothing.
    """
    wanted = ('sess', 'auth_id', 'auth_uid', 'csrf', 'fp', 'st', 'cookiesAccepted')
    seen = {}
    for c in jar or []:
        name = c.get('name') if isinstance(c, dict) else None
        if name in wanted and c.get('value'):
            seen[name] = c['value']
    return '; '.join(f'{k}={seen[k]}' for k in wanted if k in seen)
