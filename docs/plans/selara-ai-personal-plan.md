# Selara AI для личного аккаунта, характеры в группах и AI-петы: план

Статус: черновик для обсуждения с владельцем, код не менялся.
База: `dev` @ `e6957e8` (после PR #22), последняя миграция `0077_selara_ai_payment_refunds`.
Дата: 2026-10-06.

Документ покрывает три идеи из анонса:

1. **Personal AI** — Selara в личных сообщениях, личная подписка, персонализация и память.
2. **Group character** — клички и характер Selara в группе (в рамках групповой подписки).
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

---

## 2. Биллинг: раздельные подписки

### 2.1 Продукты

| product_key | Владелец | Что даёт |
|---|---|---|
| `selara_ai_monthly` (есть) | чат | `?`/`??`, итоги дня, **+ клички и характер Selara в чате (идея 2)** |
| `selara_personal_monthly` (новый) | пользователь | AI в ЛС, персонализация, личная память, **AI-петы** |

Рекомендация: петы входят в личную подписку (как в анонсе), отдельный продукт не заводить до проверки спроса. Если позже понадобится, добавится `selara_pets_monthly` тем же механизмом.

### 2.2 Схема (миграция `0078_personal_entitlements`)

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
- `chat_id`/`source_chat_id` у intent сделать NULL-able с CHECK: `target_scope='chat' ⇒ chat_id NOT NULL`, `target_scope='user' ⇒ target_user_id NOT NULL AND target_user_id = buyer_user_id` (подарки другим людям — открытый вопрос, по умолчанию запрещены);
- расширить три CHECK `product_key IN (...)` новым ключом (drop + create constraint, имена известны: `ck_chat_entitlements_product`, `ck_selara_ai_purchase_intents_product`, `ck_selara_ai_payments_product`).

Downgrade: возможен, пока нет строк с `target_scope='user'`; миграция downgrade должна падать явно, если такие строки есть (не терять оплаты молча).

### 2.3 Код

- `selara_ai_product.py` → реестр продуктов `{key: ProductSpec(scope, duration, price_env)}`; цена личной подписки — новый env `SELARA_PERSONAL_PRICE_STARS` (без значения ⇒ покупка закрыта, как сейчас).
- `process_successful_payment` диспетчеризует по `target_scope`: для `user` — advisory lock `hash(user_id, product)` + row lock `user_entitlements`, продление от `max(now, valid_until)`; идемпотентность по `telegram_payment_charge_id` уже есть.
- `pre_checkout`: для `user` нет проверки «админ чата», только buyer = target, intent, сумма, валюта, terms.
- Условия: отдельная версия условий на продукт (`terms_version` уже хранится в intent). Для личного продукта — `personal-v1` с пунктами про память, ролевые сценарии и возрастные ограничения.
- `/premium` в ЛС получает выбор: «Для группы» (как сейчас) / «Для себя».
- `/stars_refund` и админ-аналитика монетизации: добавить фильтр `target_scope`, «активные личные подписки».
- Owner exemption в ЛС: `user_id == ADMIN_USER_ID` ⇒ `OWNER_INTERNAL` (live-проверка админства не нужна, это личность, а не чат).

### 2.4 Квоты (миграция `0079_quota_user_scope`)

`ai_feature_quota_usage` сейчас считает и лочит по `chat_id`. Для личного продукта нужен счёт по пользователю, и это принципиально для петов: пет говорит **в группе**, а платит **хозяин**.

- добавить `quota_scope_type varchar(8) NOT NULL DEFAULT 'chat' CHECK IN ('chat','user')` и `quota_scope_id bigint NOT NULL` (backfill = `chat_id`);
- индекс `(feature, quota_scope_type, quota_scope_id, period_start, status)`;
- `feature_quota_lock_key` строить от `(feature, scope_type, scope_id, period_start)`;
- `FeatureAccessService.reserve_feature_usage(..., scope=QuotaScope.user(user_id))`; `chat_id` остаётся как «где произошло» для аналитики.
- `ai_feature_invocations.scope_type/scope_id` уже существуют — заполнять `user`/`<id>`.

Новые `AiFeature` и явные политики в `resolve_feature_policy` (значения — предложение, финальные цифры за владельцем):

| AiFeature | scope | Free | Paid |
|---|---|---|---|
| `personal_chat` | user | 15 сообщений/день (пробник) | 150/день |
| `personal_memory_extract` | user | нет (только ручное «запомни») | без отдельной квоты, 1 вызов на N сообщений |
| `group_character` | chat + доп. лимит на user | недоступно (только paid чата) | 100/день на чат, 15/день на участника |
| `pet_talk` | user (хозяин) | недоступно | 60/день на пета, из них не-хозяевам ≤ 20/день суммарно и ≤ 5/день на человека |
| `pet_event_text` | user (хозяин) | недоступно | ≤ 6 событий/день на пета |

Лимит «на участника» внутри чата — новый тип политики (`per_actor_limit`), реализуется вторым подсчётом под тем же advisory lock.

---

## 3. Идея 1: Personal AI в ЛС

### 3.1 Поведение

- Любое текстовое сообщение в ЛС, которое не команда и не ожидаемый ввод панели, идёт Selara-собеседнику. Роутер `personal_ai` подключается **после** `private_panel` и `autoconfig` (их фильтры ожидания ввода должны выигрывать) и **до** `text_commands`. Фильтр: `F.chat.type == "private"`, текст не начинается с `/`, нет pending-state у пользователя.
- Без подписки: пробная квота и кнопка «Оформить Selara для себя». Без включённого LLM — честное «сейчас недоступно».
- Настройка: `/ai` (или кнопка в ЛС-меню) открывает мастер:
  - имя (по умолчанию «Selara»), пресет характера (спокойный помощник, саркастичный, дружелюбный, строгий наставник, ролевой рассказчик, свой вариант до 500 символов);
  - как обращаться к пользователю (имя, «ты/вы», прозвище);
  - стиль (длина ответа, эмодзи да/нет, язык);
  - режим: «помощник» / «ролевая игра» (во втором — сцена и роль пользователя, отдельная история);
  - память вкл/выкл.
- Команды: `/ai_reset` (сбросить диалог, память остаётся), `/memory` (список фактов с кнопками удаления), `/forget_all` (удалить всё личное: профиль, историю, память — с подтверждением).
- Mini App: страница «Моя Selara» (профиль, память, статус подписки) — отдельным PR после MVP.

### 3.2 Схема (миграция `0080_personal_ai`)

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
- Лимиты: free 20 фактов, paid 200; при переполнении вытесняются самые старые неиспользованные непиннутые.
- Выбор в промпт: pinned + последние использованные + простое совпадение слов с запросом (без векторной БД на старте; pgvector — только если понадобится, отдельным решением).
- История: хранить последние 200 сообщений на поток, остальное сжимать в summary (как `maybe_compress`), сырые сообщения старше 30 дней удалять фоновой задачей (retention — открытый вопрос).

### 3.4 Что ЛС-ассистенту запрещено

- Никаких tools из `infrastructure/llm/tools.py` (модерация, данные групп). На старте tools нет вообще; потом — только безопасные: текущее время, `read_bot_doc`.
- Нет доступа к сообщениям групп и чужим данным, даже если пользователь в них состоит.

---

## 4. Идея 2: клички и характер Selara в группе

### 4.1 Поведение

- Админ с `manage_settings` задаёт до 5 имён (2–24 символа): «Селя», «Селара», «Селарка». Нормализация как у алиасов (lower, ё→е, без пунктуации).
- Триггер: сообщение **начинается** с имени, за которым идёт `,` `!` `:` `?` пробел или конец строки («Селя, кто сегодня самый активный?»). Также reply на сообщение бота, отвеченное в этом режиме, продолжает разговор. Слово в середине фразы не триггерит (иначе «я видел Селю вчера» сожжёт квоту).
- Кто может: все участники (член-режим), если включено в настройках чата. Админы по имени получают тот же член-режим; `?`/`??` остаются как есть для админских действий.
- **Член-режим** — отдельный набор tools только на чтение: `get_top`, `get_chat_stats`, `get_current_time`, `lookup_glossary`/`search_glossary`, `list_bot_docs`/`read_bot_doc`. Без `get_history` по умолчанию (переписка чата → приватность; включение — отдельная галочка админа). Никаких модерационных и изменяющих tools.
- Характер чата: пресет + кастомный текст до 500 символов от админа, влияет на тон обоих режимов (и `?`/`??`), но не на правила безопасности и авторизацию tools.
- Только при активной групповой подписке (`selara_ai_monthly`). Без неё имена можно настроить, но бот отвечает подсказкой не чаще раза в час на чат.

### 4.2 Схема (миграция `0081_group_ai_character`)

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
  created_by_user_id bigint NULL, created_at,
  UNIQUE (chat_id, name_norm)
)
```

Обе таблицы — в `chat_migration.py`. Контекст член-режима хранить отдельно от админского (`llm_context_messages` получает колонку `mode` или отдельная таблица `chat_member_ai_messages`; рекомендация: отдельная таблица, чтобы `??` админа не видел вопросы участников и наоборот).

### 4.3 Порядок обработчиков и коллизии

- Хендлер `group_character` подключается до `text_commands`, но после `llm_admin`; матчит только при наличии имён (кэш имён на чат с инвалидацией при изменении, чтобы не ходить в БД на каждое сообщение).
- Конфликты при сохранении имени: отказать, если имя совпадает с текстовой командой из `commands/catalog.py`, алиасом `chat_text_aliases` или триггером `chat_triggers` с `match_type='starts_with'|'exact'`.
- Анти-спам: кулдаун на участника (`LLM_COOLDOWN_SECONDS`), лимит на участника в день (см. 2.4), игнор ботов и анонимных админов (или как один актёр).

---

## 5. Идея 3: AI-петы

### 5.1 Модель

- Пет принадлежит пользователю (хозяину), «живёт» в одном чате (`home_chat_id`). Один пет на пользователя на старте (позже — слоты).
- Создание: тип (собака, кот, паук, дракон, человек, «своё» до 40 символов), имя, черты характера (3 из списка + свободный текст до 300 символов). Свободные поля проходят модерацию (см. §7).
- Параметры (детерминированно в коде): `level`, `xp`, `mood` 0–100, `satiety` 0–100, `energy` 0–100. Параметры деградируют «ленивым тиком»: при обращении пересчитываются от `last_tick_at`, фонового воркера на каждого пета нет.
- Действия (команды и текст): покормить, погладить, поиграть, дразнить, поговорить, «обидеть». Каждое — фиксированная таблица эффектов, кулдаун на пару (пет, человек), дневной кап изменения отношения.
- Отношения: `affinity` −100…100 на пару (пет, человек), считается кодом. Пороги дают ярлыки: «обожает», «доверяет», «нейтрален», «настороже», «боится/злится». LLM получает ярлык и пару последних событий, а не число.
- Память пета: короткие записи «X накормил меня 5 раз за неделю», «Y дёргал за хвост» — генерируются **кодом** из журнала событий (агрегаты), плюс до 30 LLM-заметок из разговоров. Память пета привязана к чату: в другом чате пет не пересказывает, что было в первом (приватность).
- Уровни: XP за уход с убывающей отдачей; уровни открывают действия, новые реплики, и на уровне N (например 10) — путешествия.
- Путешествия: хозяин переводит пета в другой чат, где сам состоит, если админ того чата разрешил петов (`pets_enabled`). Отношения и память остаются в чате, где возникли (`ai_pet_relationships` ключуется `(pet_id, chat_id, user_id)`).
- События: редкие спонтанные сообщения пета в чат (≤ N/день, только при активности чата, тихие часы), например «Мурка принесла Пете тапок». Это следующий этап, после ядра.
- Разговор: «Мурка, как дела?» или reply на сообщение пета. Говорить может кто угодно, оплачивает квота хозяина с подлимитами для не-хозяев.

### 5.2 Схема (миграция `0083_ai_pets`, после переименования старого `/pet`)

```sql
ai_pets(
  id bigserial PK,
  owner_user_id bigint NOT NULL REFERENCES users ON DELETE CASCADE,
  home_chat_id bigint NOT NULL REFERENCES chats ON DELETE CASCADE,
  current_chat_id bigint NOT NULL REFERENCES chats ON DELETE CASCADE,
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
```

Настройка чата: `chat_settings.pets_enabled` (default false — админ включает явно) и `pets_spontaneous_enabled`. Все таблицы с `chat_id` — в `chat_migration.py`.

### 5.3 Конкурентность и идемпотентность

- Любое действие: одна транзакция, `SELECT ... FOR UPDATE` строки `ai_pets` и строки отношения, вставка `ai_pet_events` по `idempotency_key` (повтор апдейта Telegram не кормит дважды), затем пересчёт.
- Левел-ап и путешествие — внутри той же транзакции, сообщение в чат после commit (как в оплате).
- LLM-текст генерируется **после** commit механики; сбой LLM не откатывает действие, показывается шаблонная реплика.
- Стоимость действий в экономике (корм за монеты) — через существующий `economy_ledger` в той же транзакции (если владелец решит, что корм платный).

### 5.4 Судьба текущего `/pet`

Текущий `/pet` — социальная ролевая связь между людьми, у неё есть данные (`relationships_graph`), ачивки и место в семейном дереве. Превратить человека в AI-пета нельзя по смыслу, поэтому **рекомендация: не удалять, а переименовать** и освободить `/pet` для AI-петов.

1. PR «rename family pet» (до AI-петов):
   - команда `/bepet` («стать питомцем»), текстовая «стать питомцем» уже есть и остаётся;
   - `/pet` на переходный период (например 4 недели) отвечает подсказкой «Теперь это /bepet» и выполняет старое действие;
   - ключ доступа команды `pet` → `family_pet`: data-миграция `UPDATE chat_command_access_rules SET command_key='family_pet' WHERE command_key='pet'` и то же для `chat_text_aliases.command_key` (проверить коллизии PK `(chat_id, command_key)` перед UPDATE);
   - `relationships_graph.relation_type='pet'` и ачивки не трогаем (внутренний ключ, пользователю не виден); в UI подпись «питомец (ролевой)».
2. После запуска AI-петов `/pet` → меню AI-пета. Старые ролевые питомцы продолжают отображаться в семейном дереве.
3. Миграция данных «ролевой питомец → AI-пет» не нужна. Опционально: хозяевам ролевых питомцев при создании AI-пета дать стартовый бонус (косметика), это продуктовое решение.

Альтернатива — полностью убрать ролевого питомца (удалить строки, ачивки оставить как исторические). Не рекомендую: теряем данные пользователей без выигрыша.

---

## 6. Стоимость LLM (оценка, проверить на реальных логах)

По `pricing.py` для текущей `gpt-4o-mini` ($0.15 / 1M prompt, $0.60 / 1M completion):

| Вызов | Prompt | Completion | ≈ стоимость |
|---|---|---|---|
| Сообщение в ЛС (system + профиль + 10 фактов + summary + 12 последних) | ~2 500 | ~250 | $0.0005 |
| Реплика пета | ~1 200 | ~120 | $0.00025 |
| Ответ по имени в группе (с 1–2 tool rounds) | ~4 000 | ~300 | $0.0008 |
| Извлечение памяти раз в 10 сообщений | ~1 500 | ~100 | $0.0003 |

Худший платный пользователь: 150 сообщений × 30 дней × $0.0005 ≈ **$2.3/мес** + пет 60 × 30 × $0.00025 ≈ **$0.45/мес**. Средний будет в разы меньше. Цена в Stars должна покрывать худший случай с запасом; курс вывода Stars владельцу проверить по актуальным условиям Telegram (в коде курса нет). При смене `LLM_MODEL` на модель без цены в реестре стоимость станет «неизвестной» — перед запуском добавить цену используемой модели в `pricing.py`.

Рычаги: лимит `max_tokens` по длине ответа из профиля, жёсткие лимиты длины кастомных полей, обрезка истории, дешёвая модель (`LLM_SUMMARY_MODEL`) для сжатия и извлечения, шаблонные реплики петов без LLM для частых действий.

---

## 7. Безопасность и модерация

- **Prompt injection через пользовательский текст** (характер, имя пета, память, сообщения других участников пету): всё вставляется как данные в тегах с экранированием (как `_untrusted()` в `tools.py`), никакого влияния на tools. У пета и Personal AI нет tools с побочными эффектами — главная защита архитектурная, не промптовая.
- **Отравление памяти пета** другими участниками («запомни, что Вася вор»): память о людях пет формирует кодом из событий; диалоговые заметки только нейтральные, ≤ 30 штук, хозяин может их стереть; пет не утверждает факты о людях, только эмоции («Вася меня дразнил»).
- **Модерация вводимых полей**: длина, запрет ссылок/упоминаний в именах, денилист, плюс проверка LLM-классификатором при сохранении кастомного характера (один дешёвый вызов). Если провайдер даёт moderation endpoint, использовать его опционально.
- **Ролевые сценарии**: запрет сексуального контента (возраст пользователей Telegram неизвестен), насилия над реальными людьми, самоповреждения (ответ с ресурсами помощи), выдачи себя за реальных людей. Это в неизменяемом system-слое и в условиях `personal-v1`.
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

Пользовательские права: `/memory` (просмотр/удаление), `/forget_all` (каскадное удаление профиля, истории, памяти; пет удаляется отдельной командой с подтверждением), экспорт памяти текстом. Retention: сырые сообщения ЛС 30 дней, summary и память до удаления пользователем. Политика конфиденциальности/условия обновить до запуска (открытый вопрос: где публикуются).

Бэкапы (`infrastructure/backup.py`) будут содержать личные тексты: явно отметить в условиях, срок хранения бэкапов определить.

---

## 9. Этапы поставки (каждый — отдельный PR в `dev`, Sonnet реализует, Opus ревьюит HEAD)

| # | PR | Миграция | Содержание | Зависит от |
|---|---|---|---|---|
| 0 | Этот план | — | документ | — |
| 1 | Billing foundation | `0078_personal_entitlements`, `0079_quota_user_scope` | реестр продуктов, `user_entitlements`, `target_scope` в intents/payments, user-scope квот, owner exemption в ЛС, `/premium` «для себя», refund/аналитика. Без пользовательских фич, продукт скрыт пока не задан `SELARA_PERSONAL_PRICE_STARS`. | — |
| 2 | Personal AI MVP | `0080_personal_ai` | профиль, мастер `/ai`, диалог в ЛС, history+summary, пресеты, `/ai_reset`, пробная квота, условия `personal-v1` | 1 |
| 3 | Personal memory | (в 0080 или `0080b`) | явная память, `/memory`, `/forget_all`, retention-задача; авто-извлечение за флагом | 2 |
| 4 | Mini App «Моя Selara» | — | профиль, память, подписка | 2, 3 |
| 5 | Group character | `0081_group_ai_character` | имена, характер, член-режим с read-only tools, раздельный контекст, лимиты на участника, `chat_migration` | 1 (per-actor политика) |
| 6 | Rename family pet | `0082_family_pet_command_key` (data) | `/bepet`, переходный `/pet`, перенос access rules и алиасов | — (можно параллельно с 2) |
| 7 | AI-петы: ядро | `0083_ai_pets` | создание, параметры, ленивый тик, действия с кулдаунами, отношения, события, уровни; реплики шаблонами без LLM; `pets_enabled` | 1, 6 |
| 8 | AI-петы: разговор и память | `0084_ai_pet_dialogue` | обращение по имени/reply, LLM-реплики, агрегатная + диалоговая память, квоты хозяина с подлимитами | 7 |
| 9 | AI-петы: события и путешествия | `0085_ai_pet_travel` (если нужно) | спонтанные события с лимитами и тихими часами, путешествия по уровню | 8 |

Номера миграций условные: на момент PR брать следующий свободный номер от актуального `dev`. Каждая миграция — с downgrade; для data-миграций downgrade обратный UPDATE. Тесты: Postgres integration на конкуренцию оплат/квот/действий пета (как `test_telegram_stars_postgres.py`, `test_feature_quotas_postgres.py`), unit на PromptBuilder и правила механики, тест порядка роутеров (ЛС-ввод панели не уходит в AI).

Минимальный запускаемый продукт для анонса: PR 1 + 2 + 3. Петы — после реакции на опрос.

---

## 10. Риски

1. **Расширение платёжного пути** — самый рискованный PR (1). Деньги, CHECK-констрейнты, advisory locks. Отдельный Opus-ревью с фокусом на идемпотентность и восстановление после падения БД.
2. **Перехват текста в ЛС**: catch-all Personal AI может съесть ввод панели/autoconfig/админ-рассылок. Нужен явный тест на порядок роутеров и pending-state.
3. **Ложные срабатывания по имени** в группах и сожжённая квота. Только начало сообщения, кулдауны, подсказка без LLM при исчерпании.
4. **Стоимость**: не-хозяева тратят квоту хозяина пета; бесплатная пробная квота ЛС может стать бесплатным ChatGPT. Подлимиты и `max_tokens`.
5. **Модерация и репутация**: ролевые сценарии и петы как канал оскорблений. Неизменяемый safety-слой, выключатели у админов, жалобы.
6. **Приватность**: смешение контекстов (ЛС ↔ группа, админ ↔ участники, чат ↔ чат у петов). Раздельные таблицы, а не фильтры в одном.
7. **Миграция group→supergroup**: новые таблицы забыть в `chat_migration.py` = потеря имён/петов при апгрейде группы. Добавить тест, который сверяет модели с `chat_id` и список миграции.
8. **Откат**: alembic downgrade на проде владелец не делает (откат = старый образ). Значит миграции должны быть аддитивными и совместимыми со старым кодом: новые колонки с default, никаких переименований существующих колонок в том же релизе, что и код.
9. **Одна глобальная модель**: если `LLM_MODEL` сменится на дорогую, все новые фичи подорожают одновременно. Предусмотреть отдельные env `LLM_PERSONAL_MODEL`/`LLM_PET_MODEL` с fallback на `LLM_MODEL`.

---

## 11. Вопросы к владельцу

1. Цена личной подписки в Stars и входят ли петы в неё (рекомендую: да, одна подписка «Selara для себя»)?
2. Лимиты из §2.4 устраивают? Нужна ли бесплатная пробная квота в ЛС и какая?
3. Клички и член-режим в группе — только для платных чатов (рекомендую) или базовая версия бесплатно?
4. Член-режим: разрешать ли по умолчанию доступ к истории чата (`get_history`) или только по галочке админа (рекомендую: по галочке)?
5. Ролевой режим в ЛС: какие жанры допустимы, нужен ли возрастной порог? Рекомендую: без 18+.
6. Судьба `/pet`: согласны переименовать ролевого питомца в `/bepet` и отдать `/pet` AI-петам?
7. Петы и экономика: корм и игрушки за монеты чата, бесплатно, или бонусы к экономике от пета?
8. Можно ли дарить личную подписку другому пользователю?
9. Retention: 30 дней для сырых сообщений ЛС подходит? Где публикуем обновлённые условия и политику конфиденциальности?
10. Нужна ли отдельная (более дешёвая или более умная) модель для личного режима и петов?
