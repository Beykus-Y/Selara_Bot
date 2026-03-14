# Gacha

Автономный подпроект для карточной гачи, который можно запускать отдельно от Selara на другом VPS.

## Что делает MVP

- хранит игроков и историю круток в собственной PostgreSQL базе;
- выполняет крутку по API;
- выдает готовый текст для Telegram;
- начисляет очки, примогемы и опыт приключений;
- держит кулдаун на получение новой карты.

## Быстрый старт

```bash
cd gacha
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
export GACHA_DATABASE_URL=postgresql+asyncpg://gacha:gacha@127.0.0.1:5432/gacha
alembic upgrade head
uvicorn gacha_service.main:app --reload
```

Сервис будет доступен на `http://127.0.0.1:8001`.

## Docker

Сборка образа:

```bash
docker build -f gacha/Dockerfile -t selara-gacha:local .
```

Запуск через отдельный compose:

```bash
docker compose -f gacha/docker-compose.yml up --build
```

По умолчанию compose поднимает:

- `postgres:16-alpine` с базой `gacha`;
- `gacha` API на `http://0.0.0.0:8001`;
- схему через `alembic upgrade head` перед стартом приложения.

Данные PostgreSQL хранятся в volume `selara_gacha_postgres_data`.

Конфиги баннеров и героев лежат в `gacha/config/banners/*.json`.
Локальные изображения кладите в `gacha/images/<banner>/`.

Пример:

```text
gacha/images/genshin/amber.jpg
gacha/images/genshin/nahida.jpg
gacha/images/hsr/kafka.jpg
```

Сервис сам раздает их с вашего VPS по пути `/images/...`, например:

```text
http://your-vps:8001/images/genshin/amber.jpg
```

## API

`POST /v1/gacha/pull`

`GET /v1/gacha/users/{user_id}/profile?banner=genshin`

`GET /v1/gacha/users/{user_id}/history?banner=genshin&limit=10`

Пример тела:

```json
{
  "user_id": 12345,
  "username": "beykus",
  "banner": "genshin"
}
```

Пример ответа:

```json
{
  "status": "ok",
  "message": "🍀 Вы получили новую карту: Оророн\n\n⬜ Редкость: 🟪 Эпическая\n\n🌟 Очки: +5000 [6000]\n💠 Примогемы: +10 [2612]\n🧭 Ранг приключений: 2 (120/450)\n\n⌛️ Вы сможете получить карту через: 3:00:00",
  "card": {
    "code": "ororon",
    "name": "Оророн",
    "rarity": "epic",
    "rarity_label": "🟪 Эпическая",
    "points": 5000,
    "primogems": 10,
    "image_url": "https://example.com/ororon.jpg"
  },
  "player": {
    "user_id": 12345,
    "adventure_rank": 2,
    "adventure_xp": 120,
    "xp_into_rank": 120,
    "xp_for_next_rank": 450,
    "total_points": 6000,
    "total_primogems": 2612
  },
  "cooldown_until": "2026-03-14T09:00:00Z"
}
```

Для команды вида `моя гача геншин` основной бот позже может вызывать:

```text
GET /v1/gacha/users/12345/profile?banner=genshin
```

Этот endpoint возвращает:

- готовый `message` для Telegram;
- текущий ранг приключений;
- очки и примогемы;
- количество уникальных карт и копий;
- последние крутки по выбранному баннеру.

## Интеграция с Selara позже

Основной бот может:

1. принять текстовую команду `гача генш`;
2. отправить запрос в этот сервис;
3. получить `message`, `image_url` и отдать их в Telegram.

Основной код Selara этот MVP не меняет.

## Миграции

Инициализация схемы:

```bash
cd gacha
export GACHA_DATABASE_URL=postgresql+asyncpg://gacha:gacha@127.0.0.1:5432/gacha
alembic upgrade head
```

Создание новой миграции:

```bash
cd gacha
alembic revision -m "describe change"
```

## Как тестировать

Быстрая проверка unit-тестов:

```bash
pytest -q gacha/tests/test_service.py
```

Проверка импортов и синтаксиса:

```bash
python3 -m compileall gacha/src gacha/tests
```

Ручной локальный smoke test:

```bash
curl -X POST http://127.0.0.1:8001/v1/gacha/pull \
  -H "Content-Type: application/json" \
  -d '{"user_id":12345,"username":"tester","banner":"genshin"}'
```

Проверка из контейнера:

```bash
curl http://127.0.0.1:8001/v1/gacha/health
curl -X POST http://127.0.0.1:8001/v1/gacha/pull \
  -H "Content-Type: application/json" \
  -d '{"user_id":12345,"username":"tester","banner":"genshin"}'
```

Пример для второго баннера:

```bash
curl -X POST http://127.0.0.1:8001/v1/gacha/pull \
  -H "Content-Type: application/json" \
  -d '{"user_id":12345,"username":"tester","banner":"hsr"}'
```

Проверка профиля игрока:

```bash
curl "http://127.0.0.1:8001/v1/gacha/users/12345/profile?banner=genshin"
```

Проверка последних круток:

```bash
curl "http://127.0.0.1:8001/v1/gacha/users/12345/history?banner=genshin&limit=5"
```
