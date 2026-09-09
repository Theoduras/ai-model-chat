FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The OnlyFans connect flow runs a real browser the creator signs in through,
# and it has to be Google Chrome rather than the Chromium Playwright downloads.
# OnlyFans' human check reads what the open-source build cannot hide: no H.264,
# a user agent that says HeadlessChrome, and no client hints at all.
#
# Chrome's own package declares the libraries it needs, so apt resolves them —
# there is no hand-kept list here to fall out of date.
RUN apt-get update && apt-get install -y --no-install-recommends \
      wget xvfb fonts-liberation \
    && wget -q -O /tmp/chrome.deb \
      https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm /tmp/chrome.deb \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY . .

EXPOSE 8080

# Cloud Run sets $PORT; gunicorn binds to it. One worker with threads keeps
# memory low while handling concurrent chat requests; bump --workers as needed.
#
# Under Xvfb, so the sign-in browser has a display to be headful on. Headless
# Chrome announces itself in its user agent and its page is never focused, and
# the human check fails it on both. `-a` picks a free display and exports
# DISPLAY, which is what of_connect reads to decide.
CMD exec xvfb-run -a -s "-screen 0 1920x1080x24" \
      gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 120 app:app
