# Selara Bot

> Основная документация проекта ведётся на русском языке.  
> English summary is intentionally short and placed at the end of this file.

---

## 🇷🇺 Полное описание проекта

Selara — Telegram-бот для групп и сообществ, объединяющий статистику активности, игровые механики, экономику, отношения, модерацию, кастомные роли/ранги и веб-панель администрирования. Проект построен на `aiogram 3` и `FastAPI`, использует `PostgreSQL` для постоянных данных и `Redis` для оперативного состояния игровых/временных механик.

### 1. Архитектура и состав репозитория

Проект включает:

- **Bot runtime (Python)** — обработка команд, событий и бизнес-логики.
- **Web runtime (FastAPI)** — вход по Telegram-коду (`/login`), панель `/app`, встроенные docs-страницы.
- **Хранилища данных**:
  - PostgreSQL (основные сущности и история);
  - Redis (временные состояния, игровые данные, кешоподобные сценарии).
- **Миграции Alembic** для контроля схемы базы.
- **Docker/Compose** для локального и серверного развёртывания.
- **CI/CD workflow** для публикации Docker-образа и деплоя на VPS.

### 2. Возможности бота (актуально по текущему коду)

#### 2.1 Профили, репутация и активность
- `/me`, `/rep` — персональная статистика и репутация.
- `/top`, `/active` — рейтинг с разными режимами/окнами.
- `/lastseen` — когда пользователь был активен в чате.
- `/achievements`, `/achsync` — система достижений и служебная синхронизация.
- `/iris_perenos` — перенос профиля из Iris (если применимо к вашему чату).

#### 2.2 Игровой блок
- `/game` — запуск и управление игровыми режимами.
- Поддерживаемые режимы включают: `spy`, `mafia`, `dice`, `number`, `quiz`, `bredovukha`, `bunker`.
- `/role` — выдача приватной роли через ЛС (для режимов с скрытой информацией).

#### 2.3 Экономика и прогресс
- Базовые: `/eco`, `/tap`, `/daily`, `/farm`, `/shop`, `/inventory`, `/lottery`, `/market`, `/pay`, `/growth`.
- Расширенные: `/craft`, `/auction`, `/bid`, `/article`.
- Поддерживаются режимы экономики (например global/local), задаются через конфигурацию группы.

#### 2.4 Отношения, семья и RP-составляющая
- `/relation`, `/pair`, `/marry`, `/breakup`, `/divorce`.
- `/love`, `/care`, `/date`, `/gift`, `/support`, `/flirt`, `/surprise`, `/vow`.
- `/adopt`, `/pet`, `/family`, `/title`.

#### 2.5 Администрирование, роли и доступы
- Ролевой контур: `/roles`, `/roleadd`, `/roleremove`, `/roledefs`, `/roletemplates`, `/rolecreate`, `/rolesettitle`, `/rolesetrank`, `/roleperms`, `/roledelete`.
- Модерация: `/pred`, `/warn`, `/unwarn`, `/ban`, `/unban`, `/modstat`.
- Настройки: `/settings`, `/setcfg`, `/setrank`, `/ranks`.
- Текстовые алиасы: `/setalias`, `/aliases`, `/unalias`, `/aliasmode`.
- Смарт-триггеры/RP-автоматизация: `/settrigger`, `/triggers`, `/triggervars`, `/deltrigger`, `/rpadd`, `/rps`, `/rpdel`.

### 3. Как использовать бот: пользователь и администратор

#### 3.1 Обычный пользователь
1. В группе выполните `/help` для обзора возможностей.
2. Проверьте профиль через `/me`.
3. Для приватных функций (роли в играх, авторизация web) откройте ЛС с ботом и отправьте `/start`.
4. Для входа в web-панель отправьте в ЛС `/login`, получите одноразовый код и введите его на странице входа.

Подробно: **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)** — расширенное руководство (включая текстовые команды, игровые сценарии и практический атлас пользовательских проблем).

#### 3.2 Администратор группы
1. Проверьте/настройте ранг доступа к критичным командам (`/setrank`, `/ranks`).
2. Настройте параметры группы через `/setcfg`.
3. При необходимости создайте свои роли и права (`/role*`).
4. Определите стратегию текстовых алиасов (`/aliasmode`) и смарт-триггеров.
5. Используйте web-доки `/app/docs/admin` для централизованного контроля.

Подробно: **[docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md)**.

### 4. Веб-панель

- Вход: `/login` (через 6-значный код из ЛС бота).
- Базовый URL локально: `http://127.0.0.1:8080/login`.
- Панель: `/app`.
- Проверка работоспособности: `/healthz`.
- Встроенная документация:
  - `/app/docs/user`
  - `/app/docs/admin`

Ключевые env-параметры:
- `WEB_ENABLED`
- `WEB_HOST`, `WEB_PORT`
- `WEB_BASE_URL`
- `GACHA_BASE_URL`
- `GACHA_GENSHIN_BASE_URL`, `GACHA_HSR_BASE_URL`
- `WEB_AUTH_SECRET`
- `WEB_SESSION_COOKIE_SECURE`

Для внешней гачи бот поддерживает текстовые команды `гача генш`, `гача геншин`, `гача хср`,
а также профильные `моя гача генш`, `моя гача геншин`, `моя гача хср`. Можно указать один общий
`GACHA_BASE_URL` или раздельные URL для разных баннеров, если сервисы стоят на разных VPS.

### 5. Установка и эксплуатация

Для практической установки и продакшен-деплоя используйте отдельный документ:

- **[INSTALLATION.md](INSTALLATION.md)** — подробная инструкция (локально, Docker, GHCR, reverse proxy через Caddy и NGINX, рекомендации по безопасности).

### 6. Аналитические документы по качеству проекта

- **[BUGS.md](BUGS.md)** — пользовательские баги и спорные механики (как проявляются и как воспроизводятся с точки зрения участника чата).
- **[CONTRADICTIONS.md](CONTRADICTIONS.md)** — найденные противоречия в документации/конфигурации.
- **[MISSING_LOGIC.md](MISSING_LOGIC.md)** — недостающие, но логически ожидаемые артефакты и процессы.

### 7. Docker и CI/CD (кратко)

- В репозитории есть `Dockerfile` и `docker-compose.yml` с сервисами `postgres`, `redis`, `app`.
- Есть workflow публикации Docker-образа в GHCR.
- Есть workflow деплоя на VPS по SSH.
- Контейнер приложения запускает миграции (`alembic upgrade head`) перед стартом runtime.

### 8. Проверка и тесты

Минимальная команда:

```bash
pytest
```

Важно:
- используйте Python **3.11+**;
- предварительно установите dev-зависимости:

```bash
pip install -e .[dev]
```

---

## 🇬🇧 English summary (short)

Selara is a Telegram bot + FastAPI web panel project with activity analytics, games, economy, social mechanics, moderation, role-based permissions, and chat automation.

- Full docs are in Russian.
- Setup and deployment guide: [INSTALLATION.md](INSTALLATION.md).
- User/admin deep guides: [docs/USER_GUIDE.md](docs/USER_GUIDE.md), [docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md).
- Current quality analysis: [BUGS.md](BUGS.md), [CONTRADICTIONS.md](CONTRADICTIONS.md), [MISSING_LOGIC.md](MISSING_LOGIC.md).
