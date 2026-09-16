"""A stand-in for TikTok's Content Posting API, for driving app.py's posting
logic without a token or a network call.

Used by test_tiktok.py. Not imported by the app.
"""
import itertools

_ids = itertools.count(1)


class FakeRest:
    """Records what would have gone out, and answers like TikTok does."""

    def __init__(self, username='lilith', privacy=('PUBLIC_TO_EVERYONE', 'SELF_ONLY')):
        self.username = username
        self.privacy_options = list(privacy)
        self.posted = []
        self.uploaded = []

    def configured(self):
        return True

    def creator_info(self):
        return {'username': self.username, 'nickname': self.username,
                'privacy_options': list(self.privacy_options),
                'max_seconds': 600, 'comment_off': False,
                'duet_off': False, 'stitch_off': False}

    def post_video(self, blob, caption='', direct=False, privacy=''):
        if not blob:
            raise ValueError('that video has no bytes')
        row = {'publish_id': 'pub_%d' % next(_ids),
               'mode': 'direct' if direct else 'inbox',
               'caption': caption, 'bytes': len(blob),
               'privacy': privacy}
        self.posted.append(row)
        self.uploaded.append(blob)
        return row

    def status(self, publish_id):
        return {'status': 'PUBLISH_COMPLETE', 'fail_reason': '', 'post_ids': []}


class FakeAuth:
    """A stand-in for tiktok_oauth, for the refresh rules."""

    class TikTokAuthError(Exception):
        def __init__(self, detail='', fatal=False):
            super().__init__(detail)
            self.detail = detail
            self.fatal = fatal

    def __init__(self, fail=None, fatal=False):
        self.fail = fail
        self.fatal = fatal
        self.calls = 0

    def refresh(self, refresh_token):
        self.calls += 1
        if self.fail:
            raise self.TikTokAuthError(self.fail, fatal=self.fatal)
        # TikTok rotates the refresh token on every refresh; a caller that
        # keeps the old one is connected for exactly one more day.
        return 'access-%d' % self.calls, 'refresh-%d' % self.calls, 86400
