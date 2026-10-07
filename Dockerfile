FROM python:3.12-slim

# Cache homes for the non-root runtime user created below. /tmp is a tmpfs in
# docker-compose.yml (app and artifact-renderer both run with a read-only root
# fs), so matplotlib (MPLCONFIGDIR) and fontconfig (XDG_CACHE_HOME) rebuild
# their caches there instead of failing on an unwritable $HOME.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    MPLCONFIGDIR=/tmp/mplconfig \
    XDG_CACHE_HOME=/tmp/cache

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential fonts-dejavu-core fonts-noto fonts-noto-color-emoji fonts-noto-cjk fonts-inter fonts-symbola postgresql-client ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md alembic.ini /app/
COPY alembic /app/alembic
COPY src /app/src

RUN pip install --upgrade pip \
    && pip install . \
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
