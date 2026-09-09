"""Tests for Threads publishing: media typing, carousels and container waits.

The Threads graph API is stubbed, so this needs no token and no network.
Run with: python test_threads.py
"""
import os
import sys

os.environ.setdefault('GEMINI_API_KEY', 'test')

import app

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ok   {name}')
    else:
        print(f'  FAIL {name} {detail}')
        FAILURES.append(name)


CALLS = []
STATUS = {'value': 'FINISHED', 'error': ''}


def fake_call(persona, method, path, params=None, body=None):
    p = dict(params or {})
    CALLS.append((method, path, p))
    if path.endswith('/threads') and method == 'POST':
        return {'id': f'c{len(CALLS)}'}
    if path.endswith('/threads_publish'):
        return {'id': 'M-' + p.get('creation_id', '')}
    if path.endswith('/threads_publishing_limit'):
        return {'data': [{'quota_usage': 7, 'config': {'quota_total': 250}}]}
    if p.get('fields', '').startswith('status'):
        return {'status': STATUS['value'], 'error_message': STATUS['error']}
    return {}


app._threads_call = fake_call
app._threads_uid = lambda persona: 'UID'
app.time.sleep = lambda s: None


def publish(*a, **kw):
    CALLS.clear()
    return app._threads_publish(*a, **kw)


def containers():
    return [p for m, path, p in CALLS if path.endswith('/threads') and m == 'POST']


def fails(fn):
    try:
        fn()
    except Exception as e:
        return str(e)
    return ''


print('media typing')
check('bare url defaults to image',
      app._threads_media_item('https://x.test/a.jpg') == ('IMAGE', {'image_url': 'https://x.test/a.jpg'}))
check('video by extension',
      app._threads_media_item('https://x.test/a.mp4')[0] == 'VIDEO')
check('extension read past the query string',
      app._threads_media_item('https://x.test/a.mov?sig=abc')[0] == 'VIDEO')
check('explicit type wins over extension',
      app._threads_media_item({'url': 'https://x.test/a.jpg', 'type': 'VIDEO'})[0] == 'VIDEO')
check('video_url key implies video',
      app._threads_media_item({'video_url': 'https://x.test/clip'})[0] == 'VIDEO')
check('alt text carried', app._threads_media_item(
      {'url': 'https://x.test/a.jpg', 'alt_text': 'a cat'})[1]['alt_text'] == 'a cat')
check('http refused', 'https' in fails(lambda: app._threads_media_item('http://x.test/a.jpg')))
check('empty url refused', 'url' in fails(lambda: app._threads_media_item({'alt_text': 'x'})))

print('library items (what the queue sends)')
app._media_public_url = lambda m: (m.get('source_url')
                                   or 'https://site.test/api/personas/%s/media/%s/image'
                                   % (m.get('slug'), m.get('id')))
check('library image resolves to its public url',
      app._threads_media_item({'kind': 'image', 'slug': 'lilith', 'id': 9})
      == ('IMAGE', {'image_url': 'https://site.test/api/personas/lilith/media/9/image'}))
check('library video is a video',
      app._threads_media_item({'kind': 'video', 'slug': 'lilith', 'id': 9})[0] == 'VIDEO')
check('externally hosted library item keeps its url',
      app._threads_media_item({'kind': 'video', 'source_url': 'https://cdn.test/v.mp4'})[1]
      == {'video_url': 'https://cdn.test/v.mp4'})
publish('lilith', 'cap', media={'kind': 'image', 'slug': 'lilith', 'id': 9})
check('a bare library dict posts as one image',
      containers()[0]['media_type'] == 'IMAGE' and 'image_url' in containers()[0])
check('a library item is waited on before publish',
      any(m == 'GET' for m, _, _ in CALLS))

print('text posts')
check('text post publishes', publish('lilith', 'hello') == 'M-c1')
check('text container is TEXT', containers()[0]['media_type'] == 'TEXT')
check('text container is not polled', not any(m == 'GET' for m, _, _ in CALLS))
publish('lilith', 'x' * 900)
check('text truncated to the limit', len(containers()[0]['text']) == app.THREADS_TEXT_LIMIT)
check('empty post refused', 'needs text' in fails(lambda: publish('lilith', '')))

print('single media')
publish('lilith', 'cap', media=['https://x.test/a.jpg'])
c = containers()[0]
check('image container carries url and text',
      c['media_type'] == 'IMAGE' and c['image_url'] == 'https://x.test/a.jpg' and c['text'] == 'cap')
check('media container is polled before publish',
      any(m == 'GET' for m, _, _ in CALLS))
publish('lilith', '', media=['https://x.test/a.mp4'])
check('media may post without text', 'text' not in containers()[0])
publish('lilith', 'c', media=['https://x.test/a.jpg'], alt_text='described')
check('alt_text applies to a single item', containers()[0]['alt_text'] == 'described')

print('carousels')
publish('lilith', 'set', media=['https://x.test/1.jpg', 'https://x.test/2.mp4'])
cs = containers()
check('children then parent', len(cs) == 3)
check('children flagged as carousel items',
      all(c.get('is_carousel_item') == 'true' for c in cs[:2]))
check('children carry no text', not any('text' in c for c in cs[:2]))
check('parent is a carousel', cs[2]['media_type'] == 'CAROUSEL')
check('parent lists its children', cs[2]['children'] == 'c1,c2')
check('parent carries the text', cs[2]['text'] == 'set')
check('mixed types preserved', cs[0]['media_type'] == 'IMAGE' and cs[1]['media_type'] == 'VIDEO')
check('over-long carousel refused', 'at most' in fails(
      lambda: publish('lilith', 'x', media=[f'https://x.test/{i}.jpg' for i in range(21)])))

print('replies')
publish('lilith', 'ty', reply_to_id='R1')
check('reply id on a text reply', containers()[0]['reply_to_id'] == 'R1')
publish('lilith', 'ty', reply_to_id='R1', media=['https://x.test/a.jpg'])
check('reply id on a media reply', containers()[0]['reply_to_id'] == 'R1')
publish('lilith', 'ty', reply_to_id='R1', media=['https://x.test/1.jpg', 'https://x.test/2.jpg'])
check('reply id only on the carousel parent',
      'reply_to_id' not in containers()[0] and containers()[2]['reply_to_id'] == 'R1')

print('container failures')
STATUS['value'], STATUS['error'] = 'ERROR', 'aspect ratio unsupported'
msg = fails(lambda: publish('lilith', 'x', media=['https://x.test/a.jpg']))
check('processing error surfaces the reason', 'aspect ratio unsupported' in msg, msg)
check('nothing is published after a failed container',
      not any(p.endswith('/threads_publish') for _, p, _ in CALLS))
STATUS['value'], STATUS['error'] = 'EXPIRED', ''
check('expired container refused', 'expired' in fails(
      lambda: publish('lilith', 'x', media=['https://x.test/a.jpg'])).lower())
STATUS['value'], STATUS['error'] = 'IN_PROGRESS', ''
app.time.time = (lambda real: (lambda: real() + 10 ** 6))(app.time.time)
check('a stuck container gives up rather than looping', 'processing' in fails(
      lambda: publish('lilith', 'x', media=['https://x.test/a.jpg'])))
STATUS['value'] = 'FINISHED'

print('quota')
CALLS.clear()
check('publishing limit read', app._threads_publishing_limit('lilith') == {'used': 7, 'total': 250})

print('meta callbacks')
import base64 as _b64, hmac as _hmac, hashlib as _hashlib, json as _json, time as _time

_SECRET = 'test-app-secret'
app._set_setting('threads_client_secret', _SECRET)


def _b64u(b):
    return _b64.urlsafe_b64encode(b).decode().rstrip('=')


def signed(user_id, secret=_SECRET, algorithm='HMAC-SHA256'):
    payload = _b64u(_json.dumps({'user_id': user_id, 'algorithm': algorithm,
                                 'issued_at': int(_time.time())}).encode())
    sig = _hmac.new(secret.encode(), payload.encode(), _hashlib.sha256).digest()
    return _b64u(sig) + '.' + payload


def seed():
    app._threads_save_tokens({
        'a': {'access_token': 'A', 'user_id': '555', 'username': 'a'},
        'b': {'access_token': 'B', 'user_id': '555', 'username': 'b'},
        'c': {'access_token': 'C', 'user_id': '999', 'username': 'c'},
    })


client = app.app.test_client()


def call(path, sr):
    return client.post(path, data={'signed_request': sr})


check('meta paths are not behind the sign-in gate',
      not any(app._path_needs_plan(p, 'POST') for p in
              ('/api/threads/webhook', '/api/threads/uninstall', '/api/threads/delete')))

seed()
check('unsigned callback refused', call('/api/threads/uninstall', '').status_code == 400)
check('wrong secret refused',
      call('/api/threads/uninstall', signed('555', secret='nope')).status_code == 400)
check('tampered payload refused',
      call('/api/threads/uninstall',
           signed('555').split('.')[0] + '.' + _b64u(b'{"user_id":"999"}')).status_code == 400)
check('algorithm downgrade refused',
      call('/api/threads/uninstall', signed('555', algorithm='none')).status_code == 400)
check('a refused callback deletes nothing', len(app._threads_load_tokens()) == 3)

check('uninstall accepted', call('/api/threads/uninstall', signed('555')).status_code == 200)
check('uninstall drops every persona on that account',
      sorted(app._threads_load_tokens()) == ['c'])

seed()
res = call('/api/threads/delete', signed('999')).get_json()
check('deletion returns the code and url Meta requires',
      bool(res.get('confirmation_code')) and '/api/threads/deletion-status' in res.get('url', ''))
check('deletion drops only that account', sorted(app._threads_load_tokens()) == ['a', 'b'])
check('the confirmation code resolves',
      client.get('/api/threads/deletion-status?code=' + res['confirmation_code']).status_code == 200)
check('a non-hex code is refused rather than echoed',
      client.get('/api/threads/deletion-status?code=<script>').status_code == 400)

print()
if FAILURES:
    print(f'{len(FAILURES)} FAILED: {FAILURES}')
    raise SystemExit(1)
print('all threads tests passed')
