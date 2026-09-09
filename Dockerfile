FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The OnlyFans connect flow runs a real Chromium the creator signs in through,
# so the image carries the browser and the libraries it needs.
#
# The libraries are listed out rather than left to `playwright install
# --with-deps`, which runs its own apt-get update and pulls a much wider set —
# minutes of build time for packages a headless Chromium never opens.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN apt-get update && apt-get install -y --no-install-recommends \
      libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 libcups2 \
      libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
      libgbm1 libpango-1.0-0 libcairo2 libasound2 libx11-6 libxcb1 libxext6 \
      fonts-liberation \
    && playwright install chromium \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY . .

EXPOSE 8080

# Cloud Run sets $PORT; gunicorn binds to it. One worker with threads keeps
# memory low while handling concurrent chat requests; bump --workers as needed.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 120 app:app
