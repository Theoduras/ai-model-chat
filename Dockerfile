FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    PORT=8080

WORKDIR /app

# Chrome first: it is the slowest layer and the one that changes least, so with
# --cache-from it is reused on every build that only touches Python or source.
# Below a requirements.txt that changes often it would be re-downloaded instead.
RUN apt-get update --fix-missing && apt-get install -y --no-install-recommends \
    wget \
    xvfb \
    xauth \
    fonts-liberation \
    && wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm /tmp/chrome.deb \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /usr/share/doc /usr/share/man

COPY requirements.txt .
# --no-compile: PYTHONDONTWRITEBYTECODE already keeps the runtime from writing
# .pyc, so the ones pip bakes in are dead weight in the image.
RUN pip install --no-cache-dir --no-compile -r requirements.txt \
    && find /usr/local/lib/python3.12 -name '__pycache__' -type d -prune -exec rm -rf {} +

COPY . .

CMD ["/bin/sh", "/app/start.sh"]
