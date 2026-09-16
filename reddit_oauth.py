"""Reddit's official OAuth, which is how this account is connected now.

The Reddit console started out driving a real signed-in browser, the way
Discord and Instagram still do, because Reddit Chat runs on an undocumented
Sendbird deployment the API has never been able to reach. Reddit refused that
browser on every auth path -- correct credentials came back "invalid username
or password", the one-time email link came back `UPEl3D` -- while the page
itself loaded fine on a clean residential IP. Reddit was rejecting the client,
not the account.

Chat is out of scope now, and everything left -- submitting, uploading media,
reading her own posts' comments, reading mentions, replying -- is covered by
the documented API. So this replaces the browser with a registered app: the
operator approves once per persona and Reddit hands back a refresh token that
does not expire. No proxy, no Cloudflare, no session to re-paste.

Register the app at reddit.com/prefs/apps as a *web app* with the redirect URI
pointing at /reddit/oauth/callback, then set the four env vars below on the
service. ENVIRONMENTS.md has the walkthrough.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

AUTHORIZE_URL = 'https://www.reddit.com/api/v1/authorize'
TOKEN_URL = 'https://www.reddit.com/api/v1/access_token'

# submit covers /api/submit and /api/comment; history covers
# /user/{name}/submitted; privatemessages covers /message/mentions and
# /api/read_message. Nothing here can read or send a DM in Reddit Chat -- that
# is the surface the API has never had.
SCOPES = 'identity submit edit read history privatemessages flair'

STATE_TTL = 900
TIMEOUT = 20

# oauth.reddit.com rate-limits a browser-shaped User-Agent far harder than a
# declared one, so this is not cosmetic.
DEFAULT_UA = 'web:ai-model-chat:v1 (by /u/unknown)'


class RedditAuthError(Exception):
    def __init__(self, detail='', fatal=False):
        super().__init__(detail or 'Reddit refused the token')
        self.detail = detail
        # A fatal refusal means the refresh token itself is dead and the
        # persona has to approve again. A network failure is not fatal and
        # must never cost her the stored token.
        self.fatal = fatal


def client_id():
    return (os.getenv('REDDIT_CLIENT_ID') or '').strip()


def client_secret():
    return (os.getenv('REDDIT_CLIENT_SECRET') or '').strip()


def redirect_uri():
    return (os.getenv('REDDIT_REDIRECT_URI') or '').strip()


def user_agent():
    return (os.getenv('REDDIT_APP_UA') or '').strip() or DEFAULT_UA


def configured():
    return bool(client_id() and client_secret() and redirect_uri())


def _secret():
    raw = (os.getenv('SECRET_KEY') or '').strip()
    if not raw:
        raise RedditAuthError('no SECRET_KEY, so the sign-in link cannot be signed')
    return raw.encode()


def sign_state(persona):
    """The `state` Reddit hands back to the callback.

    It is signed because the callback stores an account against whatever
    persona the state names: unsigned, anyone who can reach the callback could
    attach their own Reddit account to someone else's persona.
    """
    stamp = str(int(time.time()))
    body = f'{persona}:{stamp}'
    mac = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f'{body}:{mac}'


def read_state(value):
    """The persona a state names, or '' if it was tampered with or has aged
    out."""
    bits = (value or '').split(':')
    if len(bits) != 3:
        return ''
    persona, stamp, mac = bits
    body = f'{persona}:{stamp}'
    want = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(want, mac):
        return ''
    try:
        if abs(time.time() - int(stamp)) > STATE_TTL:
            return ''
    except ValueError:
        return ''
    return persona


def authorize_url(state):
    """duration=permanent is the whole point: without it Reddit returns an
    access token only, and the connection dies in an hour."""
    if not configured():
        raise RedditAuthError('Reddit app is not configured on this service')
    query = urllib.parse.urlencode({
        'client_id': client_id(),
        'response_type': 'code',
        'state': state,
        'redirect_uri': redirect_uri(),
        'duration': 'permanent',
        'scope': SCOPES,
    })
    return f'{AUTHORIZE_URL}?{query}'


def _token_call(body):
    if not configured():
        raise RedditAuthError('Reddit app is not configured on this service')
    basic = base64.b64encode(f'{client_id()}:{client_secret()}'.encode()).decode()
    req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(body).encode(),
                                 method='POST')
    req.add_header('Authorization', f'Basic {basic}')
    req.add_header('User-Agent', user_agent())
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = (e.read() or b'')[:300].decode('utf-8', 'replace')
        except Exception:
            pass
        raise RedditAuthError(f'{e.code} {detail}'.strip(),
                              fatal=e.code in (400, 401, 403))
    except Exception as e:
        raise RedditAuthError(str(e)[:200], fatal=False)
    if payload.get('error'):
        raise RedditAuthError(str(payload['error'])[:200], fatal=True)
    token = payload.get('access_token') or ''
    if not token:
        raise RedditAuthError('Reddit returned no access token', fatal=True)
    return payload


def exchange(code):
    """Approval code -> (access_token, refresh_token, expires_in)."""
    payload = _token_call({'grant_type': 'authorization_code', 'code': code,
                           'redirect_uri': redirect_uri()})
    return (payload['access_token'], payload.get('refresh_token') or '',
            int(payload.get('expires_in') or 3600))


def refresh(refresh_token):
    """Refresh token -> (access_token, expires_in). The refresh token itself
    does not rotate and does not expire."""
    if not refresh_token:
        raise RedditAuthError('no refresh token stored', fatal=True)
    payload = _token_call({'grant_type': 'refresh_token',
                           'refresh_token': refresh_token})
    return payload['access_token'], int(payload.get('expires_in') or 3600)


def revoke(token, kind='refresh_token'):
    """Best effort: dropping a connection should also tell Reddit, but a
    failure here must not stop the local disconnect."""
    if not (token and configured()):
        return
    try:
        basic = base64.b64encode(f'{client_id()}:{client_secret()}'.encode()).decode()
        req = urllib.request.Request(
            'https://www.reddit.com/api/v1/revoke_token',
            data=urllib.parse.urlencode({'token': token,
                                         'token_type_hint': kind}).encode(),
            method='POST')
        req.add_header('Authorization', f'Basic {basic}')
        req.add_header('User-Agent', user_agent())
        urllib.request.urlopen(req, timeout=TIMEOUT).close()
    except Exception as e:
        logger.info('reddit token revoke failed: %s', e)
