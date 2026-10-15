# FinanceBrain web: Next.js static export served by Caddy, which also reverse-proxies /api and /mcp
# to the API container and enforces the CloudFront origin secret. Build context is the repo root.
FROM node:24-alpine AS build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN rm -rf out .next && NODE_ENV=production npx next build

FROM caddy:2
COPY infra/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /web/out /srv/web
