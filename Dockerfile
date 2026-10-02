# syntax=docker/dockerfile:1

# Build stage — the SPA, with the pnpm version pinned in package.json.
FROM docker.io/library/node:24-alpine AS web

RUN corepack enable

WORKDIR /web

# Install dependencies first so this layer caches on lockfile changes only.
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY index.html vite.config.ts tsconfig.json tsconfig.node.json ./
COPY src/ ./src
RUN pnpm build


# Runtime stage — FastAPI serving the SPA, /auth and /api.
FROM docker.io/library/python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

# Local time in the logs; unbuffered output straight to the container log.
ENV TZ="Europe/Stockholm" \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# Dependencies first so this layer caches independently of the source.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src

COPY src/start.py ./src/
COPY src/app/ ./src/app/
COPY --from=web /web/dist ./dist

# Non-root: this process holds the service-account secret, which can manage
# every client in the realm. Nothing is written at runtime, so the root
# filesystem can be mounted read-only.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001

EXPOSE 8080

# start.py resolves .env relative to the working directory. There is no .env in
# the image by design — configuration comes from the Deployment's environment.
WORKDIR /app/src
CMD ["python", "start.py"]
