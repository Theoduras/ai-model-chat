"""What the OnlyFans plumbing is doing, kept where the console can read it.

Everything underneath the console -- signing, the browser, the session vault,
the watcher -- reports through `logging`, which on Cloud Run means the only way
to see why something failed is to open the log viewer with the right filter at
the right minute. That is the slowest possible loop for the one part of this
app that breaks most often.

So the same records are also kept here, in a small ring in memory, and served
to the console. In memory on purpose: these are diagnostics for a live
instance, they are worthless once it is replaced, and writing a line per poll
to the database would cost more than it tells anyone.
"""
import hashlib
import logging
import os
import threading
import time
from collections import deque

MAX = 400
# The modules worth watching. app.py is deliberately absent: its own activity
# already has the durable per-persona log.
SOURCES = ('of_client', 'of_rules', 'of_connect', 'of_events', 'of_session',
           'of_browser')

_lines = deque(maxlen=MAX)
_lock = threading.Lock()
_seq = 0


class _Handler(logging.Handler):
    def emit(self, record):
        global _seq
        try:
            text = record.getMessage()
        except Exception:
            return
        with _lock:
            _seq += 1
            _lines.append({'id': _seq, 'at': int(record.created),
                           'level': record.levelname.lower(),
                           'where': record.name.replace('of_', ''),
                           'text': text[:400]})


_handler = _Handler()


def install(level=logging.INFO):
    _handler.setLevel(level)
    for name in SOURCES:
        log = logging.getLogger(name)
        if _handler not in log.handlers:
            log.addHandler(_handler)
        if not log.isEnabledFor(level):
            log.setLevel(level)


def note(where, text, level='info'):
    """A line from somewhere that does not log, or that logs too quietly."""
    global _seq
    with _lock:
        _seq += 1
        _lines.append({'id': _seq, 'at': int(time.time()),
                       'level': level, 'where': where, 'text': str(text)[:400]})


def recent(after=0, limit=200):
    with _lock:
        rows = [r for r in _lines if r['id'] > after]
    return rows[-limit:]


def clear():
    with _lock:
        _lines.clear()


_build = ['']


def build_id():
    """A fingerprint of the Python this process is running.

    The app and the browser service run the same image from the same repo, so
    the same code gives the same answer. They differ only when one of them was
    not redeployed -- which is the failure the console could not see, and which
    no per-feature flag can catch for a feature that did not exist yet.
    """
    if _build[0]:
        return _build[0]
    here = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha256()
    try:
        for name in sorted(os.listdir(here)):
            if not name.endswith('.py'):
                continue
            with open(os.path.join(here, name), 'rb') as fh:
                h.update(name.encode())
                h.update(fh.read())
    except Exception:
        return 'unknown'
    _build[0] = h.hexdigest()[:10]
    return _build[0]
