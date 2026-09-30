FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    PORT=8080

WORKDIR /app

# No Chrome or Xvfb here: sign-in runs on the browser service (Dockerfile.browser),
# which the app reaches through ONLYFANS_BROWSER_URL. Set that on every service
# that runs this image, or the sign-in windows have no browser to open.
# ffmpeg lays an uploaded track over a finished clip, or takes its sound off.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# --no-compile: PYTHONDONTWRITEBYTECODE already keeps the runtime from writing
# .pyc, so the ones pip bakes in are dead weight in the image.
RUN pip install --no-cache-dir --no-compile -r requirements.txt \
    && find /usr/local/lib/python3.12 -name '__pycache__' -type d -prune -exec rm -rf {} +

COPY . .

CMD ["/bin/sh", "/app/start.sh"]
