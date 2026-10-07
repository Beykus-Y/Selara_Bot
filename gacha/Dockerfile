FROM python:3.12-slim

# The same locked resolver as app/CI, uv 0.11.33.
COPY --from=ghcr.io/astral-sh/uv@sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/app/.venv/bin:$PATH" \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md /app/
COPY gacha/pyproject.toml gacha/README.md /app/gacha/
COPY gacha/alembic.ini /app/
COPY gacha/alembic /app/alembic
COPY gacha/config /app/config
COPY gacha/images /app/images
COPY gacha/src /app/gacha/src

RUN uv sync --locked --no-dev --group build --package selara-gacha --no-install-workspace --no-build \
    && uv sync --locked --no-dev --group build --package selara-gacha --no-editable --no-build-isolation

RUN groupadd --gid 10001 gacha \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin gacha \
    && mkdir -p /data \
    && chown gacha:gacha /data

USER 10001:10001

EXPOSE 8001

CMD ["sh", "-c", "alembic upgrade head && uvicorn gacha_service.main:app --host 0.0.0.0 --port 8001"]
