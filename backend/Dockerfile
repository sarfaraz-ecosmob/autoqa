# AutoQA backend / workers / scheduler — one image, three roles.
# Non-root, pinned base, layered for cache-friendly rebuilds.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp

WORKDIR /app

RUN groupadd -r autoqa && useradd -r -g autoqa -d /app autoqa

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R autoqa:autoqa /app

# Shared artifact volume (screenshots/traces/reports): writable by the
# non-root user regardless of which service initializes the named volume.
RUN mkdir -p /data/artifacts && chmod 777 /data/artifacts

USER autoqa

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Fresh deployments: apply migrations before serving so a plain
# `docker compose up -d` produces a working API with zero manual steps.
# On subsequent starts this is a cheap no-op (alembic checks version).
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
