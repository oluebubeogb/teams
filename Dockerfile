FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data \
    CACHE_DIR=/tmp/teams-cache

WORKDIR /app

# poppler-utils: PDF page previews (pdftoppm). LibreOffice (headless, no GUI deps): DOCX/PPTX/XLSX previews.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl poppler-utils fonts-dejavu-core fonts-liberation \
        libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# Mount a persistent volume here (uploads, thumbnails, avatars when STORAGE_BACKEND=local)
RUN mkdir -p /app/data /tmp/teams-cache && chmod -R 777 /app/data /tmp/teams-cache
VOLUME ["/app/data"]

EXPOSE 8008

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8008/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8008", "--proxy-headers", "--forwarded-allow-ips", "*"]
