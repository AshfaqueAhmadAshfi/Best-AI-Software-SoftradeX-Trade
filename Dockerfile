# Exness Trading Dashboard - production image
#
# The app is one FastAPI process that serves the REST API, the WebSocket stream
# and the dashboard itself, so a single container is all a host needs.
# MetaTrader5 is Windows-only and is skipped by requirements.txt on Linux: the
# dashboard then runs on its public market feed (see DEPLOY.md).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY backend ./backend
COPY web ./web

# writable state lives here; docker-compose mounts named volumes on these paths
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p data config ea \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/' % os.environ.get('PORT','8080'), timeout=4)"

CMD ["python", "backend/app.py"]
