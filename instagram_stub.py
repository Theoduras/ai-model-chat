"""A stand-in for Instagram's REST side, for driving app.py's posting logic
without a real session or network call.

Used by test_instagram.py. Not imported by the app.
"""
import itertools
import time

_ids = itertools.count(1)


class FakeRest:
    """Records what would have gone out, and answers like Instagram does."""

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

    def _record(self, target_kind, media_kind, caption, width=0, height=0, duration_ms=0):
        row = {'id': str(next(_ids)), 'target': target_kind,
               'media_kind': media_kind, 'caption': caption,
               'width': width, 'height': height, 'duration_ms': duration_ms,
               'at': time.time()}
        self.posted.append(row)
        return row

    def post_feed(self, media_bytes, kind, caption='', width=0, height=0, duration_ms=0):
        return self._record('post', kind, caption, width, height, duration_ms)

    def post_story(self, media_bytes, kind, caption='', width=0, height=0, duration_ms=0):
        return self._record('story', kind, caption, width, height, duration_ms)

    def post_reel(self, media_bytes, caption='', width=0, height=0, duration_ms=0):
        return self._record('reel', 'video', caption, width, height, duration_ms)
