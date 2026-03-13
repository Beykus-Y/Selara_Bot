# Установка и деплой Selara

Этот документ — основная практическая инструкция по запуску проекта в локальной среде и в production.

## 1. Требования

### 1.1 Обязательные компоненты
- Python **3.11+**.
- PostgreSQL (рекомендуется 16).
- Redis (рекомендуется 7).
- Доступ к Telegram Bot API (токен от `@BotFather`).

### 1.2 Для контейнерного запуска
- Docker Engine.
- Docker Compose plugin (`docker compose`).

### 1.3 Для production
- Доменное имя.
- Reverse proxy (Caddy или NGINX).
- HTTPS-сертификат.

---

## 2. Локальный запуск без Docker (bot + web)

```bash
git clone <URL_вашего_репозитория>
cd Selara_Bot
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
docker compose up -d postgres redis
alembic upgrade head
python -m selara.main
```

После запуска:
- Telegram-бот работает в polling-режиме;
- web-панель доступна по адресу `http://127.0.0.1:8080/login`.

---

## 3. Настройка `.env`

### 3.1 Минимальный рабочий набор

```env
BOT_TOKEN=<ваш_telegram_token>
DATABASE_URL=postgresql+asyncpg://selara:selara@localhost:5432/selara
REDIS_URL=redis://localhost:6379/0
WEB_AUTH_SECRET=<длинный_случайный_секрет>
WEB_BASE_URL=http://127.0.0.1:8080
WEB_SESSION_COOKIE_SECURE=false
```

### 3.2 Важные замечания
- `WEB_AUTH_SECRET` нельзя оставлять дефолтным в production.
- `WEB_BASE_URL` должен соответствовать фактическому публичному URL.
- При HTTPS выставляйте `WEB_SESSION_COOKIE_SECURE=true`.

---

## 4. Локальный запуск через Docker Compose

### 4.1 Базовый сценарий

```bash
cp .env.example .env
# заполните BOT_TOKEN и остальные критичные параметры

docker compose build app
docker compose up -d postgres redis app
docker compose logs -f app
```

### 4.2 Проверка web health endpoint

```bash
curl -i http://127.0.0.1:8080/healthz
```

Ожидается успешный HTTP-ответ.

### 4.3 Важная особенность compose-конфига
В `docker-compose.yml` используется внешняя сеть `edge`. На «чистом» сервере её нужно создать заранее:

```bash
docker network create edge
```

---

## 5. Модель с Docker-образом (GHCR/VPS)

### 5.1 Публикация образа

```bash
docker build -t ghcr.io/<your-user>/selara:latest .
docker push ghcr.io/<your-user>/selara:latest
```

### 5.2 Настройка окружения на VPS

```env
SELARA_IMAGE=ghcr.io/<your-user>/selara:latest
SELARA_DATABASE_URL=postgresql+asyncpg://selara:selara@postgres:5432/selara
SELARA_REDIS_URL=redis://redis:6379/0
WEB_HOST=0.0.0.0
WEB_PORT=8080
WEB_BASE_URL=https://bot.example.com
WEB_SESSION_COOKIE_SECURE=true
WEB_AUTH_SECRET=<длинный_случайный_секрет>
```

### 5.3 Обновление приложения на сервере

```bash
docker compose pull app
docker compose up -d app
```

---

## 6. Reverse proxy: вариант 1 — Caddy

### 6.1 Пример Caddyfile

```caddy
bot.example.com {
    encode gzip zstd

    reverse_proxy 127.0.0.1:8080

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        Referrer-Policy "strict-origin-when-cross-origin"
    }
}
```

### 6.2 Ключевые проверки
- Публичный URL совпадает с `WEB_BASE_URL`.
- HTTPS активен.
- `WEB_SESSION_COOKIE_SECURE=true`.

---

## 7. Reverse proxy: вариант 2 — NGINX

### 7.1 Пример конфигурации

```nginx
server {
    listen 80;
    server_name bot.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name bot.example.com;

    ssl_certificate /etc/letsencrypt/live/bot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bot.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 7.2 Ключевые проверки
- Сертификаты валидны, HTTPS работает корректно.
- В `.env` установлен верный `WEB_BASE_URL`.
- `WEB_SESSION_COOKIE_SECURE=true` в production.

---

## 8. Эксплуатационные рекомендации

### 8.1 Безопасность
- Ограничьте прямой доступ к `WEB_PORT` (bind на localhost и/или firewall).
- Используйте уникальный длинный `WEB_AUTH_SECRET`.
- Минимизируйте права учётных данных БД и rotate secrets.

### 8.2 Надёжность
- Делайте регулярные бэкапы PostgreSQL.
- Отдельно контролируйте сохранность Redis при критичных сценариях.
- Включите мониторинг контейнера `app` и health-check `/healthz`.

### 8.3 Обновления
- Прогоняйте миграции только через контролируемый pipeline.
- Перед выкладкой обновлений проверяйте совместимость схемы БД.

---

## 9. Мини-чеклист после деплоя

1. Бот отвечает на `/help` и `/me` в тестовой группе.
2. Веб-логин через `/login` в ЛС выдаёт код и вход работает.
3. Эндпоинт `/healthz` отвечает стабильно.
4. В `/app/docs/admin` и `/app/docs/user` открывается документация.
5. Перезапуск `docker compose up -d app` не нарушает состояние.
