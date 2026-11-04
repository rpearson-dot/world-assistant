# syntax=docker/dockerfile:1
#
# World Assistant's supermarket service (src/server.js). Playwright's own image, so Chromium and everything it needs
# are already there; the version matches the playwright package in package-lock.json. Only the production
# dependencies are installed, from the lock file. It runs as uid 10001, like World Assistant, with its saved retailer
# logins in /data (WA_DATA_DIR/supermarket).
ARG BASE_IMAGE=mcr.microsoft.com/playwright:v1.64.1-noble
FROM ${BASE_IMAGE}

ARG VERSION=dev
ARG COMMIT=unknown
LABEL org.opencontainers.image.title="world-assistant-supermarket" \
      org.opencontainers.image.description="World Assistant's supermarket service (open-supermarkets)" \
      org.opencontainers.image.source="https://github.com/rpearson-dot/world-assistant" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${COMMIT}"

ENV NODE_ENV=production \
    HOME=/data \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    SUPERMARKET_API_PORT=7876

WORKDIR /srv
COPY package.json package-lock.json ./
# --ignore-scripts: nothing runs at install; the browsers come with the base image
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY src ./src

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin wa \
 && mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
EXPOSE 7876
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "const n=require('net');const s=n.connect(7876,'127.0.0.1',()=>{s.end();process.exit(0)});s.on('error',()=>process.exit(1))"
CMD ["node", "src/server.js"]
