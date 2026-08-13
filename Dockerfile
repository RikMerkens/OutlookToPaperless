FROM python:3.14-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOME=/app

WORKDIR ${APP_HOME}

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

RUN groupadd --system --gid 10001 app \
  && useradd --system --uid 10001 --gid app --home-dir ${APP_HOME} --no-create-home app \
  && mkdir -p ${APP_HOME}/data \
  && chown -R app:app ${APP_HOME}

COPY --chown=app:app docker-entrypoint.sh ./
COPY --chown=app:app scripts/outlook_to_paperless.py ./scripts/
COPY --chown=app:app src/*.py ./src/

RUN chmod +x docker-entrypoint.sh

# Default data directory for cache + token persistence
VOLUME ["/app/data"]

USER app

ENTRYPOINT ["./docker-entrypoint.sh"]

