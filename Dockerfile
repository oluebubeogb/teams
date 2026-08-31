FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl libreoffice \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Writable dirs for SQLite + uploads (Coolify should mount volumes here)
RUN mkdir -p /app/data app/uploads && chmod -R 777 /app/data app/uploads

ENV PYTHONUNBUFFERED=1
ENV UPLOAD_DIR=app/uploads
ENV DATA_DIR=/app/data

EXPOSE 8008

# Coolify prefers curl/wget for healthchecks
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://127.0.0.1:8008/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8008"]
