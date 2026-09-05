# syntax=docker/dockerfile:1

# Build stage — pnpm, matching the version pinned in package.json.
FROM docker.io/library/node:24-alpine AS build

RUN corepack enable

WORKDIR /app

# Install dependencies first so this layer caches on lockfile changes only.
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build


# Runtime stage — nginx serving the built SPA.
FROM docker.io/nginxinc/nginx-unprivileged:1.29-alpine

# The unprivileged image already runs as uid 101 and listens on 8080.
# The entrypoint writes config.js into the web root at startup, so that
# directory must be owned by the runtime user.
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --chmod=755 docker/entrypoint.sh /docker-entrypoint.d/99-scoutid-config.sh

EXPOSE 8080
