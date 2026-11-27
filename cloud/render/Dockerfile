# Prometheus Cloudroom on Render's free web service (M6 6.2). It runs ONLY the Cloudroom server: it takes a task the
# laptop asked it to run, runs it in Prometheus's sandbox (no internet, 256 MB, 300 seconds) and returns a receipt.
# Build context: the repository root (render.yaml says so). Only the code is copied: no .git, no data, no keys.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    CLOUDROOM_DIR=/tmp/cloudroom

RUN useradd --create-home --uid 10001 cloudroom
WORKDIR /app

COPY cloud/render/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

COPY prometheus /app/prometheus

USER cloudroom
EXPOSE 10000

# Render sets PORT (10000 if it doesn't). The server checks a signed token on every request except /v1/health.
CMD ["sh", "-c", "exec uvicorn --factory prometheus.runtime.cloudroom_server:app_from_env --host 0.0.0.0 --port ${PORT:-10000}"]
