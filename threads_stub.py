"""A stand-in for the Threads REST side, for driving app.py's posting logic
without an Instagram session or a network call.

Used by test_threads.py. Not imported by the app.
"""
import itertools
import time

_ids = itertools.count(1)


class FakeRest:
    """Records what would have gone out, and answers like Threads does."""

    def __init__(self, user_id='1', username='lilith'):
        self.user_id = str(user_id)
        self.username = username
        self.posted = []

    def configured(self):
        return True

    def held(self):
        return 0

    def me(self):
        return {'pk': self.user_id, 'username': self.username}

    def _record(self, kind, caption, count=1, **extra):
        row = {'media': {'pk': str(next(_ids))}, 'kind': kind, 'caption': caption,
               'count': count, 'at': time.time()}
        row.update(extra)
        self.posted.append(row)
        return row

    def post_text(self, caption, reply_control='everyone', reply_to_id=''):
        return self._record('text', caption, reply_to_id=reply_to_id)

    def post_image(self, media_bytes, caption='', width=0, height=0,
                   reply_control='everyone'):
        return self._record('image', caption)

    def post_video(self, media_bytes, caption='', width=0, height=0,
                   duration_ms=0, reply_control='everyone'):
        return self._record('video', caption, duration_ms=duration_ms)

    def post_carousel(self, items, caption='', reply_control='everyone'):
        return self._record('carousel', caption, count=len(items))
