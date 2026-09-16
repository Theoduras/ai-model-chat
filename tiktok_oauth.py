"""TikTok's official OAuth, which is how this account is connected now.

The TikTok console started out driving a real signed-in browser, the way
Discord and Instagram still do. Two things killed that: every tiktok.com web
call carries a signature its own JavaScript computes over the query string and
the user agent, which a captured session cannot carry, so calls came back as
empty 200s; and the sign-in itself burned the account's SMS quota before it
ever got in. Posting is all that is wanted here, and posting is the one thing
TikTok does document -- so this is a registered app, the same answer Reddit
ended up with.

There is one wrinkle, and it decides the shape of everything downstream.
Publishing straight to her profile (Direct Post, scope `video.publish`) needs
TikTok to audit the app first; until that passes, every post it makes is
SELF_ONLY -- visible to nobody but her -- and capped at five accounts a day.
Uploading to her inbox (scope `video.upload`) has no audit: the finished video
lands in her TikTok drafts and she taps publish in the app, picking the privacy
herself. So inbox is the default and `TIKTOK_DIRECT_POST` is the switch to
throw the day the audit clears.

Register the app at developers.tiktok.com with Login Kit and the Content
Posting API, redirect URI pointing at /tiktok/oauth/callback, then set the env
vars below on the service. ENVIRONMENTS.md has the walkthrough.
"""
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

AUTHORIZE_URL = 'https://www.tiktok.com/v2/auth/authorize/'
TOKEN_URL = 'https://open.tiktokapis.com/v2/oauth/token/'
REVOKE_URL = 'https://open.tiktokapis.com/v2/oauth/revoke/'

# user.info.basic is what lets the console say who she is. video.upload is the
# inbox path and needs no audit; video.publish is Direct Post and is only worth
# asking for once TikTok has audited the app, because an unapproved app holding
# it still cannot post publicly.
SCOPE_BASE = 'user.info.basic,video.upload'
SCOPE_DIRECT = 'user.info.basic,video.upload,video.publish'

STATE_TTL = 900
TIMEOUT = 20


class TikTokAuthError(Exception):
    def __init__(self, detail='', fatal=False):
        super().__init__(detail or 'TikTok refused the token')
        self.detail = detail
        # A fatal refusal means the refresh token itself is dead and the
        # persona has to approve again. A network failure is not fatal and
        # must never cost her the stored token.
        self.fatal = fatal


def client_key():
    return (os.getenv('TIKTOK_CLIENT_KEY') or '').strip()


def client_secret():
    return (os.getenv('TIKTOK_CLIENT_SECRET') or '').strip()


def redirect_uri():
    return (os.getenv('TIKTOK_REDIRECT_URI') or '').strip()


def direct_post():
    """Whether this deployment's app has been audited for Direct Post."""
    return (os.getenv('TIKTOK_DIRECT_POST') or '').strip().lower() in ('1', 'true', 'yes')


def scopes():
    return SCOPE_DIRECT if direct_post() else SCOPE_BASE


def configured():
    return bool(client_key() and client_secret() and redirect_uri())


def _secret():
    raw = (os.getenv('SECRET_KEY') or '').strip()
    if not raw:
        raise TikTokAuthError('no SECRET_KEY, so the sign-in link cannot be signed')
    return raw.encode()


def sign_state(persona):
    """The `state` TikTok hands back to the callback.

    Signed because the callback stores an account against whatever persona the
    state names: unsigned, anyone who can reach the callback could attach their
    own TikTok account to someone else's persona.
    """
    stamp = str(int(time.time()))
    body = f'{persona}:{stamp}'
    mac = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f'{body}:{mac}'


def read_state(value):
    """The persona a state names, or '' if it was tampered with or aged out."""
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
    if not configured():
        raise TikTokAuthError('TikTok app is not configured on this service')
    query = urllib.parse.urlencode({
        'client_key': client_key(),
        'response_type': 'code',
        'scope': scopes(),
        'redirect_uri': redirect_uri(),
        'state': state,
    })
    return f'{AUTHORIZE_URL}?{query}'


def _token_call(body):
    if not configured():
        raise TikTokAuthError('TikTok app is not configured on this service')
    body = dict(body, client_key=client_key(), client_secret=client_secret())
    req = urllib.request.Request(TOKEN_URL,
                                 data=urllib.parse.urlencode(body).encode(),
                                 method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    req.add_header('Cache-Control', 'no-cache')
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = (e.read() or b'')[:300].decode('utf-8', 'replace')
        except Exception:
            pass
        raise TikTokAuthError(f'{e.code} {detail}'.strip(),
                              fatal=e.code in (400, 401, 403))
    except Exception as e:
        raise TikTokAuthError(str(e)[:200], fatal=False)
    # TikTok answers 200 with an error body rather than a status line, so the
    # payload decides -- and an error here is about the grant, not the network.
    if payload.get('error') and payload.get('error') != 'ok':
        detail = f"{payload.get('error')}: {payload.get('error_description') or ''}"
        raise TikTokAuthError(detail[:200], fatal=True)
    if not payload.get('access_token'):
        raise TikTokAuthError('TikTok returned no access token', fatal=True)
    return payload


def exchange(code):
    """Approval code -> (access_token, refresh_token, expires_in, open_id)."""
    payload = _token_call({'grant_type': 'authorization_code',
                           'code': urllib.parse.unquote(code or ''),
                           'redirect_uri': redirect_uri()})
    return (payload['access_token'], payload.get('refresh_token') or '',
            int(payload.get('expires_in') or 86400),
            str(payload.get('open_id') or ''))


def refresh(refresh_token):
    """Refresh token -> (access_token, refresh_token, expires_in).

    TikTok rotates the refresh token on every refresh and the old one stops
    working, so the caller has to store what comes back -- unlike Reddit, where
    the same refresh token is good forever.
    """
    if not refresh_token:
        raise TikTokAuthError('no refresh token stored', fatal=True)
    payload = _token_call({'grant_type': 'refresh_token',
                           'refresh_token': refresh_token})
    return (payload['access_token'],
            payload.get('refresh_token') or refresh_token,
            int(payload.get('expires_in') or 86400))


def revoke(token):
    """Best effort: dropping a connection should tell TikTok too, but a failure
    here must not stop the local disconnect."""
    if not (token and configured()):
        return
    try:
        req = urllib.request.Request(
            REVOKE_URL,
            data=urllib.parse.urlencode({'client_key': client_key(),
                                         'client_secret': client_secret(),
                                         'token': token}).encode(),
            method='POST')
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        urllib.request.urlopen(req, timeout=TIMEOUT).close()
    except Exception as e:
        logger.info('tiktok token revoke failed: %s', e)
