#!/bin/sh
if command -v Xvfb >/dev/null 2>&1; then
    Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 &
fi

n=0
while command -v Xvfb >/dev/null 2>&1 && [ $n -lt 20 ]; do
    if [ -e /tmp/.X11-unix/X99 ]; then
        DISPLAY=:99
        export DISPLAY
        break
    fi
    n=$((n + 1))
    sleep 0.25
done

if [ -z "$DISPLAY" ] && command -v Xvfb >/dev/null 2>&1; then
    echo 'no display: the OnlyFans sign-in browser will be headless' >&2
fi

# Always load wsgi:app (not app:app) so the uploaded-photo undress route is used.
# Ignore GUNICORN_TARGET if the Cloud Run service still has the old value.
exec gunicorn --bind ":${PORT:-8080}" --workers 1 --threads "${GUNICORN_THREADS:-32}" \
     --timeout "${GUNICORN_TIMEOUT:-0}" wsgi:app
