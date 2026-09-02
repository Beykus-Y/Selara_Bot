# «Итоги дня» — ежедневная AI-сводка чата: прогресс реализации

Status: **в разработке**, слайсами с тестами первым делом
([[feedback_tests_before_logic]]). Полный дизайн зафиксирован и утверждён Ильёй —
см. согласованные архитектурные решения ниже. Каждый пункт помечается по мере
реализации; ничего не деплоится и не включается по умолчанию
(`chat_settings.daily_summary_enabled = False`) до отдельного решения Ильи об этом.

Метод: реализация идёт снизу вверх — сначала чистые, без-БД функции бизнес-логики
(каждая своим тестом, написанным вместе с/до неё), затем миграции и работа с БД,
затем LLM-клиент/промпты, затем шедулер/хендлеры. Порядок выбран так, чтобы самая
рискованная часть (схема БД, конкурентность, деньги) реализовывалась поверх уже
проверенной логики, а не наоборот.

Согласованные с Ильёй решения (не подлежат пересмотру без явного нового решения):
- Полный пайплайн строится сразу, без урезанного MVP.
- Скользящее окно «последние 24 часа» (не календарный день) — и для scheduled
  (плановое, не фактическое время запуска — переживает даунтайм), и для `/summary`.
- Транскрипты — отдельная колонка `messages.transcript` + `transcribed_at`, TTL 14 дней.
- `search_messages` в v1 — только текстовый (ILIKE/pg_trgm), без embeddings.
- Санитизация авторов (живые ↔ анонимный токен) — обязательный шаг до любой LLM,
  плюс отдельный privacy-pass по известным алиасам (username/persona/text-mention)
  для случая, когда активный участник сам упоминает ушедшего.
- `episode_count` (было `reappeared_count`) считается ПОСЛЕ LLM-merge, backend'ом,
  по дедуплицированным message_id — не LLM и не до merge.
- LLM #1 не считает статистику карточки — только backend, по точным message_id.
- Атомарный claim + lease/reclaim через `daily_summary_runs` — не read-then-write.
- Себестоимость: `pipeline_cost_usd` (без двойного счёта) отдельно от
  `context_stt_cost_usd` (может законно повторяться между пересекающимися прогонами).
- STT — асинхронная очередь (не синхронно в хендлере), с recovery-сканом при рестарте.
- Полный план: `~/.claude/plans/fancy-baking-pascal.md` (согласован в диалоге с Ильёй,
  прошёл несколько раундов правок — семантика окна, атомарность, себестоимость STT,
  privacy-pass, episode_count, structured output с fallback).

---

## 1. Чистая бизнес-логика (`src/selara/application/daily_summary/`) — без БД

Каждый пункт: модуль + тест, написанные вместе, зелёные локально
(`.venv/bin/python -m pytest tests/unit/test_daily_summary_*.py`).

- [x] `segmentation.py` — разбиение сообщений на "разговоры" (пауза / token-budget /
  max_messages со strахoвкой, overlap на границе принудительного разреза).
  Тест: `tests/unit/test_daily_summary_segmentation.py` (5/5).
- [x] `eligibility.py` — гейты запуска (`daily_summary_enabled`, `save_message`,
  реальный активный лок `chat_write_locked` — НЕ `antiraid_enabled`-тумблер,
  порог сообщений, "уже запускали сегодня"). Тест:
  `tests/unit/test_daily_summary_eligibility.py` (12/12).
- [x] `participants.py` — live-директория отображаемых имён для активных участников
  (persona/имя/username), ушедшие полностью исключены. Тест:
  `tests/unit/test_daily_summary_participants.py` (4/4).
- [x] `sanitize.py` — токены авторов (активный → имя/persona, ушедший → стабильный
  `Участник #N` в рамках прогона) + privacy-pass (`build_alias_index`,
  `redact_known_aliases`, `redact_text_mentions` по text_mention-entities). Тест:
  `tests/unit/test_daily_summary_sanitize.py` (8/8).
- [x] `stats.py` — `compute_episode_count` (дедуп по времени/merge пересекающихся
  диапазонов после LLM-merge, а не "сколько карточек объединил merge"). Тест:
  `tests/unit/test_daily_summary_stats.py` (7/7). Ещё нужно: расчёт
  `message_count/participant_count/reply_count/duration` по реальным message_id
  (требует репозиторного слоя — Alembic-миграция A с `reply_to_telegram_message_id`
  должна быть готова первой, см. раздел 2).
- [x] `tool_limits.py` — жёсткие капы и chat-scope guard для 4 read-only тулов
  аналитика (`GET_MESSAGE_CONTEXT_MAX_ROWS=40`, `GET_REPLY_THREAD_MAX_ROWS=50`,
  `SEARCH_MESSAGES_MAX_ROWS=50`, `enforce_chat_scope`). Тест:
  `tests/unit/test_daily_summary_tool_limits.py` (8/8). Сами тулы (SQL-реализация
  `get_message_context`/`get_reply_thread`/`search_messages`/`get_activity_stats`)
  — отдельный пункт после миграции A, живут в
  `infrastructure/llm/daily_summary_tools.py` (отдельный реестр, не смешивать с
  admin-тулами).
- [ ] `sanitize.py` — не хватает функции, которая прогоняет ВЕСЬ пайплайн ввода
  (текст сегмента для LLM #1, ответы тулов для LLM #3) через
  `build_author_display_tokens` + `redact_known_aliases` + `redact_text_mentions` за
  один проход — сейчас это только строительные блоки, оркестрации ещё нет.
- [x] `chat_settings.py`/`settings_common.py`/`repositories.py`: dataclass-поля
  `daily_summary_enabled/hour/min_messages/style/include_voice/include_video_notes` с
  дефолтами, регистрация в `CHAT_SETTINGS_KEYS`/`parse_chat_setting_value`/
  `CFG_BOOL_KEYS`/`CFG_ENUM_VALUES`/`SETTING_META`/`settings_to_dict`/`_to_chat_settings`,
  документированы в `docs/ADMIN_GUIDE.md` §6. `default_chat_settings` не трогали —
  дефолты уже приходят с уровня dataclass, как и у большинства других булевых полей.

## 2. Хранение данных (Alembic + repositories.py)

- [x] Миграция A (`0058_daily_summary_messages`): `messages.transcript`,
  `messages.transcribed_at`, `messages.reply_to_telegram_message_id` (+ индекс),
  `pg_trgm`+GIN на `messages.text` (postgres-only, guarded по dialect).
- [x] Миграция B (`0059_daily_summary_settings`): 6 новых колонок `chat_settings`.
- [x] Миграция C (`0060_daily_summary_runs`): таблица `daily_summary_runs`
  (claim/lease/reclaim через `status`/`claimed_at`/`lease_until`, скользящее окно
  `window_from/window_to`, `pipeline_cost_usd`/`context_stt_cost_usd` раздельно,
  `UNIQUE(chat_id, summary_date, trigger)`, check-constraints на `trigger`/`status`).
  ORM: `DailySummaryRunModel` в `infrastructure/db/models.py`.
- [x] Миграция D (`0061_llm_usage_log`): таблица `llm_usage_log`
  (`summary_run_id`/`message_archive_id` nullable, ровно один из двух заполнен).
  ORM: `LlmUsageLogModel`.
- Все 4 миграции проверены end-to-end на одноразовом Postgres 16 контейнере:
  `upgrade head` от 0057 до 0061 чисто, `downgrade` обратно до 0057 чисто, повторный
  `upgrade head` чисто; `\d messages`/`\d daily_summary_runs` подтвердили индексы,
  FK, check-constraints и `pg_trgm` GIN-индекс на месте. Ревизии Alembic должны быть
  ≤32 символов (колонка `alembic_version.version_num` — `varchar(32)`) — учтено при
  выборе id миграций (`0058_daily_summary_messages`, `0059_daily_summary_settings`,
  не полные "человеческие" описания).
- [x] `reply_to_telegram_message_id` теперь реально пишется при архивации: цепочка
  `activity_tracker.py` (`_build_message_archive_payload` вытаскивает
  `reply_to_message.message_id`) → `ActivityBatchMessage`/`enqueue_message`
  (`activity_batching.py`, `activity_batcher.py`) → `_build_message_archive_rows`/
  `_insert_message_archive*` в `repositories.py` (оба пути — batch-postgresql и
  единичная вставка/fallback). Тест: `test_activity_tracker_archive_payload_captures_reply_to_message_id`
  в `tests/unit/test_activity_tracker_middleware.py`, плюс обновлены 3 существующих
  теста, которые проверяли точные kwargs вызова `enqueue_message` (регрессия была бы
  незамечена без них).
- [x] `claim_daily_summary_run`/`get_daily_summary_run`/`finalize_daily_summary_run_generated`/
  `mark_daily_summary_run_sent`/`mark_daily_summary_run_send_failed`/
  `mark_daily_summary_run_failed`/`record_llm_usage`/`sum_context_stt_cost_in_window` в
  `SqlAlchemyActivityRepository` (`repositories.py`), плюс `DailySummaryRun` domain-сущность
  (`domain/entities.py`). Claim — один атомарный `INSERT ... ON CONFLICT ... DO UPDATE ...
  WHERE status IN ('claimed','generating') AND lease_until < now()` на Postgres (с
  best-effort fallback на select+insert/update для sqlite-юнит-тестов, продакшн всегда
  на Postgres). Проверено 4 интеграционными тестами на реальном Postgres 16 в Docker
  (`tests/integration/test_daily_summary_claim_postgres.py`): (1) два truly-конкурентных
  claim'а через `asyncio.gather` — ровно один побеждает; (2) живой claim не
  переклеймивается до истечения lease; (3) протухший (`lease_until` в прошлом) claim
  успешно переклеймивается; (4) `status='generated'` никогда не переклеймивается для
  повторной генерации даже спустя много времени — можно только пересылать сохранённый
  `generated_text` (сам resend-путь как отдельный хендлер ещё не реализован).
- [x] `count_archived_messages_in_window` (для eligibility.py — читает именно
  `messages`, а не общие activity-event счётчики, т.к. пайплайн видит только то,
  что реально заархивировано) и 4 запроса аналитика — `get_message_context`
  (окно ±N вокруг сообщения), `get_reply_thread` (BFS по `reply_to_telegram_message_id`,
  до 10 уровней вложенности), `search_messages` (ILIKE по `text`/`transcript` в
  окне), `get_activity_stats_in_window` (message/participant/reply count без LLM) —
  все в `SqlAlchemyActivityRepository`, используют `tool_limits.clamp_row_limit`
  как защитный клэмп даже если тул-обёртка выше по стеку когда-нибудь забудет
  клэмпнуть сама. Domain-типы: `ArchivedMessageView`, `ActivityWindowStats`
  (`domain/entities.py`). Проверено 8 интеграционными тестами на реальном Postgres
  (`tests/integration/test_daily_summary_tools_postgres.py`): контекст вокруг
  сообщения, пустой результат для неизвестного id, транзитивный reply-thread (BFS
  через цепочку 1←2←3←4), обрезка по лимиту, ILIKE-поиск внутри/вне окна, подсчёт
  статистики, исключение edited-снапшотов и ботов из счётчика для eligibility.
- [x] LLM-facing обёртка `infrastructure/llm/daily_summary_tools.py` — отдельный
  реестр (НЕ смешан с admin-тулами `infrastructure/llm/tools.py`), 4 tool-схемы в
  OpenAI-совместимом формате для `LlmClient.chat_with_tools`. Ключевое архитектурное
  решение: ни одна схема не принимает `chat_id` как параметр вообще — чат фиксирован
  через `DailySummaryToolContext.scope`, модель физически не может попросить данные
  другого чата (сильнее, чем "прими chat_id и провалидируй" — здесь его просто негде
  подменить). `search_messages`/`get_activity_stats` дополнительно клэмпают
  запрошенное окно через `clamp_window_to_scope` (новая функция в `tool_limits.py`,
  4 теста). Каждое возвращаемое сообщение проходит через
  `author_tokens`(санитизация автора) + `redact_known_aliases` (privacy-pass) перед
  тем как попасть в JSON, отдаваемый модели — то есть даже тулы аналитика не могут
  случайно раскрыть имя/persona ушедшего участника. Проверено 7 юнит-тестами с
  фейковым репозиторием (`tests/unit/test_daily_summary_tools.py`): подмена автора
  токеном, privacy-pass редактирует чужой текст с упоминанием ушедшего, дефолтное и
  клэмпнутое окно для `search_messages`/`get_activity_stats`, пустой запрос не бьёт
  в БД, неизвестный тул и исключение репозитория превращаются в `success=False`, а
  не роняют вызывающий код.
- [ ] Оркестрация 4-стадийного пайплайна (`pipeline.py`), которая реально собирает
  `DailySummaryToolContext` и гоняет `chat_with_tools` в цикле — тулы и данные для
  них готовы, но ничего пока не вызывает `execute_daily_summary_tool` из настоящего
  LLM-цикла.

## 3. Admin UI (`settings_common.py`, `settings.py`, `chat_assistant.py`)

- [x] Регистрация 6 новых `daily_summary_*` настроек по существующему `/cfg`-паттерну
  (toggle/select/SettingMeta) — сделано вместе с разделом 1. Ещё не проверено вручную
  в реальном админ-UI/веб-панели (только юнит-тесты `test_web_presenters.py`,
  `test_guide_docs_match_catalog.py`, `test_web_chat_hub_routes.py` зелёные).

## 4. STT-очередь для целей сводки — НЕ начато

- [ ] `asyncio.Queue` + N воркеров, ограниченная конкурентность, запись
  `transcript`/`transcribed_at`, лог в `llm_usage_log` (`stage='stt'`,
  `message_archive_id`). Recovery-скан при старте воркера.
- [ ] TTL-очистка транскриптов (по `transcribed_at`, 14 дней).

## 5. LLM-пайплайн

- [x] `LlmClient.chat_structured` (`infrastructure/llm/client.py`) — новый метод,
  всегда бьёт в `summary_model` (дешёвая модель, как у `summarize()`), не в основную
  `model`. Native `response_format={"type":"json_schema",...}` используется только
  если `LlmConfig.supports_structured_output=True` (новая настройка
  `LLM_SUPPORTS_STRUCTURED_OUTPUT` в `.env`/`core/config.py`, явный opt-in, не
  угадывается по имени провайдера — так и оговорено с Ильёй). Ответ **всегда**
  дополнительно валидируется Pydantic-схемой независимо от native-режима (провайдер
  может заявлять поддержку и всё равно не соблюдать её). При ошибке JSON-парсинга
  или валидации схемы — один повторный запрос с сообщением об ошибке, затем
  `LlmClientError`. 7 юнит-тестов с моком `AsyncOpenAI`
  (`tests/unit/test_llm_client_structured.py`): валидный JSON, использование именно
  `summary_model`, native vs fallback режим по флагу, успешный ретрай после
  невалидного JSON, успешный ретрай после несовпадения со схемой, финальный отказ
  после двух неудач подряд.
- [x] Промпты `.md` в `infrastructure/llm/prompts/daily_summary/` (`segmenter.md`,
  `merge.md`, `analyst.md`, `writer.md`) + загрузчик `daily_summary_prompts.py` с
  mtime+size-кэшем (образец — `InterestingFactCatalog`). `writer.md` берёт
  `{style_instructions}` из словаря `STYLE_INSTRUCTIONS` (neutral/lively/snarky,
  фолбэк на neutral), `analyst.md` — `{chat_title}`/`{window_from_ru}`/`{window_to_ru}`.
  Каждый файл содержит формулировку про untrusted user-контент. 8 тестов
  (`tests/unit/test_daily_summary_prompts.py`), включая hot-reload по mtime.
- [x] `infrastructure/llm/daily_summary_tools.py` — 4 тула поверх репозитория +
  `tool_limits.py` (см. раздел выше, уже был готов).
- [x] `infrastructure/llm/pricing.py` — статическая мапа цен по модели
  (`MODEL_PRICING_USD_PER_1K_TOKENS`, неизвестная модель → 0 стоимость, не ошибка) +
  `estimate_llm_cost_usd`/`estimate_stt_cost_usd`. 5 юнит-тестов
  (`tests/unit/test_llm_pricing.py`).
- [x] `LlmClient.last_usage`/`last_model` — новый атрибут, выставляется после каждого
  `chat_with_tools`/`chat_simple`/`summarize`/`chat_structured` вызова (через
  внутренний `_record_usage`), не меняя сигнатуру возвращаемого значения ни одного
  метода — так пайплайн узнаёт токены/модель последнего вызова для учёта стоимости.
  Покрыто тестом `test_chat_structured_records_last_usage_for_cost_accounting`.
- [x] `application/daily_summary/schemas.py` — Pydantic-схемы `SegmentTopicCardList`
  (LLM #1, без статистики) и `MergedThemeList` (LLM #2, с `importance` 1-5).
- [x] `application/daily_summary/pipeline.py` — оркестрация LLM #1→#2→#3→#4:
  забирает сообщения окна → санитизирует авторов/тексты → сегментирует →
  `chat_structured` по сегменту (LLM #1, graceful skip сегмента при `LlmClientError`)
  → merge (LLM #2, graceful fallback "каждая карточка — своя тема" при отказе) →
  `episode_count` по `stats.compute_episode_count` → аналитик (LLM #3,
  `chat_with_tools` с 4 тулами, до `MAX_ANALYST_TOOL_ROUNDS=4` раундов,
  **best-effort**: пустой/невалидный JSON-ответ не стирает темы, любое исключение —
  просто оставляет темы после merge как есть) → писатель (LLM #4, `chat_simple`,
  топ `MAX_THEMES_IN_WRITER=6` тем по `importance`) → финальный текст + статичный
  бета-дисклеймер. Guardrail `MAX_SEGMENTS_PER_RUN=20` — остаток сегментов сверх
  лимита **отбрасывается без LLM-детализации** (упрощение этого слайса, без
  статистического rollup остатка — зафиксировано как осознанное упрощение, не баг).
  4 юнит-теста с фейковым LLM-клиентом и фейковым репозиторием
  (`tests/unit/test_daily_summary_pipeline.py`): пустое окно → "было тихо", полный
  happy path со всеми 4 стадиями и ненулевой себестоимостью, все сегменты упали →
  "не получилось", merge упал → фолбэк на карточки как отдельные темы.
  **Не сделано в этом слайсе**: реальный вызов `record_llm_usage`/
  `finalize_daily_summary_run_generated` в БД по итогам прогона (пайплайн отдаёт
  `stage_usages`/`pipeline_cost_usd`, но кто-то выше (шедулер/`/summary`-хендлер)
  должен сам вызвать репозиторные методы) — это часть раздела 6.

## 6. Шедулер и команды

- [x] `application/daily_summary/schedule.py` — `compute_scheduled_window_to` (чистая
  функция: плановое, не фактическое время — переживает даунтайм). 4 юнит-теста
  (`tests/unit/test_daily_summary_schedule.py`).
- [x] `presentation/daily_summary.py`:
  - `attempt_daily_summary_run(...)` — единая функция цикла claim→generate→finalize→send
    для ОБОИХ триггеров (`scheduled`/`manual`), идемпотентна при повторном вызове
    (уже `sent`/`failed` → no-op, уже `generated` → просто пересылает сохранённый
    текст без повторного пайплайна, живой чужой claim → `claim_lost`). Важный нюанс:
    `daily_summary_enabled` гейтит **только** `scheduled` — для `trigger="manual"`
    eligibility вызывается с `replace(chat_settings, daily_summary_enabled=True)`,
    поэтому `/summary` работает независимо от того, включена ли автоматика (как и
    зафиксировано в плане), не трогая уже протестированную логику `eligibility.py`.
  - `DailySummaryScheduler`/`run_daily_summary_scheduler` — поллинг раз в 15 минут
    (`_POLL_INTERVAL_SECONDS`), `list_chats_with_daily_summary_enabled` → для каждого
    чата план по `compute_scheduled_window_to` в его локальной таймзоне
    (`settings.bot_timezone`, `ZoneInfo`) → `attempt_daily_summary_run`.
  - Новый репозиторный метод `list_chats_with_daily_summary_enabled` (по образцу
    `list_chats_with_interesting_facts_enabled`) и `get_daily_summary_run_by_id`
    (нужен для пересылки уже сгенерированного текста по id прогона).
  - 5 интеграционных теста на реальном Postgres + фейковый LLM-клиент
    (`tests/integration/test_daily_summary_scheduler_postgres.py`): полный цикл
    claim→generate→send с проверкой `pipeline_cost_usd > 0`, ручной триггер работает
    при выключенной автоматике, плановый триггер блокируется при выключенной
    автоматике, отказ при нехватке сообщений (без единого LLM-вызова — `run is None`
    в БД), повторный ручной вызов в тот же день — no-op без повторной отправки.
- [x] `/summary` хендлер (`presentation/handlers/daily_summary.py`) — доступен
  только когда `llm_client` сконфигурирован (регистрация роутера условна в
  `routers.py`, как у `llm_admin_router`), требует право `manage_settings` (как у
  `/setcfg`), шлёт статус-сообщение "собираю итоги…", затем либо удаляет его (успех),
  либо редактирует на человекочитаемую причину отказа (все ветки `DailySummaryOutcome.reason`
  разобраны в `_describe_outcome_reason`). Добавлена команда в `build_bot_commands()`
  и в `docs/ADMIN_GUIDE.md` (§6, "Итоги дня — команда").
- [x] Регистрация: `run_daily_summary_scheduler` таск в `main.py` (только если
  `llm_client is not None`, symmetричный shutdown как у `interesting_facts_task`),
  `LlmConfig.supports_structured_output` прокинут из `settings.llm_supports_structured_output`.
- [x] STT-очередь для голосовых/кружков полностью реализована:
  - `application/daily_summary/transcription.py` — чистые функции: `extract_media_ref`/
    `build_job_from_raw_message` (парсинг file_id/duration из `raw_message_json` —
    нужно только recovery-скану; live-путь строит `TranscriptionJob` напрямую из
    aiogram-объекта, минуя JSON), `is_transcription_enabled`, `is_within_transcription_budget`.
    12 юнит-тестов (`tests/unit/test_daily_summary_transcription.py`).
  - `infrastructure/stt/daily_summary_queue.py` — `DailySummaryTranscriptionQueue`:
    `asyncio.Queue` + N воркеров (`settings.daily_summary_stt_concurrency`), bounded
    retry/backoff (`_claim_with_retry`, по умолчанию 5 попыток) вместо ожидания
    архивной строки синхронно, per-chat лимит секунд транскрибации в сутки
    (`settings.daily_summary_max_transcription_seconds_per_chat_per_day`, проверяется
    ДО скачивания файла — по duration из Telegram, не после оплаты STT), recovery-скан
    на старте (`list_pending_voice_transcription_candidates`, окно 26ч).
  - **Дедупликация live-job/recovery без новой колонки**: `claim_message_for_transcription`
    переиспользует `transcribed_at` как маркер claim'а (атомарный
    `UPDATE ... WHERE transcript IS NULL AND transcribed_at IS NULL RETURNING id`) —
    выигрывает ровно один заявитель, второй получает `NULL` и просто не работает
    дальше. `release_transcription_claim` откатывает `transcribed_at` в `NULL` при
    любой неудаче (бюджет/скачивание/STT-ошибка/выключенный тумблер), не трогая
    `transcript`, — сообщение остаётся доступным для будущего retry (следующий
    recovery-скан после рестарта), но не бьёт по очереди/сводке прямо сейчас.
    Ошибка одного job (`_process_job`) ловится в `_worker_loop` — воркер не падает,
    берёт следующий job из очереди.
  - Новые репозиторные методы: `claim_message_for_transcription`,
    `release_transcription_claim`, `finalize_message_transcript`,
    `sum_transcription_seconds_in_window`, `list_pending_voice_transcription_candidates`.
    Новая domain-сущность `PendingTranscriptionCandidate`.
  - `voice.py` **не тронут по существу** — только добавлен параллельный,
    полностью отделённый вызов `_maybe_enqueue_for_daily_summary(...)` в обоих
    хендлерах (`voice_message_handler`/`video_note_message_handler`), который
    берёт `file_id`/`duration` прямо из aiogram-объекта Telegram (никакой
    зависимости от архивации) и просто кладёт job в очередь — существующая
    мгновенная расшифровка-ответ (`_transcribe_and_reply`) не изменена ни строкой
    и не знает о существовании этой очереди. Гейты на enqueue: только group/supergroup,
    `save_message=true`, соответствующий `daily_summary_include_voice`/
    `include_video_notes` включён (плюс воркер **перепроверяет** тумблер ещё раз
    непосредственно перед обработкой — если выключили между enqueue и обработкой,
    STT не тратится).
    `SttClient.model` — новый маленький public-property (был только приватный
    `_config.model`) для записи модели в `llm_usage_log`.
  - Регистрация: `DailySummaryTranscriptionQueue` создаётся и стартует в `main.py`
    (только если `stt_client is not None`, независимо от `llm_client` — сама
    транскрибация ценна и без готового пайплайна сводки), передаётся в хендлеры
    через `polling_kwargs` (тот же механизм, что у `stt_client`/`llm_client`),
    корректно останавливается в `finally`.
  - Новые настройки в `.env.example`/`core/config.py`: `DAILY_SUMMARY_STT_CONCURRENCY`
    (default 2), `DAILY_SUMMARY_MAX_TRANSCRIPTION_SECONDS_PER_CHAT_PER_DAY` (default 1800).
  - 7 юнит-тестов на wiring в `voice.py` (`tests/unit/test_voice_daily_summary_enqueue.py`):
    enqueue при включённом тумблере, no-op при выключенном/`save_message=false`/приватном
    чате, инстант-ответ пользователю продолжает работать независимо от сводки,
    правильный `message_type` для voice/video_note, video_note не триггерится тумблером
    voice. Плюс обновлён 1 существующий тест сигнатуры хендлера (аддитивные параметры
    с дефолтами, старое поведение не сломано).
  - **14 интеграционных тестов на реальном Postgres**
    (`tests/integration/test_daily_summary_stt_queue_postgres.py`): claim retries
    until archive row appears (реальная гонка через `asyncio.gather` с отложенной
    вставкой строки), bounded give-up когда строка так и не появилась, полный успешный
    цикл транскрибации с записью `llm_usage_log`, skip при выключенном тумблере
    (STT не вызван вообще), skip при превышении суточного бюджета (с release claim'а),
    release при ошибке скачивания, release при ошибке STT без падения воркера,
    **конкурентный claim на одно сообщение — ровно один победитель** (дедуп
    live/recovery), recovery-скан находит и корректно обрабатывает висящий кандидат,
    recovery-скан НЕ переставляет уже заклейменное сообщение в очередь, размер
    пула воркеров соответствует конфигу, один упавший job не останавливает
    обработку следующего в том же воркере, **протухший claim после жёсткого краша
    переклеймивается** (и напрямую через `claim_message_for_transcription`, и через
    recovery-скан), живой claim в пределах lease НЕ перехватывается вторым вызовом.
  - **Найдена и исправлена реальная дыра (замечание Ильи после ревью):** claim не
    имел lease/TTL — если процесс падал (`kill -9`/reboot VPS) между claim'ом
    (`transcribed_at` выставлен) и `finalize`/`release`, сообщение оставалось
    "занятым" навсегда: `transcript IS NULL AND transcribed_at IS NOT NULL` выглядит
    как живой claim, и ни обычный claim, ни recovery-скан не подбирали его повторно.
    Исправлено: обе функции (`claim_message_for_transcription` и
    `list_pending_voice_transcription_candidates`) теперь считают claim старше
    `STT_CLAIM_STALE_AFTER_SECONDS` (константа в `repositories.py`, 600 секунд —
    с большим запасом над временем скачивания+транскрибации одного сообщения)
    оборванным и доступным для повторного захвата — `transcribed_at IS NULL OR
    transcribed_at < now() - 600s` вместо строгого `IS NULL`. Это тот же паттерн
    lease/reclaim, что уже используется в `daily_summary_runs.lease_until`, просто
    без отдельной колонки (переиспользуем `transcribed_at`).
  - Прогнано: полный юнит-набор (см. ниже) и **весь** интеграционный набор на
    реальном Postgres — `pytest tests/integration/ -q` → **72 passed** после
    lease-фикса (включая
    все прочие интеграционные файлы проекта, не только daily_summary — проверка
    отсутствия регрессий от новых колонок/claim-семантики в `messages`).

## 6.1. Диагностика прогона для беты (по запросу Ильи после ревью)

- [x] Миграция `0062_daily_summary_diagnostics.py` — `daily_summary_runs.diagnostics_json`
  (nullable JSON). ORM (`DailySummaryRunModel.diagnostics_json`) и domain-сущность
  (`DailySummaryRun.diagnostics_json`) обновлены; `finalize_daily_summary_run_generated`
  принимает `diagnostics_json` как обычный параметр (не ломает существующих вызовов —
  дефолт `None`).
- [x] `LlmClient.last_retry_count` — новый атрибут (по образцу `last_usage`/`last_model`),
  выставляется в `chat_structured` (0 — с первой попытки, 1 — потребовался
  корректирующий повтор). Тест: `test_chat_structured_records_zero_retries_on_first_try_success`
  + обновлён тест на ретрай (`tests/unit/test_llm_client_structured.py`).
  Не выставляется в `chat_with_tools`/`chat_simple`/`summarize` — retry-логики там нет.
- [x] `application/daily_summary/pipeline.py`: новый `DailySummaryDiagnostics`
  (frozen dataclass) на выходе пайплайна (`DailySummaryPipelineOutput.diagnostics`),
  считает: `message_count`, `segments_total_before_truncation`/`segments_processed`,
  `segment_failures`, `cards_before_merge_count`, `structured_output_retries`
  (сумма по segment_topics+merge), `merge_fallback_used`, `themes_after_merge_count`,
  `final_themes_count`, `analyst_tool_rounds`, `analyst_tool_calls`, `analyst_fallback_used`
  (true и при исключении, и при пустом/невалидном JSON-ответе, и при исчерпании
  `MAX_ANALYST_TOOL_ROUNDS` без финального ответа — то есть "аналитик не помог"
  тоже отдельный, видимый факт, а не тихо проглоченный случай). Диагностика
  заполняется даже на fallback-ветках (пустое окно, все сегменты упали, merge упал,
  нет тем) — везде, где это осмысленно.
- [x] `presentation/daily_summary.py` сериализует `output.diagnostics` через
  `dataclasses.asdict` и передаёт в `finalize_daily_summary_run_generated`.
- [x] Тесты: 2 новых юнит-теста на `LlmClient.last_retry_count`
  (`tests/unit/test_llm_client_structured.py`), 2 новых юнит-теста на диагностику
  пайплайна (полный happy path со всеми ожидаемыми числами; сегмент падает — считается
  в `segment_failures`, остальные сегменты продолжают обрабатываться) в
  `tests/unit/test_daily_summary_pipeline.py`, плюс обновлён тест на merge-fallback
  (проверяет `diagnostics.merge_fallback_used=True`). Интеграционный тест
  `test_attempt_daily_summary_run_full_cycle_sends_and_marks_sent`
  (`tests/integration/test_daily_summary_scheduler_postgres.py`) расширен проверкой,
  что `run.diagnostics_json` реально долетает до БД со сквозным пайплайном.
- Осознанно **не сделано** в этом слайсе (не заявлено Ильёй как блокирующее для
  беты): STT-специфичные счётчики (число failures/recovered *за конкретный день*)
  не привязаны к `daily_summary_runs` — они событийные и происходят асинхронно
  относительно конкретного прогона; сейчас доступны только через логи очереди
  (`infrastructure/stt/daily_summary_queue.py`) и через прямой SQL-запрос к
  `llm_usage_log`/`messages` (`transcript IS NULL AND transcribed_at IS NOT NULL`
  = сообщения с активным/протухшим claim на конкретный момент). Время сборки одного
  прогона — грубо `sent_at - created_at` на `daily_summary_runs`, отдельного поля
  под это заводить не стали.

## 7. Интеграционные тесты (Postgres) — НЕ начато

- [ ] `test_daily_summary_claim_postgres.py` — конкурентный claim + reclaim по
  истёкшему lease.
- [ ] `test_daily_summary_pipeline.py` — весь пайплайн на fake LLM client.
- [ ] `test_cost_attribution` (может остаться юнит-тестом, если не требует реальной БД).
