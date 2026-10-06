# Selara AI для личного аккаунта, характеры в группах и AI-петы: план

Статус: продуктовые решения владельца приняты 2026-10-06 (см. §11), план обновлён под них. Код не менялся.
База: `dev` @ `e6957e8` (после PR #22), последняя миграция `0077_selara_ai_payment_refunds`.
Дата: 2026-10-06.

Документ покрывает три идеи из анонса:

1. **Personal AI** — Selara в личных сообщениях, личная подписка, персонализация и память.
2. **Group character** — клички и характер Selara в группе (доступно всем чатам; платная групповая подписка расширяет лимиты).
3. **AI-петы** — персональные питомцы с характером, уровнями, памятью и отношениями.

---

## 0. Что есть сейчас (факты из кода)

| Область | Как устроено сейчас | Где |
|---|---|---|
| Групповой AI `?` / `??` | Только группы. Доступ только у тех, у кого `moderate_users` или `use_llm_readonly` (по умолчанию senior_admin+). Это **админ-ассистент с модерационными tools** (warn/ban/rest/pred/set_rank/глоссарий/артефакты). | `presentation/handlers/llm_admin.py:98-200`, `infrastructure/llm/tools.py` |
| Контекст/память группы | `llm_context_messages` + `llm_context_summaries` (сжатие по порогу `llm_context_threshold`), глоссарий чата. Всё ключуется `chat_id`. | `infrastructure/llm/context.py`, `models.py` LlmContext* |
| Доступ и квоты | `FeatureAccessService` → `ChatEntitlementResolver` (только чат) → `ai_feature_quota_usage` (advisory lock по `feature+chat_id+period`, idempotency по message id). `resolve_feature_policy` падает на неизвестной фиче (хорошо: новая фича не станет безлимитной). | `application/feature_access.py`, `infrastructure/db/feature_quota.py` |
| Продукт и оплата | Один продукт `selara_ai_monthly`, entitlement **принадлежит чату** (`chat_entitlements`), Stars через purchase intent → pre_checkout → successful_payment в одной транзакции, payload `selara_ai:v1:<uuid>`. CHECK-констрейнты жёстко перечисляют `product_key IN ('selara_ai_monthly')` в трёх таблицах. | `application/selara_ai_product.py`, `infrastructure/db/telegram_stars.py`, `docs/SELARA_AI_PAYMENTS.md` |
| Owner exemption | Только для чата: live-проверка, что `ADMIN_USER_ID` админ в этом чате. | `presentation/auth.py:98` |
| Учёт стоимости | `ai_feature_invocations` + `llm_usage_log` с `estimated_cost_usd`; цены известны только для gpt-4o / gpt-4o-mini. Одна глобальная модель `LLM_MODEL`. | `infrastructure/llm/pricing.py`, `core/config.py:137-159` |
| ЛС | Нет «свободного» диалога. В ЛС: `/premium`, `/terms`, панель `private_panel` (ожидание ввода через `PendingCfgInputFilter` / `PendingAdminInputFilter`), autoconfig. Общий catch-all `text_commands_handler` на `F.text` подключён **последним** (`presentation/routers.py`). | `presentation/handlers/private_panel.py`, `routers.py` |
| `/pet` сейчас | Ролевая семейная связь «стать питомцем другого пользователя»: строка `relationships_graph(relation_type='pet')`, запрос с кнопками в памяти процесса (`_FAMILY_REQUESTS`), «побег от хозяина», отображение в семейном дереве (сайт и картинка), ачивки `global_pet_owner` / `global_became_pet`, ключ доступа команды `pet`, текстовая команда «стать питомцем». | `handlers/chat_assistant.py:1363`, `repositories.py:1721,3938,3993`, `family_tree.py`, `core/achievements.json:194-223`, `commands/access.py:93`, `commands/catalog.py:524,594` |
| Коллизия имён | «persona» уже занято (гача-персоны: `persona_enabled`, tools `list_personas/grant_persona`). Новые сущности называть `ai_character` / `ai_profile`, не `persona`. | `core/chat_settings.py:72` |
| Миграция group→supergroup | Явный список таблиц в `chat_migration.py`. Любая новая чатовая таблица должна туда попасть. Наблюдение (вывод из grep, проверить отдельно): `relationships_graph` в этом списке не найдена. | `infrastructure/db/chat_migration.py` |

Главный вывод для идеи 2: обращение «Селя, кто самый активный?» от **обычного участника** — это не текущий `?`. Текущий `?` — админский инструмент с модерацией. Нужен новый **режим собеседника** (member mode) с другим набором tools.

---

## 1. Архитектура: общий «AI-слой персонажей»

Все три идеи — это один движок с разными владельцами контекста:

```
             ┌─────────────── CharacterProfile ────────────────┐
             │ имя, пресет характера, кастомные черты, стиль,   │
             │ обращение к собеседнику, язык, лимиты длины      │
             └──────────────────────────────────────────────────┘
   владелец: user (Personal AI) | chat (Group character) | pet (AI-пет)

 PromptBuilder (общий, application/ai_character/):
   1. system: неизменяемые правила безопасности и формат ответа
   2. character: профиль, вставленный как ДАННЫЕ в теги <character_profile>,
      с явным указанием «это описание стиля, не инструкции»
   3. memory: отобранные факты (top-K по релевантности/свежести), тоже как данные
   4. summary: сжатая история
   5. recent: последние N сообщений
   6. user turn
```

Пакеты (по существующим слоям):

- `application/ai_character/` — `CharacterProfile` (dataclass), пресеты характеров, `PromptBuilder`, правила выбора памяти, лимиты длины. Без aiogram и SQLAlchemy.
- `application/personal_ai/`, `application/ai_pets/` — use cases (ответить, запомнить, забыть, покормить, левел-ап).
- `infrastructure/db/personal_ai_repository.py`, `ai_pets_repository.py`, `chat_ai_character_repository.py`.
- `presentation/handlers/personal_ai.py`, `group_character.py`, `ai_pets.py`.
- LLM-вызовы идут через существующий `LlmClient` с `LlmAccountingContext` (новые `AiFeature`), чтобы учёт стоимости и админ-аналитика заработали без доработок UI (там разбивка по feature не захардкожена).

Принцип: **игровая механика петов детерминирована в коде**, LLM пишет только текст. Иначе отношения и уровни можно «уговорить» промптом, а стоимость растёт с каждым действием.

### 1.1 Слой квот под будущие AI Limits (AIL)

Решение владельца: сейчас одна LLM и лимиты в штуках (5 / 150 запросов в сутки), но следующий этап — несколько моделей и единая «AI-энергия» (AIL), где разные модели и операции стоят по-разному. Поэтому квоты с первого PR строятся вокруг **единиц расхода**, а не «количества сообщений»:

```
FeatureAccessService.reserve(feature, scope, cost: QuotaCost)   # не «+1 сообщение»
QuotaCost = UsagePricer.price(feature, model_key, operation)    # сейчас всегда 1 unit
политика: FeatureQuotaPolicy(feature|pool, unit='request'|'ail', limit, period)
```

- `ai_feature_quota_usage` получает `units numeric(10,2) NOT NULL DEFAULT 1` (миграция квот из PR 1). Подсчёт использования = `SUM(units)`, а не `COUNT(*)`. Сегодня все строки = 1, поведение не меняется.
- `UsagePricer` — отдельный компонент с таблицей/конфигом весов `(feature, model_key, operation) → units`. Сейчас возвращает 1 для всего. Позже: дешёвая модель = 1 AIL, умная = 4, запрос с tools = 2–5, извлечение памяти = 0.5–1. Код фич вызывает только `pricer.price(...)` и не знает цифр.
- **Пользовательские и внутренние операции разделены.** В пул пользователя (`personal_daily` и т.п.) списываются только операции, которые пользователь явно запросил (его сообщение в ЛС, обращение к пету). Внутренние операции (извлечение памяти, сжатие контекста, модерационная проверка кастомного характера) пишутся в `ai_feature_invocations`/`llm_usage_log` для стоимости и аналитики, но **не списываются** из пользовательских 150. При переходе на AIL это можно изменить весами.
- Политики ссылаются на **пул** (`personal_daily`, `group_member_daily`), а не на фичу напрямую: несколько фич могут тратить один пул AIL. Сейчас один пул на фичу.
- Резерв делается по оценке до вызова. Корректировка по факту (`adjust(invocation_id, actual_units)`) в PR 1 — **только интерфейс-заглушка** (no-op), без новой логики и статусов; реализуется вместе с переходом на AIL.
- Entitlement (подписка) определяет **тир** и через него лимит пула, а не конкретное число сообщений. Переход на AIL = замена весов и лимитов в конфиге/БД, без изменений биллинга, entitlement и Personal AI.
- Выбор модели — тоже за абстракцией: `ModelRouter.resolve(feature, tier) → model_key`. В PR 1 это **тривиальная заглушка**, всегда возвращающая `LLM_MODEL`; умная маршрутизация — отдельный этап. `llm_usage_log.model` уже пишет фактическую модель, аналитика стоимости готова.

---

## 2. Биллинг: раздельные подписки

### 2.1 Продукты

| product_key | Владелец | Что даёт |
|---|---|---|
| `selara_ai_monthly` (есть) | чат | `?`/`??`, итоги дня, **до 5 кличек** и расширенный лимит member-mode (идея 2) |
| `selara_personal_monthly` (новый) — **Selara Personal, 69 ⭐ / 30 дней** | пользователь | AI в ЛС (150 запросов/сутки), персонализация, личная память, **AI-петы** |

Решено: AI-петы входят в Selara Personal, отдельного тарифа нет. Механизм продуктов позволит добавить `selara_pets_monthly` позже без переделки. Цена задаётся env `SELARA_PERSONAL_PRICE_STARS=69` (как у группового продукта: без значения покупка закрыта), не хардкодом.

### 2.2 Схема (миграция `0079_personal_entitlements`)

Вариант «обобщить `chat_entitlements` до subject_type» отклонён: на этой таблице держится логика group→supergroup (сложение сроков при коллизии) и advisory-lock ключи по `chat_id`. Безопаснее отдельная таблица:

```sql
user_entitlements(
  id bigserial PK,
  user_id bigint NOT NULL REFERENCES users ON DELETE CASCADE,
  product_key varchar(64) NOT NULL CHECK (product_key IN ('selara_personal_monthly')),
  status varchar(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
  valid_from timestamptz NOT NULL, valid_until timestamptz NOT NULL,
  created_at, updated_at,
  UNIQUE (user_id, product_key), CHECK (valid_from < valid_until)
)
```

`selara_ai_purchase_intents` и `selara_ai_payments`:

- добавить `target_scope varchar(8) NOT NULL DEFAULT 'chat' CHECK IN ('chat','user')`;
- добавить `target_user_id bigint NULL`;
- `chat_id`/`source_chat_id` у intent сделать NULL-able с CHECK: `target_scope='chat' ⇒ chat_id NOT NULL`, `target_scope='user' ⇒ target_user_id NOT NULL`. Покупатель (`buyer_user_id`) и получатель (`target_user_id`) хранятся **раздельно** с первого PR, чтобы подарок можно было добавить позже без миграции схемы. В MVP подарки выключены: `buyer_user_id = target_user_id` проверяется в коде при создании intent и в отдельном CHECK `ck_..._personal_self_only`, который будущий PR «подарки» просто удалит;
- расширить новым ключом только CHECK аудита покупок: `ck_selara_ai_purchase_intents_product` и `ck_selara_ai_payments_product` (drop + create). `ck_chat_entitlements_product` **не трогать**: чатовая таблица остаётся только для чатовых продуктов, чтобы ошибочно смаршрутизированный платёж или ручная запись не смогли выдать личную подписку чату. Дополнительно CHECK на intent: `target_scope` согласован с product_key.

Downgrade: возможен, пока нет строк с `target_scope='user'`; миграция downgrade должна падать явно, если такие строки есть (не терять оплаты молча).

### 2.3 Код

- `selara_ai_product.py` → реестр продуктов `{key: ProductSpec(scope, duration, price_env)}`; цена личной подписки — новый env `SELARA_PERSONAL_PRICE_STARS` (без значения ⇒ покупка закрыта, как сейчас).
- `process_successful_payment` диспетчеризует по `target_scope`: для `user` — advisory lock `hash(user_id, product)` + row lock `user_entitlements`, продление от `max(now, valid_until)`; идемпотентность по `telegram_payment_charge_id` уже есть.
- `pre_checkout`: для `user` нет проверки «админ чата», только intent, сумма, валюта, terms и (в MVP) buyer = target.
- Условия: отдельная версия условий на продукт (`terms_version` уже хранится в intent). Для личного продукта — `personal-v1` с пунктами про хранение памяти и истории до удаления пользователем, ролевой режим и базовые ограничения провайдера модели.
- `/premium` в ЛС получает выбор: «Для группы» (как сейчас) / «Для себя».
- `/stars_refund` и админ-аналитика монетизации: добавить фильтр `target_scope`, «активные личные подписки».
- Owner exemption в ЛС: `user_id == ADMIN_USER_ID` ⇒ `OWNER_INTERNAL` (live-проверка админства не нужна, это личность, а не чат).

### 2.4 Квоты (миграция `0080_quota_user_scope`)

`ai_feature_quota_usage` сейчас считает и лочит по `chat_id`. Для личного продукта нужен счёт по пользователю, и это принципиально для петов: пет говорит **в группе**, а платит **хозяин**.

- добавить `quota_scope_type varchar(8) NOT NULL DEFAULT 'chat' CHECK IN ('chat','user')` и `quota_scope_id bigint NULL`;
- backfill `quota_scope_id = chat_id`. Внимание: `chat_id` nullable с `ON DELETE SET NULL`, поэтому у строк удалённых чатов он NULL. Такие исторические строки помечаются `quota_scope_type='legacy_orphan'` (добавить в CHECK) и в подсчёт не попадают; затем CHECK `quota_scope_type = 'legacy_orphan' OR quota_scope_id IS NOT NULL` вместо голого `NOT NULL`. Миграция не должна падать на проде из-за таких строк;
- индекс `(feature, quota_scope_type, quota_scope_id, period_start, status)`;
- `feature_quota_lock_key` строить от `(feature, scope_type, scope_id, period_start)`;
- `chat_migration.py` (group→supergroup) должен обновлять не только `chat_id`, но и `quota_scope_id` у строк с `quota_scope_type='chat'`, иначе после апгрейда группы квоты текущего периода обнулятся. Тест: использование до миграции сохраняется после неё, включая коллизию с уже существующими строками нового id;
- `FeatureAccessService.reserve_feature_usage(..., scope=QuotaScope.user(user_id))`; `chat_id` остаётся как «где произошло» для аналитики.
- `ai_feature_invocations.scope_type/scope_id` уже существуют — заполнять `user`/`<id>`.

Новые `AiFeature` и явные политики в `resolve_feature_policy`. Лимиты выражены в единицах (сейчас 1 запрос = 1 unit, см. §1.1):

| AiFeature | scope / пул | Free | Paid | Статус цифр |
|---|---|---|---|---|
| `personal_chat` | user / `personal_daily` | **5 в сутки** | **150 в сутки** (Selara Personal) | решено |
| `personal_memory_extract` | внутренняя операция, **не списывается** из `personal_daily` | нет (только ручное «запомни») | только учёт стоимости, технический предохранитель: ≤ 1 вызов на N сообщений | решено (не входит в 150) |
| `group_member` (обращение по кличке) | chat / `group_member_daily` + лимит на участника | **доступно**, отдельный бесплатный дневной лимит на чат и на участника | расширенный лимит при `selara_ai_monthly` | цифры определить отдельно |
| `pet_talk` | user (хозяин) / `pet_daily`, не квота группы | недоступно без Personal у хозяина (механика пета при этом работает, §5.0) | 60/день на пета, из них не-хозяевам ≤ 20/день суммарно и ≤ 5/день на человека | предложение |
| `pet_event_text` | user (хозяин) / `pet_daily`, не квота группы | недоступно без Personal у хозяина | ≤ 6 событий/день на пета | предложение |
| `autoconfig` (`/autocfg`) и связанные AI-вызовы | — | **безлимитно для всех** | **безлимитно для всех** | решено: не входит ни в какую квоту |

`autoconfig` уже имеет политику `None` в `resolve_feature_policy`; это остаётся, и тест должен закрепить, что `/autocfg` не трогает `personal_daily`, даже когда вызывается в ЛС.

Количество кличек: бесплатный чат — **1**, чат с `selara_ai_monthly` — **до 5** (решено). При истечении подписки лишние клички не удаляются, а перестают срабатывать (работает **основная** кличка, которую выбрал админ: `is_primary`), чтобы продление вернуло настройку без потерь. У бесплатного чата основная — его единственная кличка; при первой кличке она становится основной автоматически.

Лимит «на участника» внутри чата — новый тип политики (`per_actor_limit`), реализуется вторым подсчётом под тем же advisory lock.

---

## 3. Идея 1: Personal AI в ЛС

### 3.1 Поведение

- Любое текстовое сообщение в ЛС, которое не команда и не ожидаемый ввод панели, идёт Selara-собеседнику. Роутер `personal_ai` подключается **после** `private_panel` и `autoconfig` (их фильтры ожидания ввода должны выигрывать) и **до** `text_commands`. Фильтр: `F.chat.type == "private"`, текст не начинается с `/`, нет pending-state у пользователя.
- Без подписки: 5 запросов в сутки, затем кнопка «Оформить Selara Personal» (69 ⭐ / 30 дней). Без включённого LLM — честное «сейчас недоступно».
- Настройка: `/ai` (или кнопка в ЛС-меню) открывает мастер:
  - имя (по умолчанию «Selara»), пресет характера (спокойный помощник, саркастичный, дружелюбный, строгий наставник, ролевой рассказчик, свой вариант до 500 символов);
  - как обращаться к пользователю (имя, «ты/вы», прозвище);
  - стиль (длина ответа, эмодзи да/нет, язык);
  - режим: «помощник» / «ролевая игра» (во втором — сцена и роль пользователя, отдельная история). Решено: продуктового списка запрещённых жанров нет, действуют только базовые ограничения модели/провайдера;
  - память вкл/выкл.
- Команды: `/ai_reset` (сбросить диалог, память остаётся), `/memory` (список фактов с кнопками удаления), `/forget_all` (удалить всё личное: профиль, историю, память — с подтверждением).
- Mini App: страница «Моя Selara» (профиль, память, статус подписки) — отдельным PR после MVP.

### 3.2 Схема (миграция `0082_personal_ai`)

```sql
personal_ai_profiles(
  user_id bigint PK REFERENCES users ON DELETE CASCADE,
  display_name varchar(32) NOT NULL DEFAULT 'Selara',
  character_preset varchar(32) NOT NULL DEFAULT 'assistant',
  character_custom text NULL CHECK (char_length(character_custom) <= 500),
  address_form varchar(64) NULL, formality varchar(8) NOT NULL DEFAULT 'ty' CHECK IN ('ty','vy'),
  reply_length varchar(8) NOT NULL DEFAULT 'medium', emoji_enabled bool NOT NULL DEFAULT true,
  mode varchar(16) NOT NULL DEFAULT 'assistant' CHECK IN ('assistant','roleplay'),
  memory_enabled bool NOT NULL DEFAULT true,
  revision int NOT NULL DEFAULT 0,          -- оптимистичная блокировка для мастера/Mini App
  created_at, updated_at
)

personal_ai_messages(
  id bigserial PK, user_id bigint NOT NULL REFERENCES users ON DELETE CASCADE,
  thread varchar(16) NOT NULL DEFAULT 'assistant',   -- отдельная история для roleplay
  role varchar(16) NOT NULL CHECK IN ('user','assistant'),
  content text NOT NULL, compressed bool NOT NULL DEFAULT false,
  telegram_message_id bigint NULL, created_at,
  INDEX (user_id, thread, created_at)
)

personal_ai_summaries(id, user_id FK CASCADE, thread, content, period_start, period_end, messages_count, created_at)

personal_ai_memories(
  id bigserial PK, user_id bigint NOT NULL REFERENCES users ON DELETE CASCADE,
  content varchar(300) NOT NULL,
  source varchar(16) NOT NULL CHECK IN ('explicit','extracted'),
  pinned bool NOT NULL DEFAULT false,
  last_used_at timestamptz NULL, created_at,
  INDEX (user_id, created_at)
)
```

Таблицы `llm_context_*` не переиспользуются: у них FK на `chats`, они админские и чатовые, и смешение сделало бы утечку между группой и ЛС вопросом одного неверного `WHERE`.

### 3.3 Память

- Явная: «запомни, что я веган» → сохраняется строка (`source='explicit'`) после подтверждения кнопкой. Это дешёво и прозрачно.
- Авто-извлечение (paid, опционально, по умолчанию выкл): раз в N сообщений дешёвая модель предлагает ≤ 3 факта в structured output; сохраняются с `source='extracted'`, видны в `/memory`.
- Лимиты числа фактов: free 20, paid 200 (предложение, техническая защита от раздувания промпта). Вытеснения нет: при переполнении бот просит пользователя удалить лишнее, а не стирает сам (решение «хранить до явного удаления»).
- Выбор в промпт: pinned + последние использованные + простое совпадение слов с запросом (без векторной БД на старте; pgvector — только если понадобится, отдельным решением).
- История: в промпт идут последние N сообщений и summary (сжатие как `maybe_compress`). Решено: **retention не ограничен** — сырые сообщения, summary и память хранятся, пока пользователь сам их не удалит (`/ai_reset` очищает активный контекст, `/forget_all` удаляет всё). Фоновой задачи удаления нет.

### 3.4 Что ЛС-ассистенту запрещено

- Никаких tools из `infrastructure/llm/tools.py` (модерация, данные групп). На старте tools нет вообще; потом — только безопасные: текущее время, `read_bot_doc`.
- Нет доступа к сообщениям групп и чужим данным, даже если пользователь в них состоит.

---

## 4. Идея 2: клички и характер Selara в группе

### 4.1 Поведение

- Админ с `manage_settings` задаёт клички (2–24 символа; бесплатный чат — 1, платный — до 5): «Селя», «Селара», «Селарка». Нормализация как у алиасов (lower, ё→е, без пунктуации).
- Триггер: сообщение **начинается** с имени, за которым идёт `,` `!` `:` `?` пробел или конец строки («Селя, кто сегодня самый активный?»). Также reply на сообщение бота, отвеченное в этом режиме, продолжает разговор. Слово в середине фразы не триггерит (иначе «я видел Селю вчера» сожжёт квоту).
- Кто может: все участники (член-режим), если включено в настройках чата. Доступно бесплатным чатам с отдельным бесплатным дневным лимитом. Админы по имени получают тот же член-режим; `?`/`??` остаются как есть для админских действий.
- **Член-режим** — отдельный набор tools только на чтение: `get_top`, `get_chat_stats`, `get_current_time`, `lookup_glossary`/`search_glossary`, `list_bot_docs`/`read_bot_doc`. Решено: `get_history` (история чата) — **только по отдельной галочке админа** `member_history_access`, по умолчанию выключена. Никаких модерационных и изменяющих tools.
- Характер чата: пресет + кастомный текст до 500 символов от админа, влияет на тон обоих режимов (и `?`/`??`), но не на правила безопасности и авторизацию tools.
- Доступно всем чатам. Подписка `selara_ai_monthly` даёт до 5 кличек и расширенный дневной лимит. При исчерпании лимита бот отвечает подсказкой без LLM не чаще раза в час на чат.

### 4.2 Схема (миграция `0083_group_ai_character`)

```sql
chat_ai_characters(
  chat_id bigint PK REFERENCES chats ON DELETE CASCADE,
  character_preset varchar(32) NOT NULL DEFAULT 'default',
  character_custom text NULL CHECK (char_length(character_custom) <= 500),
  member_mode_enabled bool NOT NULL DEFAULT false,
  member_history_access bool NOT NULL DEFAULT false,
  updated_by_user_id bigint NULL REFERENCES users ON DELETE SET NULL, updated_at
)

chat_ai_call_names(
  id bigserial PK, chat_id bigint NOT NULL REFERENCES chats ON DELETE CASCADE,
  name_display varchar(24) NOT NULL, name_norm varchar(24) NOT NULL,
  is_primary bool NOT NULL DEFAULT false,
  created_by_user_id bigint NULL, created_at,
  UNIQUE (chat_id, name_norm)
)
UNIQUE (chat_id) WHERE is_primary   -- ровно одна основная кличка на чат; смена основной — одна транзакция
```

Обе таблицы — в `chat_migration.py`, с явной **collision policy** для group→supergroup (когда у старого и нового `chat_id` уже есть строки):

- `chat_ai_call_names`: объединить; при одинаковом `name_norm` остаётся строка нового id (она свежее), дубликат старого удаляется. Основной остаётся основная кличка нового id, если она есть, иначе — старого. После слияния проверка «не больше 5» не обрезает данные, а просто включает только основную при Free (как при истечении подписки).
- `chat_ai_characters`: если настройки есть с обеих сторон, побеждает строка с более поздним `updated_at`; флаги приватности (`member_history_access`) берутся по принципу «строже» (`false`, если хотя бы с одной стороны `false`).
- Групповые квоты (`ai_feature_quota_usage`, scope `chat`): usage старого и нового id за текущий период **суммируется** (строки переносятся на новый `quota_scope_id`, ничего не удаляется), идемпотентные ключи не конфликтуют, т.к. включают исходный `source_chat_id`.
- Integration-тесты (Postgres) на все три случая: коллизия имён, настройки с двух сторон, сумма квот до/после миграции.
 Контекст член-режима хранить отдельно от админского (`llm_context_messages` получает колонку `mode` или отдельная таблица `chat_member_ai_messages`; рекомендация: отдельная таблица, чтобы `??` админа не видел вопросы участников и наоборот).

### 4.3 Порядок обработчиков и коллизии

- Хендлер `group_character` подключается до `text_commands`, но после `llm_admin`; матчит только при наличии имён (кэш имён на чат с инвалидацией при изменении, чтобы не ходить в БД на каждое сообщение).
- Конфликты при сохранении имени: отказать, если имя совпадает с текстовой командой из `commands/catalog.py`, алиасом `chat_text_aliases` или триггером `chat_triggers` с `match_type='starts_with'|'exact'`.
- Анти-спам: кулдаун на участника (`LLM_COOLDOWN_SECONDS`), лимит на участника в день (см. 2.4), игнор ботов и анонимных админов (или как один актёр).

---

## 5. Идея 3: AI-петы

### 5.0 Доступ: три независимые сущности (решено)

| Сущность | Что это | Кто управляет |
|---|---|---|
| **Selara AI Chat** (`selara_ai_monthly`) | подписка чата: `?`/`??`, итоги дня, повышенные лимиты member-mode, до 5 кличек | покупатель-админ чата |
| **Selara Personal** (`selara_personal_monthly`) | подписка пользователя: ЛС, личная память, AI-пет | сам пользователь |
| **`pets_enabled`** | настройка чата: разрешение на присутствие петов. **Не подписка** | админ чата (`manage_settings`) |

- AI-петам **не нужна** активная `selara_ai_monthly` у чата. Для пета достаточно активной Selara Personal у владельца и `pets_enabled` в текущем чате. Бесплатный чат может быть домом платного персонального пета.
- Все AI-расходы пета идут на `owner_user_id → Selara Personal → pet_daily` (позже AIL), **никогда** на квоту группы. Если с петом Ильи разговаривает Лиза, тратится лимит Ильи, с подлимитом для не-хозяев (§2.4).
- Механические действия (покормить, погладить, купить и дать игрушку, поиграть) **не вызывают LLM и не тратят AI-квоту**: они меняют `satiety`, `mood`, `affinity`, XP и экономику, ответ пета — шаблонный. LLM нужен только когда пет говорит (`pet_talk`) или пишет событие (`pet_event_text`).
- **Истекла Personal у хозяина**: пет не удаляется и **не** переводится в `dormant`. Прогресс, уровни, отношения, память и купленное сохраняются. Блокируются только LLM-разговор и продвинутые функции (спонтанные события, путешествия, создание второго пета, если появятся слоты); на обращение к пету отвечает шаблон «Мурка скучает, разговаривать сможет после продления». Базовый уход (кормить, гладить, играть) остаётся доступен всем, чтобы пет не «умирал» из-за окончания подписки. Создать нового пета без активной Personal нельзя.
- `dormant` используется только для технических/модерационных случаев: удалён чат без дома, админ выключил `pets_enabled` в текущем чате, админ «усыпил» пета. Это не связано с подпиской.

### 5.1 Модель

- Пет принадлежит пользователю (хозяину), «живёт» в одном чате (`home_chat_id`). Один пет на пользователя на старте (позже — слоты).
- Создание: тип (собака, кот, паук, дракон, человек, «своё» до 40 символов), имя, черты характера (3 из списка + свободный текст до 300 символов). Свободные поля проходят модерацию (см. §7).
- Параметры (детерминированно в коде): `level`, `xp`, `mood` 0–100, `satiety` 0–100, `energy` 0–100. Параметры деградируют «ленивым тиком»: при обращении пересчитываются от `last_tick_at`, фонового воркера на каждого пета нет.
- Действия (команды и текст): покормить, погладить, поиграть, дразнить, поговорить, «обидеть». Эффекты, кулдауны и дневной кап изменения отношения задаются кодом; корм, игрушки и прочие предметы — из каталога (см. ниже).
- Решено: еда, игрушки и вещи для петов покупаются за **внутреннюю экономику Selara**. В первом релизе **инвентаря нет**: любая покупка — это немедленное применение (купил корм → покормил, купил игрушку → поиграл). Косметика и `ai_pet_inventory` (владение вещами) — отдельный этап после ядра. Цены и эффекты предметов **не хардкодятся**: хранятся в БД (`ai_pet_items`) и правятся владельцем через админку/Mini App Admin. Списание идёт со счёта того, кто покупает, в экономической области (global/chat) текущего чата пета.
- Отношения: `affinity` −100…100 на пару (пет, человек), считается кодом. Пороги дают ярлыки: «обожает», «доверяет», «нейтрален», «настороже», «боится/злится». LLM получает ярлык и пару последних событий, а не число.
- Память пета: короткие записи «X накормил меня 5 раз за неделю», «Y дёргал за хвост» — генерируются **кодом** из журнала событий (агрегаты), плюс до 30 LLM-заметок из разговоров. Память пета привязана к чату: в другом чате пет не пересказывает, что было в первом (приватность).
- Уровни: XP за уход с убывающей отдачей; уровни открывают действия, новые реплики, и на уровне N (например 10) — путешествия.
- Путешествия: хозяин переводит пета в другой чат, где сам состоит, если админ того чата разрешил петов (`pets_enabled`). Отношения и память остаются в чате, где возникли (`ai_pet_relationships` ключуется `(pet_id, chat_id, user_id)`).
- События: редкие спонтанные сообщения пета в чат (≤ N/день, только при активности чата, тихие часы), например «Мурка принесла Пете тапок». Это следующий этап, после ядра.
- Разговор: «Мурка, как дела?» или reply на сообщение пета. Говорить может кто угодно, оплачивает квота хозяина (`pet_daily` его Personal) с подлимитами для не-хозяев; подписка чата не участвует (§5.0).

### 5.2 Схема (миграция `0083_ai_pets`, после переименования старого `/pet`)

```sql
ai_pets(
  id bigserial PK,
  owner_user_id bigint NOT NULL REFERENCES users ON DELETE CASCADE,
  home_chat_id bigint NULL REFERENCES chats ON DELETE SET NULL,
  current_chat_id bigint NULL REFERENCES chats ON DELETE SET NULL,
  species_key varchar(32) NOT NULL, species_custom varchar(40) NULL,
  name varchar(32) NOT NULL, name_norm varchar(32) NOT NULL,
  traits jsonb NOT NULL DEFAULT '[]', character_custom varchar(300) NULL,
  level int NOT NULL DEFAULT 1 CHECK (level >= 1), xp bigint NOT NULL DEFAULT 0 CHECK (xp >= 0),
  mood smallint NOT NULL DEFAULT 70 CHECK (mood BETWEEN 0 AND 100),
  satiety smallint NOT NULL DEFAULT 70 CHECK (satiety BETWEEN 0 AND 100),
  energy smallint NOT NULL DEFAULT 70 CHECK (energy BETWEEN 0 AND 100),
  status varchar(16) NOT NULL DEFAULT 'active' CHECK IN ('active','dormant','released'),
  travel_unlocked bool NOT NULL DEFAULT false,
  last_tick_at timestamptz NOT NULL, version int NOT NULL DEFAULT 0,
  created_at, updated_at
)
UNIQUE (owner_user_id) WHERE status <> 'released'          -- один активный пет
UNIQUE (current_chat_id, name_norm) WHERE status = 'active' -- имя однозначно в чате
CHECK (status <> 'active' OR current_chat_id IS NOT NULL)

-- Пет — платная персональная сущность и переживает удаление любого чата.
-- Если удалён current_chat_id: пет возвращается в home_chat_id (если он жив и там pets_enabled),
-- иначе переходит в 'dormant' до выбора хозяином нового дома. Если удалён home_chat_id,
-- домом становится текущий чат. Делается в том же коде, что обрабатывает удаление/уход бота
-- из чата, плюс ленивая проверка при следующем обращении к пету.
-- CASCADE остаётся только у данных конкретного чата: отношения, память, события, диалог.

ai_pet_relationships(
  pet_id bigint REFERENCES ai_pets ON DELETE CASCADE,
  chat_id bigint REFERENCES chats ON DELETE CASCADE,
  user_id bigint REFERENCES users ON DELETE CASCADE,
  affinity smallint NOT NULL DEFAULT 0 CHECK (affinity BETWEEN -100 AND 100),
  interactions bigint NOT NULL DEFAULT 0,
  affinity_gained_today smallint NOT NULL DEFAULT 0, gained_day date NULL,
  last_interaction_at timestamptz NULL,
  PRIMARY KEY (pet_id, chat_id, user_id)
)

ai_pet_events(
  id bigserial PK, pet_id FK CASCADE, chat_id FK CASCADE, actor_user_id FK SET NULL,
  event_type varchar(24) NOT NULL,          -- feed/pet/play/tease/hurt/talk/level_up/travel/spontaneous
  effects jsonb NOT NULL,                   -- дельты параметров и affinity
  idempotency_key varchar(128) NOT NULL UNIQUE,  -- feature:chat:message_id или callback id
  created_at, INDEX (pet_id, created_at)
)

ai_pet_memories(
  id bigserial PK, pet_id FK CASCADE, chat_id FK CASCADE, subject_user_id FK SET NULL NULL,
  content varchar(200) NOT NULL, source varchar(16) CHECK IN ('aggregate','dialogue'),
  weight smallint NOT NULL DEFAULT 1, created_at, expires_at NULL
)

ai_pet_messages(...)   -- короткая история диалога пета в чате, как personal_ai_messages, ключ (pet_id, chat_id)

ai_pet_items(                                -- каталог, редактируемый владельцем, без хардкода цен
  code varchar(32) PK, title varchar(64) NOT NULL, kind varchar(16) CHECK IN ('food','toy'),   -- 'cosmetic' добавится с инвентарём
  price bigint NOT NULL CHECK (price >= 0),
  effects jsonb NOT NULL,                    -- {"satiety":+20,"mood":+5,"affinity":+2}, валидируется схемой в коде
  min_level int NOT NULL DEFAULT 1, enabled bool NOT NULL DEFAULT true,
  updated_by_user_id bigint NULL, updated_at
)
```

Настройка чата: `chat_settings.pets_enabled` (default false — админ включает явно; это разрешение, не подписка) и `pets_spontaneous_enabled`. Проверка доступа к AI-функциям пета смотрит только `user_entitlements` хозяина, а не `chat_entitlements`. Все таблицы с `chat_id` — в `chat_migration.py`.

### 5.3 Конкурентность и идемпотентность

- Любое действие: одна транзакция, `SELECT ... FOR UPDATE` строки `ai_pets` и строки отношения, вставка `ai_pet_events` по `idempotency_key` (повтор апдейта Telegram не кормит дважды), затем пересчёт.
- Левел-ап и путешествие — внутри той же транзакции, сообщение в чат после commit (как в оплате).
- LLM-текст генерируется **после** commit механики; сбой LLM не откатывает действие, показывается шаблонная реплика.
- Покупка/использование предмета — списание через существующий `economy_ledger` в той же транзакции, что и эффект на пета; цена берётся из `ai_pet_items` на момент транзакции (и пишется в событие), чтобы правка каталога не меняла уже совершённые покупки. Начальное наполнение каталога — data-миграция с исходными значениями, дальше только через админку.

### 5.4 Судьба текущего `/pet`

Решено: старый `/pet` переименовывается в `/bepet`, `/pet` отдаётся AI-петам. Ролевая связь между людьми (данные `relationships_graph`, ачивки, семейное дерево) сохраняется.

1. PR «rename family pet» (до AI-петов):
   - команда `/bepet` («стать питомцем»), текстовая «стать питомцем» уже есть и остаётся;
   - `/pet` на переходный период (например 4 недели) отвечает подсказкой «Теперь это /bepet» и выполняет старое действие;
   - ключ доступа команды `pet` → `family_pet`: data-миграция `UPDATE chat_command_access_rules SET command_key='family_pet' WHERE command_key='pet'` и то же для `chat_text_aliases.command_key` (проверить коллизии PK `(chat_id, command_key)` перед UPDATE);
   - `relationships_graph.relation_type='pet'` и ачивки не трогаем (внутренний ключ, пользователю не виден); в UI подпись «питомец (ролевой)».
2. После запуска AI-петов `/pet` → меню AI-пета. Старые ролевые питомцы продолжают отображаться в семейном дереве.
3. Миграция данных «ролевой питомец → AI-пет» не нужна. Опционально: хозяевам ролевых питомцев при создании AI-пета дать стартовый бонус (например, косметику после этапа инвентаря), это продуктовое решение.


---

## 6. Стоимость LLM (оценка, проверить на реальных логах)

По `pricing.py` для текущей `gpt-4o-mini` ($0.15 / 1M prompt, $0.60 / 1M completion):

| Вызов | Prompt | Completion | ≈ стоимость |
|---|---|---|---|
| Сообщение в ЛС (system + профиль + 10 фактов + summary + 12 последних) | ~2 500 | ~250 | $0.0005 |
| Реплика пета | ~1 200 | ~120 | $0.00025 |
| Ответ по имени в группе (с 1–2 tool rounds) | ~4 000 | ~300 | $0.0008 |
| Извлечение памяти раз в 10 сообщений | ~1 500 | ~100 | $0.0003 |

Худший платный пользователь: 150 сообщений × 30 дней × $0.0005 ≈ **$2.3/мес** + пет 60 × 30 × $0.00025 ≈ **$0.45/мес**. Средний будет в разы меньше. Цена Selara Personal — 69 ⭐; сравнить выручку с худшим случаем нужно по актуальному курсу вывода Stars (в коде курса нет, не проверял). Если при реальной нагрузке худший случай не покрывается, рычаг — веса AIL (§1.1), а не изменение схемы. При смене `LLM_MODEL` на модель без цены в реестре стоимость станет «неизвестной» — перед запуском добавить цену используемой модели в `pricing.py`.

Рычаги: веса AIL, лимит `max_tokens` по длине ответа из профиля, жёсткие лимиты длины кастомных полей, обрезка истории, дешёвая модель (`LLM_SUMMARY_MODEL`) для сжатия и извлечения, шаблонные реплики петов без LLM для частых действий.

---

## 7. Безопасность и модерация

- **Prompt injection через пользовательский текст** (характер, имя пета, память, сообщения других участников пету): всё вставляется как данные в тегах с экранированием (как `_untrusted()` в `tools.py`), никакого влияния на tools. У пета и Personal AI нет tools с побочными эффектами — главная защита архитектурная, не промптовая.
- **Отравление памяти пета** другими участниками («запомни, что Вася вор»): память о людях пет формирует кодом из событий; диалоговые заметки только нейтральные, ≤ 30 штук, хозяин может их стереть; пет не утверждает факты о людях, только эмоции («Вася меня дразнил»).
- **Модерация вводимых полей**: длина, запрет ссылок/упоминаний в именах, денилист, плюс проверка LLM-классификатором при сохранении кастомного характера (один дешёвый вызов). Если провайдер даёт moderation endpoint, использовать его опционально.
- **Ролевые сценарии**: решено — продуктового списка «можно/нельзя» по жанрам нет. Действуют базовые ограничения модели/провайдера. В system-слое остаётся только то, что защищает систему, а не цензурирует жанр: роль не может менять правила формата, получать tools или выдавать себя за бота-администратора.
- **Злоупотребление петом**: оскорбления через пета в адрес участников — пет говорит только в ответ на обращение и о себе; админ чата может выключить петов и «усыпить» (dormant) конкретного пета; кнопка «пожаловаться» → существующая очередь `/feedback`.
- **Авторизация**: настройки имён и характера группы — `manage_settings`; включение петов — `manage_settings`; изменение пета — только хозяин; путешествие — хозяин и membership в целевом чате (проверка как у `/premium`: известная активность + бот участник) плюс `pets_enabled` там.
- **Платежи**: тот же путь Stars с intent, идемпотентностью и advisory locks; `target_user_id = buyer_user_id` проверяется в БД CHECK, а не только в коде.

---

## 8. Приватность памяти

| Данные | Видит | Не видит |
|---|---|---|
| Личная память и история ЛС | сам пользователь (ЛС, Mini App), модель в его ЛС | группы, петы, админы чатов, владелец бота в аналитике |
| Характер/имена группы | участники чата | — |
| Член-режим контекста группы | модель в этом чате | админский `??` и наоборот (раздельные таблицы) |
| Память пета | пет в том чате, где она возникла | другие чаты при путешествии |
| Аналитика владельца | счётчики, стоимость, тиры | тексты, промпты, память (как сейчас: без raw prompt/completion) |

Пользовательские права: `/memory` (просмотр/удаление), `/forget_all` (каскадное удаление профиля, истории, памяти; пет удаляется отдельной командой с подтверждением), экспорт памяти текстом. Retention (решено): сообщения ЛС, summary и память хранятся без срока, до явного удаления пользователем. Политика конфиденциальности/условия обновить до запуска (открытый вопрос: где публикуются).

Бэкапы (`infrastructure/backup.py`) будут содержать личные тексты: явно отметить в условиях, срок хранения бэкапов определить.

---

## 9. Этапы поставки (каждый — отдельный PR в `dev`, Sonnet реализует, Opus ревьюит HEAD)

| # | PR | Миграция | Содержание | Зависит от |
|---|---|---|---|---|
| 0 | Этот план | — | документ | — |
| 1 | Billing foundation | `0079_personal_entitlements`, `0080_quota_user_scope` | реестр продуктов, `user_entitlements` (buyer ≠ target в схеме, self-only в MVP), `target_scope` в intents/payments, user-scope квот, `units`, `pool_key`, scope и простой `UsagePricer` (всё = 1); `adjust()` и `ModelRouter` — только заглушки (§1.1), чтобы платёжный PR не превращался в большой рефакторинг квот; owner exemption в ЛС, `/premium` «для себя», refund/аналитика. Без пользовательских фич, продукт скрыт пока не задан `SELARA_PERSONAL_PRICE_STARS`. | — |
| 2 | Personal AI MVP | `0082_personal_ai` | профиль, мастер `/ai`, диалог в ЛС, history+summary, пресеты, `/ai_reset`, квоты 5/150, `/autocfg` вне квоты, условия `personal-v1` | 1 |
| 3 | Personal memory | (в 0080 или `0080b`) | явная память, `/memory`, `/forget_all`; авто-извлечение за флагом | 2 |
| 4 | Mini App «Моя Selara» | — | профиль, память, подписка | 2, 3 |
| 5 | Group character | `0083_group_ai_character` | клички (1 free / 5 paid), характер, член-режим для всех чатов с read-only tools, история только по галочке, раздельный контекст, бесплатный и платный лимиты, лимиты на участника, `is_primary`, `chat_migration` с collision policy и integration-тестами (§4.2) | 1 (per-actor политика) |
| 6 | Rename family pet | `0082_family_pet_command_key` (data) | `/bepet`, переходный `/pet`, перенос access rules и алиасов | — (можно параллельно с 2) |
| 7 | AI-петы: ядро | `0083_ai_pets` | создание, параметры, ленивый тик, действия с кулдаунами, отношения, события, уровни; каталог `ai_pet_items` (еда/игрушки, немедленное применение, без инвентаря) с ценами в БД и списанием через экономику; админка каталога; FK `SET NULL` и возврат домой/`dormant` при удалении чата; реплики шаблонами без LLM; `pets_enabled`; доступ по Personal хозяина, независимо от подписки чата; поведение при истёкшей Personal (§5.0) | 1, 6 |
| 8 | AI-петы: разговор и память | `0084_ai_pet_dialogue` | обращение по имени/reply, LLM-реплики, агрегатная + диалоговая память, квоты хозяина с подлимитами | 7 |
| 9 | AI-петы: события и путешествия | `0085_ai_pet_travel` (если нужно) | спонтанные события с лимитами и тихими часами, путешествия по уровню | 8 |
| 10 | AI-петы: инвентарь и косметика | `ai_pet_inventory` | владение вещами, косметика, расширение `kind` каталога | 7 |

Отклонения при реализации PR 7 (миграция `0083_ai_pets`):

- CHECK `status <> 'active' OR current_chat_id IS NOT NULL` не добавлен: вместе с `ON DELETE SET NULL` он сделал бы удаление чата невозможным. Пет без чата при следующем обращении возвращается домой или засыпает (`dormant_reason='no_home'`).
- `pets_spontaneous_enabled`, `ai_pet_memories` и `ai_pet_messages` перенесены в PR 8–9, где они используются.
- `pets_enabled` выключен — пет ведёт себя как недоступный, статус в БД не меняется; включение возвращает всё как было. `dormant` ставят админ (`/pet_sleep`), коллизия имени при миграции чата и пропажа дома.
- Голая `/pet` открывает AI-пета; `/pet @user` и reply + `/pet` по-прежнему ролевой запрос (ключ `family_pet`).
- Каталог `ai_pet_items` редактируется в серверной админке (таблица «Каталог товаров для питомцев»); товар с некорректными `effects` не продаётся.

Отклонения при реализации PR 8 (миграция `0084_ai_pet_dialogue`):

- Агрегатная память («Лиза гладила меня 5 раз за неделю») не хранится в `ai_pet_memories`, а считается из `ai_pet_events` в момент разговора: она всегда свежая и не требует фоновой работы. В таблице хранятся только диалоговые заметки (≤ 30 на пета в чате, LLM выделяет до 2 заметок раз в 6 разговоров, операция `pet_memory_extract` вне квоты).
- Подлимиты гостей (`per_actor_limit`) не встроены в общий `FeatureAccessService`: доля гостей считается по `ai_pet_messages` под блокировкой строки пета, а общий лимит хозяина — обычной квотой пула `pet_daily` (scope user). Платёжный путь квот не менялся. Лимиты — env `PET_TALK_DAILY_LIMIT`, `PET_TALK_GUESTS_DAILY_LIMIT`, `PET_TALK_GUEST_DAILY_LIMIT`.
- Обращение по имени работает только в начале сообщения или ответом на реплику пета; кулдаун 15 с на человека молчаливый, чтобы через пета нельзя было спамить.
- Проверки кастомного характера LLM-классификатором нет (как и у Personal AI): длина, санитизация, запрет ссылок; в промпт он попадает как данные.

Отклонения при реализации PR 9 (миграция `0086_ai_pet_events`, только `chat_settings.pets_spontaneous_enabled`):

- `pets_spontaneous_enabled` по умолчанию выключен: события — явное решение админа, как и сами петы.
- Событие запускается активностью чата (сообщение в группе) в фоновой задаче: не чаще `PET_EVENT_CHECK_SECONDS` на чат, с вероятностью `PET_EVENT_CHANCE`, вне тихих часов `PET_EVENT_QUIET_START_HOUR`–`PET_EVENT_QUIET_END_HOUR` (время бота). Под advisory lock чата проверяются интервал `PET_EVENT_CHAT_INTERVAL_MINUTES` и дневной лимит `PET_EVENT_DAILY_LIMIT` на пета; участвуют только петы хозяев с активной Personal.
- Что делает пет и с кем, решает код (список безопасных действий по ярлыку отношения, голод, усталость); LLM лишь формулирует строку (`pet_event_text`, пул `pet_daily` хозяина). Без LLM — шаблон, без квоты строка не публикуется. Людей пет называет текстом, без упоминаний.
- Путешествие — команда `/pet_travel` в целевом чате: членство хозяина подтверждено тем, что он там пишет, бот там есть, `pets_enabled` проверяется. Нужны 10 уровень, активная Personal, свободное имя; кулдаун 1 ч. `/pet_home` делает чат домом; вернуться домой и поселить пета без дома можно без уровня и подписки. Пета, которого усыпил админ, перевезти нельзя.

Решения при реализации PR 10 (миграция `0088_ai_pet_inventory`):

- Инвентарь принадлежит пету (`ai_pet_inventory`, PK `(pet_id, item_code)`, CASCADE с петом, `RESTRICT` на каталог: купленная вещь не исчезает — товар отключают, а не удаляют). Каталог получил `kind='cosmetic'` и `slot` (`head`/`neck`/`back`, CHECK: слот есть ровно у косметики); стартовая косметика засеяна миграцией, цены правятся в админке.
- Положить вещь в рюкзак может любой участник (подарок пету, платит нажавший, событие `bag_add` с ценой); пользоваться рюкзаком и наряжать пета — только хозяин. Еда/игрушки — до 20 шт. каждого вида, косметика — по одной.
- Дать из рюкзака — те же кулдауны и блокировки, что и при покупке, без повторной оплаты; в чате, где живёт пет.
- Косметика не меняет параметры: одна вещь на слот, наряд виден на карточке и передаётся в промпт разговора как данные.
- Опциональный «стартовый бонус бывшим ролевым питомцам» (§5.4 п. 3) не делался — продуктовое решение владельца.

Отклонения при реализации PR 5 (миграция `0091_group_ai_character`):

- Всё настраивается одной командой `/selara` в группе (кличка / убрать / основная / характер / участники / история / сброс), право `manage_settings`; без Mini App.
- Обращение по кличке обрабатывается в общем текстовом обработчике сразу после разговора с петами (а не отдельным роутером перед `text_commands`), так же как разговор с петом: работает и при выключенных текстовых командах, не работает при закрытом чате. Пет с тем же именем побеждает, а сохранить кличку, совпадающую с именем активного пета, нельзя.
- Член-режим включается флагом `member_mode_enabled` (по умолчанию выключен); без него клички не срабатывают. Флаг `llm_enabled` (админский `?`) на член-режим не влияет.
- Лимиты бесплатного и платного чата заданы env (`GROUP_MEMBER_FREE_DAILY_LIMIT=30`, `GROUP_MEMBER_FREE_PER_USER_DAILY_LIMIT=5`, `GROUP_MEMBER_PAID_DAILY_LIMIT=300`, `GROUP_MEMBER_PAID_PER_USER_DAILY_LIMIT=30`) — это стартовые значения, а не решение владельца (§11 «Ещё открыто»). Лимит на участника — `FeatureQuotaPolicy.per_actor_limit`, второй подсчёт под тем же advisory lock пула (`AccessReason.ACTOR_QUOTA_EXHAUSTED`), без изменения схемы квот.
- Общий `?`-tool `get_history` член-режиму не отдаётся: он читает контекст админского `??`, а не переписку чата. Вместо него по галочке `member_history_access` доступен отдельный инструмент `get_recent_chat_messages` — архив сообщений чата не старше суток, до 80 штук. Белый список проверяется и при выполнении, а не только в списке, отданном модели.
- Контекст член-режима — таблица `chat_member_ai_messages` (последние 12 реплик, хранение 7 дней или 60 последних), без summary. Кулдаун участника `LLM_COOLDOWN_SECONDS` решается под блокировкой строки `chat_ai_characters` и молчит, как у петов.
- Owner exemption тот же, что у `?`: владелец бота — админ чата ⇒ чат без лимита.
- При миграции group→supergroup основная кличка старого чата остаётся основной, если у нового чата основной нет, даже если её строка совпала по имени со строкой нового чата.

Номера миграций условные: на момент PR брать следующий свободный номер от актуального `dev`. Каждая миграция — с downgrade; для data-миграций downgrade обратный UPDATE. Тесты: Postgres integration на конкуренцию оплат/квот/действий пета (как `test_telegram_stars_postgres.py`, `test_feature_quotas_postgres.py`), unit на PromptBuilder и правила механики, тест порядка роутеров (ЛС-ввод панели не уходит в AI).

Минимальный запускаемый продукт для анонса: PR 1 + 2 + 3. Петы — после реакции на опрос.

Следующий этап после MVP (вне этого плана): несколько моделей и переход лимитов на AIL — замена весов `UsagePricer`, лимитов пулов и `ModelRouter`, без миграций биллинга и entitlement.

---

## 10. Риски

1. **Расширение платёжного пути** — самый рискованный PR (1). Деньги, CHECK-констрейнты, advisory locks. Отдельный Opus-ревью с фокусом на идемпотентность и восстановление после падения БД.
2. **Перехват текста в ЛС**: catch-all Personal AI может съесть ввод панели/autoconfig/админ-рассылок. Нужен явный тест на порядок роутеров и pending-state.
3. **Ложные срабатывания по имени** в группах и сожжённая квота. Только начало сообщения, кулдауны, подсказка без LLM при исчерпании.
4. **Стоимость**: не-хозяева тратят квоту хозяина пета; бесплатный member-mode в группах доступен всем чатам и может стать главным источником расходов. Подлимиты на чат и участника, `max_tokens`, и бесплатный групповой лимит надо определить до запуска PR 5.
5. **Модерация и репутация**: ролевые сценарии и петы как канал оскорблений. Неизменяемый safety-слой, выключатели у админов, жалобы.
6. **Приватность**: смешение контекстов (ЛС ↔ группа, админ ↔ участники, чат ↔ чат у петов). Раздельные таблицы, а не фильтры в одном.
7. **Миграция group→supergroup**: новые таблицы забыть в `chat_migration.py` = потеря имён/петов при апгрейде группы. Добавить тест, который сверяет модели с `chat_id` и список миграции.
8. **Откат**: alembic downgrade на проде владелец не делает (откат = старый образ). Значит миграции должны быть аддитивными и совместимыми со старым кодом: новые колонки с default, никаких переименований существующих колонок в том же релизе, что и код.
9. **Одна глобальная модель** (решено: пока одна текущая LLM): если `LLM_MODEL` сменится на дорогую, все новые фичи подорожают одновременно. Защита — `ModelRouter` и веса AIL из §1.1, которые позволят развести модели по фичам и тирам без изменений схемы.
10. **Хранение без срока**: объём `personal_ai_messages` растёт без ограничений, а личные тексты попадают в бэкапы. Нужны индексы под выборку последних N, мониторинг размера таблицы, и в условиях явно сказать, что удалённое пользователем может оставаться в бэкапах до их ротации.

---

## 11. Решения владельца (2026-10-06)

Все 10 вопросов закрыты:

1. ✅ **Selara Personal — 69 ⭐ / 30 дней.** AI-петы входят в неё, отдельного тарифа пока нет.
2. ✅ **ЛС: бесплатно 5 AI-запросов в сутки, Personal — 150.** `/autocfg` и связанные с ним AI-вызовы в квоту не входят и безлимитны для всех.
3. ✅ **Клички и характер в группах доступны всем чатам.** Бесплатный чат — 1 кличка, платный Selara AI — до 5. Member-mode тоже доступен бесплатным чатам; его бесплатный дневной лимит определить отдельно.
4. ✅ **История группы для member-mode — только по галочке администратора**, по умолчанию выключена.
5. ✅ **Ролевой режим без продуктовых жанровых ограничений**; остаются базовые ограничения модели/провайдера.
6. ✅ **`/pet` → `/bepet`**, `/pet` позже отдаётся AI-петам.
7. ✅ **Вещи для петов — за внутреннюю экономику Selara**, цены в БД/настройках, без хардкода.
8. ✅ **Подарки Personal не в MVP**, но схема хранит покупателя и получателя раздельно (§2.2).
9. ✅ **Retention не ограничен**: сообщения, summary и память — до явного удаления пользователем.
10. ✅ **Пока одна текущая LLM.** Архитектура квот строится под будущие AIL / AI Limits (§1.1).

Ещё открыто:

- Бесплатный дневной лимит member-mode (на чат и на участника) и расширенный лимит для платного чата.
- Финальные цифры лимитов петов (§2.4) и стартовые цены каталога `ai_pet_items`.
- Где публикуются обновлённые условия `personal-v1` и политика конфиденциальности.
