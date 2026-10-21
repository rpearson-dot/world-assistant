# syntax=docker/dockerfile:1
#
# Two stages: dependencies are installed from the hash-locked requirements.lock into a
# virtualenv, then only that virtualenv and the application are copied into the final
# image. The final image has no pip cache, no build tools and no package manager state.
#
# CI passes BASE_IMAGE as python@sha256:<digest> so each release is built on one exact
# base image (recorded in the build provenance). The default tag is for local builds.
ARG BASE_IMAGE=python:3.12-slim

FROM ${BASE_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.lock /tmp/requirements.lock
# --require-hashes: refuse anything not in the lock; --no-deps: the lock is complete
RUN /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock

FROM ${BASE_IMAGE}
ARG VERSION=dev
ARG COMMIT=unknown
ARG SOURCE_URL=https://github.com/rpearson-dot/world-assistant
LABEL org.opencontainers.image.title="world-assistant" \
      org.opencontainers.image.description="API gateway for everything outside the home" \
      org.opencontainers.image.source="${SOURCE_URL}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${COMMIT}"

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WA_VERSION=${VERSION} \
    WA_COMMIT=${COMMIT}

COPY --from=build /opt/venv /opt/venv

# Unprivileged user with no home and no shell
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin wa

WORKDIR /srv
COPY app ./app
# Migrations ship in the same image and are run by the `migrate` service
COPY alembic.ini ./
# Tells scripts/auto-deploy.sh whether the server's compose files are new enough for this image
COPY COMPOSE_REVISION ./
COPY migrations ./migrations

USER 10001:10001
EXPOSE 8000

# Default is the API; the worker and migrate services override the command in compose
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
