FROM python:3.12-slim

# uv 0.11.33, pinned to the registry manifest digest (not a mutable tag).
COPY --from=ghcr.io/astral-sh/uv@sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7 /uv /bin/uv

# Cache homes for the non-root runtime user created below. /tmp is a tmpfs in
# docker-compose.yml (app and artifact-renderer both run with a read-only root
# fs), so matplotlib (MPLCONFIGDIR) and fontconfig (XDG_CACHE_HOME) rebuild
# their caches there instead of failing on an unwritable $HOME.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    MPLCONFIGDIR=/tmp/mplconfig \
    XDG_CACHE_HOME=/tmp/cache \
    PATH="/app/.venv/bin:$PATH" \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential fonts-dejavu-core fonts-noto fonts-noto-color-emoji fonts-noto-cjk fonts-inter fonts-symbola postgresql-client ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md alembic.ini /app/
COPY gacha/pyproject.toml gacha/README.md /app/gacha/
COPY alembic /app/alembic
COPY src /app/src

RUN uv sync --locked --no-dev --group build --package selara --no-install-workspace --no-build \
    && uv sync --locked --no-dev --group build --package selara --no-editable --no-build-isolation \
    && playwright install chromium --with-deps \
    && chmod -R a+rX /ms-playwright

# Non-root runtime user. uid/gid 10001 must match the `user:` override for the
# artifact-renderer service in docker-compose.yml. /data/gacha_reel_cache is
# pre-created with the correct owner so that a freshly created named volume
# (selara_gacha_reel_cache in docker-compose.yml) inherits it on first mount.
RUN groupadd --gid 10001 selara \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin selara \
    && mkdir -p /data/gacha_reel_cache \
    && chown selara:selara /data/gacha_reel_cache

USER 10001:10001

EXPOSE 8080

CMD ["sh", "-c", "alembic upgrade head && python -m selara.main"]
