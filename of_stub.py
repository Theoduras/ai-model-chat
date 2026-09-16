"""A stand-in for OnlyFans, for driving the transport without the site.

It checks our arithmetic rather than trusting it: every request has its
signature recomputed from the loaded rules, the path exactly as sent and the
stamp the header carried, and a mismatch is refused the way OnlyFans refuses
one. The account id comes off the `auth_id` cookie, because the current rules
retire the `user-id` header and OnlyFans reads it there too.

Used by test_of_local.py. Not imported by the app.
"""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import of_rules


class Inbox:
    """What the stub is holding: the chats it serves and what was sent to it."""

    def __init__(self, me=4242, username='lilith_x'):
        self.me = me
        self.username = username
        self.chats = {}
        self.sent = []
        self.seen = []
        self.refused = []
        self.vault = []

    # Old by default: a watcher's first pass is meant to record a backlog
    # rather than answer it, and a fixture stamped "now" cannot show that.
    OLD = '2020-01-01T09:00:00+00:00'

    def chat(self, fan_id, handle, text, mid=1, mine=False, at=OLD,
             unread=1, **extra):
        """Put one chat in the inbox, or move it on with a newer message."""
        message = {'id': mid, 'text': text, 'createdAt': at, 'isSentByMe': mine,
                   'fromUser': {'id': self.me if mine else fan_id,
                                'username': handle}}
        message.update(extra)
        held = self.chats.setdefault(str(fan_id), {
            'id': str(fan_id),
            'withUser': {'id': fan_id, 'username': handle, 'name': handle},
            'messages': []})
        held['messages'].append(message)
        held['unreadMessagesCount'] = unread
        held['lastMessage'] = dict(message)
        return message

    def texts(self):
        return [m.get('text') for m in self.sent]


def _handler(inbox):
    class Stub(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *a):
            pass

        def _signed(self):
            head = {k.lower(): v for k, v in self.headers.items()}
            inbox.seen.append((self.command, self.path))
            if not (head.get('sign') and head.get('time')):
                inbox.refused.append((self.path, 'unsigned'))
                return False
            cookie = head.get('cookie') or ''
            user_id = (re.search(r'auth_id=(\d+)', cookie) or [None, '0'])[1]
            try:
                want, _ = of_rules.sign(self.path, user_id, when=int(head['time']))
            except Exception:
                inbox.refused.append((self.path, 'unsignable'))
                return False
            if want != head['sign']:
                inbox.refused.append((self.path, head['sign']))
                return False
            return True

        def _send(self, body, code=200):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('content-type', 'application/json')
            self.send_header('content-length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _refuse(self):
            # What OnlyFans says to a signature it does not accept, which is
            # what the client reads to tell a rotation from a dead session.
            self._send({'error': {'code': 401,
                                  'message': 'Please refresh the page'}}, 401)

        def _page(self, rows):
            offset = int((re.search(r'offset=(\d+)', self.path) or [0, 0])[1])
            return {'list': rows if not offset else [], 'hasMore': False}

        def do_GET(self):
            if not self._signed():
                return self._refuse()
            path = self.path.split('?')[0]
            if path == '/api2/v2/users/me':
                return self._send({'id': inbox.me, 'username': inbox.username,
                                   'name': inbox.username})
            if path == '/api2/v2/chats':
                return self._send(self._page(
                    [{k: v for k, v in c.items() if k != 'messages'}
                     for c in inbox.chats.values()]))
            held = re.match(r'/api2/v2/chats/([^/]+)/messages$', path)
            if held:
                chat = inbox.chats.get(held.group(1)) or {'messages': []}
                newest = sorted(chat['messages'], key=lambda m: m['id'], reverse=True)
                return self._send(self._page(newest))
            if path.startswith('/api2/v2/vault'):
                return self._send(self._page(inbox.vault))
            return self._send({'error': {'code': 404, 'message': 'no such path'}}, 404)

        def do_POST(self):
            if not self._signed():
                return self._refuse()
            length = int(self.headers.get('content-length') or 0)
            try:
                body = json.loads(self.rfile.read(length) or b'{}')
            except ValueError:
                body = {}
            path = self.path.split('?')[0]
            sending = re.match(r'/api2/v2/chats/([^/]+)/messages$', path)
            if sending:
                body['chat'] = sending.group(1)
                inbox.sent.append(body)
                return self._send({'id': 900 + len(inbox.sent),
                                   'text': body.get('text'),
                                   'price': body.get('price', 0)})
            return self._send({'success': True})

    return Stub


def start(inbox=None):
    """Serve until the process ends. Returns (inbox, base_url)."""
    inbox = inbox or Inbox()
    server = ThreadingHTTPServer(('127.0.0.1', 0), _handler(inbox))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return inbox, 'http://127.0.0.1:%d' % server.server_port
