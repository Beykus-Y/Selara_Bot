# Инвентарь серверного Web UI Selara

Актуально на 2026-08-15. Это приложение к
[`WEB_UI_MODERNIZATION_TODO.md`](WEB_UI_MODERNIZATION_TODO.md). При изменении
серверного маршрута, шаблона, авторизации или API соответствующая строка должна
обновляться в том же коммите.

## Уровни доступа

- **Public** — страница открывается без пользовательской сессии.
- **User** — требуется Web-сессия Telegram-пользователя.
- **User + chat** — требуется Web-сессия и видимость конкретного чата.
- **User + permission** — часть страницы или действие требует права управления.
- **Admin** — требуется отдельная административная сессия.
- **Partial** — самостоятельного route нет, HTML включается в другой шаблон.

## Маршруты и шаблоны

| Область | Jinja-шаблон | HTML route | JSON/API counterpart | Доступ сейчас | Обязательные состояния для baseline | Риск |
|---|---|---|---|---|---|---|
| Общая оболочка | `base.html` | Все Jinja-страницы | — | Зависит от страницы | nav overflow, flash/error, long title, keyboard focus, mobile menu | Высокий: общий для всего UI |
| Landing | `landing.html` | `GET /` | `GET /api/landing/context` | Public, учитывает optional user session | guest, logged in, flash, error, mobile CTA | Средний |
| Вход пользователя | `login.html` | `GET/POST /login` | `GET /api/login/context` | Public; logged-in redirect | empty, invalid code, expired code, rate limit, server error | Высокий: auth |
| Главная пользователя | `home.html` | `GET /app` | `GET /api/app/home` | User | no groups, many groups, long names, expired session, economy absent | Средний |
| Достижения | `achievements.html` | `GET /app/achievements` | `GET /api/app/achievements` | User | empty catalogue, locked/unlocked, long labels, expired session | Низкий |
| Обратная связь | `feedback.html` | `GET/POST /app/feedback` | `GET/POST /api/app/feedback` | User | empty history, open/done requests, validation, duplicate submit, server error | Средний |
| Игры | `games.html` | `GET /app/games` | `GET /api/app/games`, `GET /app/games/live` | User | no manageable chats, lobby, active games, reconnect, errors, large roster | Очень высокий |
| Игровой dashboard | `_games_dashboard.html` | Partial внутри `games.html` и live fragment | HTML в `/app/games/live` | Partial/User | каждый игровой режим и стадия | Очень высокий |
| Чат и настройки | `chat.html` | `GET /app/chat/{chat_id}` | `/api/chat/{chat_id}/overview`, `leaderboard`, `achievements`, `settings` | User + chat; отдельные секции User + permission | inaccessible chat, read-only, manager, empty/large stats, settings errors, live updates | Очень высокий |
| Семья | `family.html` | `GET /app/family/{chat_id}` | `GET /api/chat/{chat_id}/family` | User + chat | empty family, large graph, unavailable chat, JS error | Средний |
| Экономика | `economy.html` | `GET /app/chat/{chat_id}/economy` | `/api/chat/{chat_id}/economy*` | User + chat; зависит от settings/locks | disabled, global/local, empty inventory, market errors, locked writes | Высокий |
| Аудит чата | `audit.html` | `GET /app/chat/{chat_id}/audit` | `GET /api/chat/{chat_id}/audit` | User + chat; содержимое зависит от permission | empty, many entries, inaccessible chat, expired session | Средний |
| Ошибка | `error.html` | Используется несколькими routes для 403/404/5xx | — | Public/User context | 403, 404, 500, recovery link, missing optional user | Средний |
| Пользовательская документация | `user_docs.html` | `GET /app/docs/user` | `GET /api/app/docs/user` | Public; optional user/chat context | public, contextual chat, search/anchor future, mobile TOC | Средний |
| Документация администраторов групп | `admin_docs.html` | `GET /app/docs/admin` | `GET /api/app/docs/admin` | Public с optional user/chat context; это справка по управлению группой, а не системная админка | public, contextual chat, search, deep link, mobile TOC | Средний: не допустить попадания внутренних system-admin процедур |
| Вход администратора | `admin_login.html` | `GET/POST /app/admin/login` | `POST /api/admin/login` | Public; active admin redirect | invalid password, unsafe configured password, rate limit, expired session | Высокий: auth |
| Главная администратора | `admin.html` | `GET /app/admin` | `GET /api/admin` | Admin | empty/large chats, broadcasts, feedback, backup success/error, expired session | Очень высокий |
| Детали рассылки | `admin_broadcast_detail.html` | `GET /app/admin/broadcasts/{id}` | `GET /api/admin/broadcasts/{id}` | Admin | missing broadcast, mixed delivery states, replies, all reaction types, bot reaction failure | Очень высокий |
| Архив сообщений | `admin_messages_compact.html` | `GET /app/admin/table/messages_compact` | generic admin table API пока не возвращает диалоговый view | Admin | empty, filters, replies, edits, media, unavailable file, long archive, mobile navigation | Очень высокий |
| Generic DB explorer | `admin_table.html` | `GET /app/admin/table/{table}` | `GET /api/admin/table/{table}` | Admin | invalid table/filter, empty/large data, composite PK, raw JSON, mobile overflow | Высокий: destructive tooling |
| Редактор записи | `admin_edit.html` | `GET /app/admin/table/{table}/edit`; POST update | API edit/update | Admin | unauthenticated denial, invalid table/PK, missing row, validation, composite PK | Очень высокий: sensitive data/write |

## Формы и изменяющие маршруты, которые должны сохранять поведение

### Пользовательские

- `POST /login`, `POST /logout`.
- `POST /app/feedback`.
- `POST /app/games/create`, `POST /app/games/action`.
- `POST /app/chat/{chat_id}/triggers`.
- `POST /app/chat/{chat_id}/aliases`.
- `POST /app/chat/{chat_id}/settings`.
- Economy mutations работают через `/api/chat/{chat_id}/economy/*`.

### Административные

- `POST /app/admin/login`, `POST /app/admin/logout`.
- `POST /app/admin/request-backup`.
- `POST /app/admin/broadcasts/send`.
- `POST /app/admin/broadcasts/{broadcast_id}/replies/{reply_id}/reaction`.
- `POST /app/admin/feedback/{request_id}/status`.
- `POST /app/admin/table/{table_name}/update`.
- `POST /app/admin/table/{table_name}/delete`.

Для каждой формы сохраняются оба существующих режима ответа, где они поддерживаются:
обычный redirect/flash и JSON для API/enhanced UI.

### Карта доступа и риска изменяющих действий

| Действие | Требуемый доступ | Успешный ответ | Ошибки/защита | Destructive |
|---|---|---|---|---|
| User login/logout | Public / User session | redirect или JSON; cookie set/delete | rate limit, invalid/expired code | Нет |
| Feedback submit | User | redirect/flash или JSON | validation, expired session | Нет |
| Game create/action | User + chat | redirect/flash или JSON | membership, state, validation | Меняет активную игру |
| Chat triggers/aliases/settings | User + permission | redirect/flash или JSON | permission, validation, lock | Да, конфигурация чата |
| Economy actions | User + chat | JSON | feature disabled, write lock, balance/ownership | Да, экономика |
| Admin login/logout | Public / Admin session | redirect или JSON; отдельная admin cookie | rate limit, unsafe password, expiry | Нет |
| Backup request | Admin | redirect/flash или JSON | auth, backup failure | Нет, но чувствительно |
| Broadcast send/reaction | Admin | redirect/flash или JSON | auth, Telegram/API/media validation | Да, внешняя отправка/реакция |
| Feedback status | Admin | redirect/flash или JSON | auth, missing request | Да, рабочий статус |
| Generic DB update/delete | Admin | redirect/flash или JSON | auth, table/PK/type validation | **Да, прямое изменение БД** |

## Frontend-дубли, не подключённые к production Mini App router

Активный `frontend/src/app/router/AppRouter.tsx` публикует только Mini App routes:
home, groups, chat, games, gacha и more. Следующие деревья исходников существуют,
но не импортируются активным router:

- `frontend/src/pages/admin/**`;
- `frontend/src/pages/admin-login/**`;
- `frontend/src/pages/admin-table/**`;
- `frontend/src/pages/admin-broadcast/**`;
- `frontend/src/pages/docs/**`;
- desktop-oriented `frontend/src/widgets/app-shell/**`.

До удаления необходимо проверить не только прямые imports, но и тесты, CSS, shared
types/API helpers и build aliases. Удаление выполняется после завершения Jinja-паритета,
не в первом baseline-коммите.

## Текущий HTML/CSS/JavaScript debt baseline

| Метрика | Baseline 2026-08-15 | Правило до рефакторинга |
|---|---:|---|
| Jinja HTML-файлы | 21 | Новые страницы добавляются только с route/test/inventory entry |
| Строки Jinja/HTML | ~6 206 | Информационная метрика, не quality gate |
| Строки `panel.css` | ~6 145 | Не увеличивать монолит новыми page-блоками без плана extraction |
| Inline `style=` | 38 | Количество не должно расти |
| Шаблоны с inline `<script>` | 5 | Количество не должно расти |
| Inline script lines | ~1 942 | Количество не должно расти; extraction уменьшает baseline |
| Шаблоны с `<table>` | 3 | Новая product UI таблица требует mobile alternative |

## Обнаруженные prerequisites и риски

- [x] 2026-08-15: обнаружено отсутствие admin-session check на HTML GET редактора
  generic DB record. Добавлен регрессионный тест и проверка до загрузки записи;
  commit пока pending.
- [x] 2026-08-15: раздел `/app/docs/admin` классифицирован как публичная документация
  администраторов Telegram-групп, а не интерфейс системного администратора Selara.
  Внутренние system-admin процедуры в него добавлять нельзя.
- [x] 2026-08-15: общий logout админского shell указывал на пользовательский
  `/logout`. Исправлен на `/app/admin/logout` и защищён route-тестом; commit pending.
- [x] 2026-08-15: JavaScript конструктора рассылки вынесен из `admin.html` в
  пакетируемый `admin-broadcast.js`; модуль покрыт ESLint и Chromium-сценариями.
- [x] 2026-08-15: preview рассылки строится через whitelist поддерживаемых тегов,
  не переносит произвольные элементы/атрибуты в DOM и дублируется в confirm dialog.
- [ ] Полная история Telegram сейчас невозможна: архив содержит входящие group
  message snapshots, но не полный поток исходящих сообщений бота.
- [ ] Message media preview требует отдельного решения по Telegram `file_id`,
  кешированию, авторизации и fallback после истечения доступности файла.
- [ ] Большие inline scripts в `chat.html` и `games.html` тесно связаны с текущим DOM;
  DOM нельзя массово менять до сценарных Playwright-тестов.
