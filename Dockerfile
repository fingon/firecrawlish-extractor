ARG PYTHON_BASE=python-base

FROM python:3.15.1-slim AS python-base

FROM ghcr.io/astral-sh/uv:0.13.6 AS uv-tools

FROM ${PYTHON_BASE} AS builder

COPY --from=uv-tools /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --locked --no-dev --no-editable

FROM ${PYTHON_BASE}

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 firecrawlish \
    && useradd --system --uid 10001 --gid 10001 --home-dir /nonexistent \
        --shell /usr/sbin/nologin firecrawlish

WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv

USER 10001:10001

EXPOSE 3002

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:3002/healthz', timeout=2)"]

ENTRYPOINT ["firecrawlish-extractor"]
