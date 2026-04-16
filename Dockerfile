FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential fonts-noto fonts-noto-color-emoji fonts-symbola postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md alembic.ini /app/
COPY alembic /app/alembic
COPY src /app/src

RUN pip install --upgrade pip \
    && pip install .

EXPOSE 8080

CMD ["sh", "-c", "alembic upgrade head && python -m selara.main"]
