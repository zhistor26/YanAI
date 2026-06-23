ARG BUILDPLATFORM
ARG TARGETPLATFORM

FROM --platform=$BUILDPLATFORM node:22-alpine AS web-build

WORKDIR /app/web

COPY yanai-src.tar.gz /tmp/yanai-src.tar.gz
RUN apk add --no-cache tar \
    && mkdir -p /tmp/src \
    && tar -xzf /tmp/yanai-src.tar.gz -C /tmp/src \
    && cp /tmp/src/web/package.json /tmp/src/web/package-lock.json ./ \
    && npm ci \
    && cp -a /tmp/src/web/. ./ \
    && NEXT_PUBLIC_APP_VERSION="$(cat /tmp/src/VERSION)" npm run build


FROM --platform=$TARGETPLATFORM python:3.13-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    tar \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY yanai-src.tar.gz /tmp/yanai-src.tar.gz
RUN mkdir -p /tmp/src \
    && tar -xzf /tmp/yanai-src.tar.gz -C /tmp/src \
    && cp /tmp/src/pyproject.toml /tmp/src/uv.lock ./ \
    && uv sync --frozen --no-dev --no-install-project \
    && cp /tmp/src/main.py /tmp/src/VERSION ./ \
    && cp -a /tmp/src/api ./api \
    && cp -a /tmp/src/services ./services \
    && cp -a /tmp/src/utils ./utils \
    && cp -a /tmp/src/scripts ./scripts \
    && cp /tmp/src/config.example.json ./config.example.json \
    && mkdir -p /app/data

COPY --from=web-build /app/web/out ./web_dist

VOLUME ["/app/data"]
EXPOSE 80

CMD ["sh", "-c", "python /app/scripts/bootstrap_defaults.py && exec uvicorn main:app --host 0.0.0.0 --port 80 --access-log"]
