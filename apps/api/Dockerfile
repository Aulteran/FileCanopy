# Build context is the repository root:  docker build -f apps/api/Dockerfile .
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/srv/filecanopy/apps/api:/srv/filecanopy
WORKDIR /srv/filecanopy
COPY apps/api/requirements.txt apps/api/requirements.txt
RUN pip install -r apps/api/requirements.txt

# Development image: lint, type check and tests run here (see scripts/api-check.mjs).
FROM base AS dev
COPY apps/api/requirements-dev.txt apps/api/requirements-dev.txt
RUN pip install -r apps/api/requirements-dev.txt
COPY . .

# Runtime image: non-root. Compose may override the uid/gid to match bind-mount ownership.
FROM base AS runtime
RUN groupadd --system --gid 10001 filecanopy \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin filecanopy
COPY apps/api apps/api
COPY workers workers
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
