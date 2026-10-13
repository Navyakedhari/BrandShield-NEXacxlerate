# syntax=docker/dockerfile:1
# ScamMind AI server (API and RQ worker share this image).
#   docker build -t scammind-server ./backend
#   docker run ... scammind-server api       (default)   |   scammind-server worker   |   scammind-server migrate
# Not built or run in the authoring environment (no Docker there): see docs/DECISIONS.md.

# ---- builder: installs runtime dependencies into a virtualenv (no dev tools, no pytest/ruff) ----
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY requirements.txt ./
RUN pip install -r requirements.txt
# Optional extras (Sentry). Build with:  --build-arg INSTALL_EXTRAS=true
ARG INSTALL_EXTRAS=false
COPY requirements-extra.txt ./
RUN if [ "$INSTALL_EXTRAS" = "true" ]; then pip install -r requirements-extra.txt; fi

# ---- runtime ----
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/opt/venv/bin:$PATH" \
    APP_ENV=production DATA_DIR=/data PORT=8000
# Non-root user with a fixed uid so a mounted volume can be chowned to it.
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data && chown app:app /data
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app docker-entrypoint.sh ./
RUN sed -i 's/\r$//' /app/docker-entrypoint.sh && chmod 0555 /app/docker-entrypoint.sh
USER app
VOLUME ["/data"]
EXPOSE 8000
# Readiness-independent liveness probe using only the standard library (no curl in the slim image).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health/live' % os.environ.get('PORT','8000'), timeout=4).status == 200 else 1)"
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["api"]
