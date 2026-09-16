#!/bin/sh
# The OnlyFans sign-in browser needs a display to be headful on: headless Chrome
# announces itself in its user agent and its page is never focused, and Cloudflare's
# human check refuses it on both counts. of_connect decides by whether DISPLAY is set,
# so this decides for it.
#
# Xvfb is started directly rather than through xvfb-run, which needs xauth -- a
# package that once took the whole container down with it rather than just the display.
#
# DISPLAY is exported only once the socket is really there. A display that fails to
# come up leaves the app serving normally with a headless sign-in, which is worth much
# more than a container that will not start.
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 &

n=0
while [ $n -lt 20 ]; do
    if [ -e /tmp/.X11-unix/X99 ]; then
        DISPLAY=:99
        export DISPLAY
        break
    fi
    n=$((n + 1))
    sleep 0.25
done

[ -n "$DISPLAY" ] || echo 'no display: the OnlyFans sign-in browser will be headless' >&2

# Threads, not workers: every request this serves is waiting on something else
# -- Gemini, a platform's API, or, for a sign-in window, the browser service one
# hop away. Eight of them is eight frame polls before the ninth request queues,
# and a sign-in window on its own asks several times a second, so the window
# and the site it is hosting were competing for the same handful.
exec gunicorn --bind ":${PORT:-8080}" --workers 1 --threads "${GUNICORN_THREADS:-32}" \
     --timeout "${GUNICORN_TIMEOUT:-0}" "${GUNICORN_TARGET:-app:app}"
