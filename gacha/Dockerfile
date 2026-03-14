FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY gacha/pyproject.toml gacha/README.md /app/
COPY gacha/config /app/config
COPY gacha/images /app/images
COPY gacha/src /app/src

RUN pip install --upgrade pip \
    && pip install .

EXPOSE 8001

CMD ["uvicorn", "gacha_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
