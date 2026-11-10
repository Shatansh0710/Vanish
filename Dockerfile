# Vanish app image: Python + Flask, served by Gunicorn on port 8000.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/vanish.db

WORKDIR /app

# Install dependencies first so code changes don't re-run pip (faster rebuilds).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

# Run as a non-root user; /data holds the SQLite database (a Docker volume).
RUN useradd --system --uid 10001 vanish \
 && mkdir -p /data && chown vanish:vanish /data
USER vanish

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"

# One worker process keeps a single cleanup thread; 8 threads handle concurrent requests.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", \
     "--access-logfile", "-", "main:app"]
