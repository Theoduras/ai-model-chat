"""Stand-ins for Reddit's REST and chat sides, for driving app.py's posting,
comment and DM logic without a real session or network call.

Used by test_reddit.py. Not imported by the app.
"""
import itertools
import time

_ids = itertools.count(1)


class FakeRest:
    """Records what would have gone out, and answers like Reddit does."""

    def __init__(self, username='lilith', user_id='t2_abc'):
        self.username = username
        self.user_id = user_id
        self.posted = []
        self.comments = []
        self.read = []
        self.inbox = []
        self.threads = {}
        self.flair_rows = [{'id': 'f1', 'text': 'Verified', 'editable': False}]

    def configured(self):
        return True

    def held(self):
        return 0

    def me(self):
        return {'name': self.username, 'id': self.user_id, 'modhash': 'm'}

    def _submit(self, sub, title, kind, flair, nsfw, **extra):
        row = {'id': str(next(_ids)), 'sub': sub, 'title': title, 'kind': kind,
               'flair': flair, 'nsfw': nsfw, 'at': time.time()}
        row.update(extra)
        self.posted.append(row)
        return {'json': {'errors': [], 'data': {'name': 't3_' + row['id']}}}

    def submit_text(self, sub, title, body='', flair_id='', nsfw=True):
        return self._submit(sub, title, 'text', flair_id, nsfw, body=body)

    def submit_link(self, sub, title, url, flair_id='', nsfw=True):
        return self._submit(sub, title, 'link', flair_id, nsfw, url=url)

    def submit_image(self, sub, title, blob, mime='image/jpeg', flair_id='', nsfw=True):
        return self._submit(sub, title, 'image', flair_id, nsfw, bytes=len(blob or b''))

    def submit_video(self, sub, title, blob, mime='video/mp4', cover=None,
                     cover_mime='image/jpeg', flair_id='', nsfw=True):
        return self._submit(sub, title, 'video', flair_id, nsfw,
                            bytes=len(blob or b''), cover=bool(cover))

    def flairs(self, sub):
        return list(self.flair_rows)

    def comment(self, thing_id, text):
        self.comments.append({'on': thing_id, 'text': text})
        return {'json': {'errors': []}}

    def my_posts(self, username='', limit=25):
        return [{'id': pid, 'subreddit': sub} for pid, sub in self.threads]

    def post_comments(self, article_id, limit=100):
        for (pid, sub), rows in self.threads.items():
            if pid == str(article_id):
                return list(rows)
        return []

    def inbox_mentions(self, limit=25):
        return list(self.inbox)

    def mark_read(self, names):
        self.read.extend(names or [])


class FakeChat:
    """The chat gateway's module surface, with the socket replaced by a list."""

    def __init__(self):
        self.sent = []
        self.rooms = {}
        self.typed = []

    def send(self, persona, fan_id, text):
        self.sent.append({'persona': persona, 'fan': str(fan_id), 'text': text})
        return {'message_id': str(next(_ids))}

    def typing(self, persona, fan_id):
        self.typed.append(str(fan_id))

    def chats(self, persona):
        return list(self.rooms.get(persona, []))

    def messages(self, persona, fan_id, want=20):
        return []

    def history(self, persona, fan_id, want=200):
        return []

    def runner(self, persona):
        return None

    def me(self, persona):
        return 'me'
