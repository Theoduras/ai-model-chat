"""A stand-in for TikTok's REST side, for driving app.py's posting and reply
logic without a real session or network call.

Used by test_tiktok.py. Not imported by the app.
"""
import itertools
import time

_ids = itertools.count(1)


class FakeRest:
    """Records what would have gone out, and answers like TikTok does."""

    def __init__(self, user_id='1', username='lilith'):
        self.user_id = str(user_id)
        self.username = username
        self.sec_uid = 'sec-' + self.username
        self.posted = []
        self.replied = []
        self.feed = []
        self.threads = {}

    def configured(self):
        return True

    def held(self):
        return 0

    def me(self):
        return {'user_id': self.user_id, 'username': self.username,
                'sec_uid': self.sec_uid}

    def _record(self, kind, caption, stills=0):
        row = {'item_id': str(next(_ids)), 'kind': kind, 'caption': caption,
               'stills': stills, 'at': time.time()}
        self.posted.append(row)
        return row

    def post_video(self, media_bytes, caption='', schedule_at=0):
        return self._record('video', caption)

    def post_photos(self, images, caption='', schedule_at=0):
        if not images:
            raise ValueError('a photo post needs at least one still')
        return self._record('photo', caption, len(images))

    def posts(self, count=12, cursor=0):
        return self.feed[:count]

    def comments(self, item_id, count=20, cursor=0):
        return (self.threads.get(str(item_id)) or [])[:count]

    def reply(self, item_id, text, reply_id=''):
        row = {'id': str(next(_ids)), 'item_id': str(item_id),
               'reply_id': str(reply_id), 'text': text}
        self.replied.append(row)
        return {'id': row['id'], 'text': text}
