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

## Формат banner JSON

Баннеры лежат в `gacha/config/banners/*.json`.
Файл можно называть как угодно, но на практике лучше держать имя файла равным `code`, например:

```text
gacha/config/banners/genshin.json
gacha/config/banners/hsr.json
```

Текущая схема грузится из `BannerConfig` и `CardConfig` в `gacha/src/gacha_service/application/catalog.py`.

### Верхний уровень

```json
{
  "code": "genshin",
  "title": "Genshin Impact",
  "cooldown_seconds": 10800,
  "cards": []
}
```

- `code` — внутренний код баннера.
  Его же передают в API как `banner`, например `genshin` или `hsr`.
- `title` — человекочитаемое название баннера.
  Используется в сообщениях и админских ответах.
- `cooldown_seconds` — кулдаун между крутками для этого баннера.
  Должен быть больше `0`.
- `cards` — список карт, из которых реально выбирается награда.

### Поля карты

```json
{
  "code": "nahida",
  "name": "Нахида",
  "rarity": "legendary",
  "points": 11000,
  "primogems": 22,
  "adventure_xp": 200,
  "image_url": "/images/genshin/nahida.jpg",
  "weight": 1
}
```

- `code` — уникальный код карты внутри баннера.
  По нему считаются копии в коллекции игрока.
- `name` — отображаемое имя карты.
- `rarity` — редкость.
  Допустимые значения сейчас только: `common`, `rare`, `epic`, `legendary`.
- `points` — сколько очков получает игрок за выпадение этой карты.
  Должно быть `>= 0`.
- `primogems` — сколько примогемов получает игрок.
  Должно быть `>= 0`.
- `adventure_xp` — базовый опыт приключений от карты.
  Должно быть `>= 0`.
- `image_url` — URL или путь к картинке.
  В текущем проекте обычно используется локальный путь вида `/images/<banner>/<file>.jpg`.
- `weight` — относительный вес выпадения.
  Должен быть `>= 1`.
  Если поле не указать, по умолчанию будет `1`.

### Как работают шансы

В текущей версии сервис использует не проценты, а веса.
Выбор идёт через `random.choices(..., weights=...)` в `gacha/src/gacha_service/application/service.py`.

Шанс карты считается так:

```text
chance(card) = card.weight / sum(weight всех карт баннера)
```

Пример:

- есть 3 карты с весами `60`, `30`, `10`;
- сумма весов = `100`;
- итоговые шансы = `60%`, `30%`, `10%`.

Если вы хотите шанс редкости, а не конкретной карты, суммируйте веса всех карт этой редкости:

```text
chance(rarity) = sum(weight карт этой редкости) / sum(weight всех карт баннера)
```

### Важный момент про pity

Сейчас в сервисе **нет pity-системы**.

Это значит:

- нет soft pity;
- нет hard pity;
- нет гаранта по числу неудачных круток;
- шанс определяется только весами `weight`.

Если нужен гарант, его надо добавлять отдельно в сервисную логику, одного JSON для этого сейчас недостаточно.

### Как сейчас работают дубликаты

Логика дубликатов находится в `gacha/src/gacha_service/application/service.py`.

- Первая копия карты считается новой.
- Для баннера `genshin` копии `C1..C6` меняют отображаемое имя карты на `(<Cn>)`.
- Для `genshin` копии после `C6` помечаются как дубликат `C6` и дают удвоенные `primogems`.
- Для остальных баннеров специальной системы созвездий сейчас нет.

### Как сейчас считается adventure XP у копий

Базовое значение берётся из `adventure_xp`, но награда уменьшается в зависимости от уже имеющихся копий:

- `0` копий до этого: `100%` от `adventure_xp`
- `1` копия до этого: `50%`
- `2` копии до этого: `25%`
- `3+` копий до этого: `10%`

Минимум всё равно `1 XP`.

### Практические рекомендации по заполнению

- Сначала определите желаемый шанс по редкостям.
- Потом распределите `weight` между картами внутри каждой редкости.
- Не пытайтесь задавать проценты отдельно: в текущем сервисе важны только относительные веса.
- Для сверхредких карт удобно ставить маленькие веса вроде `1`.
- Для частых карт удобно ставить большие веса вроде `20`, `30`, `50`.
- Следите, чтобы `code` карты не менялся без причины: он влияет на учёт коллекции и копий.
- `image_url` лучше держать стабильным и привязанным к локальным ассетам сервиса.

### Минимальный рабочий пример

```json
{
  "code": "demo",
  "title": "Demo Banner",
  "cooldown_seconds": 3600,
  "cards": [
    {
      "code": "common_slime",
      "name": "Слайм",
      "rarity": "common",
      "points": 100,
      "primogems": 1,
      "adventure_xp": 15,
      "image_url": "/images/demo/slime.jpg",
      "weight": 70
    },
    {
      "code": "rare_mage",
      "name": "Маг",
      "rarity": "rare",
      "points": 700,
      "primogems": 4,
      "adventure_xp": 45,
      "image_url": "/images/demo/mage.jpg",
      "weight": 25
    },
    {
      "code": "legendary_dragon",
      "name": "Дракон",
      "rarity": "legendary",
      "points": 5000,
      "primogems": 15,
      "adventure_xp": 150,
      "image_url": "/images/demo/dragon.jpg",
      "weight": 5
    }
  ]
}
```

У такого баннера шансы будут:

- `common` = `70%`
- `rare` = `25%`
- `legendary` = `5%`

## API

`POST /v1/gacha/pull`

`POST /v1/gacha/admin/cooldowns/reset`

`POST /v1/gacha/admin/backup`

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

Для admin-сброса кулдауна:

```json
{
  "user_id": 12345,
  "banner": "genshin"
}
```

Нужен header:

```text
X-Gacha-Admin-Token: <GACHA_ADMIN_TOKEN>
```

Для backup endpoint нужен тот же header. В ответ сервис возвращает бинарный файл дампа:

- для PostgreSQL это `pg_dump --format=custom`;
- для SQLite это копия `.sqlite3`;
- `Cache-Control: no-store` запрещает кэширование;
- файл создаётся во временной директории и удаляется после отправки ответа.

Опционально можно переопределить путь к утилите дампа:

```text
GACHA_PG_DUMP_PATH=/usr/bin/pg_dump
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
curl -X POST http://127.0.0.1:8001/v1/gacha/admin/backup \
  -H "X-Gacha-Admin-Token: $GACHA_ADMIN_TOKEN" \
  -o gacha.dump
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
