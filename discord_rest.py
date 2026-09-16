"""Discord REST transport for a user account.

Everything here speaks to discord.com as the desktop client does, not as a bot:
the token goes out bare (a `Bot ` prefix is what marks an application), and every
request carries the same `X-Super-Properties` blob the gateway identified with.
Those two agreeing is the whole point of this module — a client whose REST calls
describe a different browser than its socket did is the loudest thing an
automated account can do.

Nothing here touches the database or the persona layer; app.py binds it to a
platform adapter, the same way onlyfans.py is bound.
"""
import base64
import json
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

DISCORD_API = os.getenv('DISCORD_API_BASE', 'https://discord.com/api/v10')
DISCORD_TIMEOUT = 20
# What the desktop client reports. Held as one set so the gateway can identify
# with exactly what the REST calls claim; see properties().
DEFAULT_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
DEFAULT_BUILD = int(os.getenv('DISCORD_BUILD', '9999999'))
# The capabilities bitfield the web client sends. It changes with client
# releases and a stale one is refused at IDENTIFY, so it is overridable without
# a deploy rather than baked in.
DEFAULT_CAPABILITIES = int(os.getenv('DISCORD_CAPABILITIES', '16381'))


class DiscordApiError(RuntimeError):
    """`code` is the HTTP status and `detail` what Discord said, so a caller can
    tell a dead token (401) from a fan who has closed their DMs (403)."""

    def __init__(self, code, detail=''):
        self.code = code
        self.detail = detail
        super().__init__(f'Discord API {code}: {detail}'.strip())


def properties(ua=DEFAULT_UA, build=DEFAULT_BUILD, locale='en-US'):
    """The client description. One dict feeds both the IDENTIFY payload and the
    X-Super-Properties header, because they have to say the same thing."""
    return {
        'os': 'Windows', 'browser': 'Chrome', 'device': '',
        'system_locale': locale, 'browser_user_agent': ua,
        'browser_version': ua.split('Chrome/')[-1].split(' ')[0] if 'Chrome/' in ua else '131.0.0.0',
        'os_version': '10', 'referrer': '', 'referring_domain': '',
        'referrer_current': '', 'referring_domain_current': '',
        'release_channel': 'stable', 'client_build_number': int(build),
        'client_event_source': None,
    }


def super_properties(props):
    return base64.b64encode(json.dumps(props, separators=(',', ':')).encode()).decode()


class Rest:
    """One account's REST side. Holds the token, the fingerprint, and the rate
    limit state, so a persona that has been told to slow down slows down
    everywhere rather than per call site."""

    def __init__(self, token, props=None, base=DISCORD_API):
        self.token = token or ''
        self.props = props or properties()
        self.base = base.rstrip('/')
        self._lock = threading.Lock()
        self._buckets = {}      # route key -> epoch it is free again
        self._global_until = 0.0

    def configured(self):
        return bool(self.token)

    def held(self):
        """Seconds until this account may call again, or 0. The console reads it
        so a stood-down account explains itself rather than looking broken."""
        left = self._global_until - time.time()
        return int(left) + 1 if left > 0 else 0

    def _headers(self):
        return {
            'Authorization': self.token,
            'Content-Type': 'application/json',
            'User-Agent': self.props.get('browser_user_agent') or DEFAULT_UA,
            'X-Super-Properties': super_properties(self.props),
            'X-Discord-Locale': self.props.get('system_locale') or 'en-US',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Origin': 'https://discord.com',
            'Referer': 'https://discord.com/channels/@me',
        }

    def _wait(self, bucket):
        while True:
            with self._lock:
                until = max(self._global_until, self._buckets.get(bucket, 0))
                left = until - time.time()
                if left <= 0:
                    return
            time.sleep(min(left, 5.0))

    def _note_limits(self, bucket, headers):
        try:
            remaining = headers.get('X-RateLimit-Remaining')
            reset_after = headers.get('X-RateLimit-Reset-After')
            if remaining is not None and int(float(remaining)) <= 0 and reset_after:
                with self._lock:
                    self._buckets[bucket] = time.time() + float(reset_after)
        except (TypeError, ValueError):
            pass

    def call(self, method, path, body=None, retries=1):
        """One request. 429s are honoured rather than retried at, and a global
        429 stands the whole account down — hammering after one is how an
        account gets flagged, not just throttled."""
        if not self.token:
            raise DiscordApiError(0, 'No Discord token for this persona')
        bucket = f'{method}:{path.split("?")[0].rsplit("/", 1)[0]}'
        self._wait(bucket)
        url = f'{self.base}/{path.lstrip("/")}'
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method.upper())
        for key, value in self._headers().items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=DISCORD_TIMEOUT) as resp:
                self._note_limits(bucket, resp.headers)
                raw = resp.read()
                if not raw:
                    return {}
                try:
                    return json.loads(raw)
                except ValueError:
                    return {}
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = (e.read() or b'')[:400].decode('utf-8', 'replace')
            except Exception:
                pass
            if e.code == 429:
                payload = {}
                try:
                    payload = json.loads(detail or '{}')
                except ValueError:
                    pass
                after = float(payload.get('retry_after') or
                              e.headers.get('Retry-After') or 5)
                with self._lock:
                    if payload.get('global') or e.headers.get('X-RateLimit-Global'):
                        self._global_until = time.time() + after
                    else:
                        self._buckets[bucket] = time.time() + after
                if retries > 0:
                    self._wait(bucket)
                    return self.call(method, path, body, retries - 1)
            # A refused token or a closed DM is a standing answer, never a retry.
            raise DiscordApiError(e.code, detail)
        except urllib.error.URLError as e:
            raise DiscordApiError(0, str(getattr(e, 'reason', e))[:200])

    # ── The calls the engine actually makes ──────────────────────────────────

    def me(self):
        return self.call('GET', 'users/@me')

    def send(self, channel_id, content, reply_to=''):
        body = {'content': content, 'nonce': str(random.getrandbits(63)),
                'tts': False}
        if reply_to:
            body['message_reference'] = {'message_id': str(reply_to)}
        return self.call('POST', f'channels/{channel_id}/messages', body)

    def typing(self, channel_id):
        return self.call('POST', f'channels/{channel_id}/typing')

    def react(self, channel_id, message_id, emoji):
        emoji = urllib.parse.quote(emoji, safe='')
        return self.call(
            'PUT', f'channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me')

    def history(self, channel_id, limit=20):
        rows = self.call('GET', f'channels/{channel_id}/messages?limit={int(limit)}')
        return list(reversed(rows)) if isinstance(rows, list) else []

    def private_channels(self):
        rows = self.call('GET', 'users/@me/channels')
        return rows if isinstance(rows, list) else []

    def accept_friend(self, user_id):
        return self.call('PUT', f'users/@me/relationships/{user_id}', body={})


# ── Accepting a message request ──────────────────────────────────────────────
#
# Discord has never documented this endpoint and it has moved more than once.
# Rather than guess, the first real request is used to find out which spelling
# this account's API answers, the winner is remembered, and it is never probed
# again — a burst of 404s against private endpoints is itself a signal.
#
# None of this gates a reply: sending into the channel clears the request on
# Discord's side anyway, so acceptance here is an upgrade, not a precondition.
ACCEPT_ROUTES = (
    ('PUT', 'channels/{channel}/recipients/@me'),
    ('POST', 'users/@me/message-requests/{user}'),
    ('PUT', 'users/@me/message-requests/{user}'),
)
IMPLICIT = 'implicit'


def accept_request(rest, channel_id, user_id, known=''):
    """Try to accept a DM request. Returns the route that worked — pass it back
    as `known` next time — or IMPLICIT when only replying will do it."""
    routes = ACCEPT_ROUTES
    if known and known != IMPLICIT:
        method, path = known.split(' ', 1)
        routes = ((method, path),)
    for method, template in routes:
        path = template.format(channel=channel_id, user=user_id)
        try:
            rest.call(method, path, body={} if method == 'POST' else None, retries=0)
            return f'{method} {template}'
        except DiscordApiError as e:
            if e.code in (401, 403):
                return IMPLICIT
            time.sleep(1.0)
        except Exception:
            break
    return IMPLICIT
