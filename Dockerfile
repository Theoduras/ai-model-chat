FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The OnlyFans connect flow runs a real Chromium the creator signs in through,
# so the image carries the browser and the system libraries it needs.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 8080

# Cloud Run sets $PORT; gunicorn binds to it. One worker with threads keeps
# memory low while handling concurrent chat requests; bump --workers as needed.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 120 app:app
