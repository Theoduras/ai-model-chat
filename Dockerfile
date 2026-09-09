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

# Cloud Run sets $PORT; gunicorn binds to it. start.sh brings up the display the
# sign-in browser needs first, then hands over to gunicorn.
CMD ["/bin/sh", "/app/start.sh"]
