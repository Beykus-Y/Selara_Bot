# Анализ функциональных пробелов Selara (Python Telegram Bot)
**По состоянию: апрель 2026**

---

## 🔴 КРИТИЧЕСКИЕ / ВЫСОКИЙ ПРИОРИТЕТ

### 1. **chat_write_locked НЕ блокирует команды в группах**
**Статус:** Частичная реализация  
**Файлы:** 
- [src/selara/core/chat_settings.py](src/selara/core/chat_settings.py#L60)
- [src/selara/presentation/handlers/chat_assistant.py](src/selara/presentation/handlers/chat_assistant.py#L183-L800)

**Проблема:**
- Флаг `chat_write_locked=true` умеет закрывать чат на уровне Telegram API (ограничение разрешений участников)
- Но использует только для "interesting_facts" — не рассылаются если `chat_write_locked=true`
- **НЕ РАБОТАЕТ** как универсальный блокер команд для участников
- Пользователи могут вызывать /tap, /daily, /pair, /market и прочие команды несмотря на `chat_write_locked=true`
- Только `antiraid_enabled` применяет "retro-ban" недавних участников, но это не блокирует команды

**Где должна быть проверка:**
- Middleware уровня (перед всеми командами)
- Или в начале каждого handler-а для блокировки состояний пользователя (экономики, игр, отношений)

**Влияние:**
- Администратор может закрыть чат через `/chatlock`, но участники продолжат менять баланс, создавать пары, ставить в аукционы
- Нарушает целостность состояния в заблокированном чате

---

### 2. **text_commands_locale=en полностью отключает встроенные текстовые команды**
**Статус:** Документировано в BUGS.md как баг #1  
**Файлы:** 
- [src/selara/presentation/handlers/text_commands.py](src/selara/presentation/handlers/text_commands.py) (early return при `locale != "ru"`)

**Проблема:**
- Когда `text_commands_locale` установлен на `en`, код возвращается рано и не парсит встроенные текстовые команды
- Пользователи видят выполнение только кастомных RP и триггеров, но не `кто я`, `топ`, `актив` и д.р.
- Не реализована локализация на английский, только "отключение" встроенного функционала

**Рекомендация:**
- Реализовать английскую локализацию встроенных команд или
- Документировать что `en` на самом деле это `custom_triggers_only`

---

### 3. **Асимметричный UX для таргетирования команд (reply vs @username)**
**Статус:** Частично исправлено  
**Файлы:**
- [src/selara/presentation/handlers/text_commands.py](src/selara/presentation/handlers/text_commands.py#L2473-L2490)
- [src/selara/presentation/commands/catalog.py](src/selara/presentation/commands/catalog.py#L427-L428)

**Что исправлено:**
- Социальные действия (`обнять`, `поцеловать` и др.) теперь поддерживают `reply` + `@username` + `user_id`
- `когда был @username` работает через prefix-matching

**Что остаётся:**
- `/role game_id` работает (slash), но текстовая `роль game_id` — **НЕ работает** (catalog.py:427: `return False` для role)

---

### 4. **Блокировка команд `/chatlock` и `/chat_lock` НЕ проверяется в web-панели**
**Статус:** Отсутствует  
**Файлы:**
- [src/selara/web/app.py](src/selara/web/app.py)
- [src/selara/web/presenters.py](src/selara/web/presenters.py)

**Проблема:**
- Флаг `chat_write_locked` используется только в Telegram handlers
- Web-панель (/app) позволяет администраторам видеть и изменять состояние экономики, игр, отношений **несмотря на блокировку чата**
- Нет синхронизации web-действий с состоянием `chat_write_locked`

---

## 🟠 ВАЖНО / СРЕДНИЙ ПРИОРИТЕТ

### 5. **Интеграция между экономикой и персонажем неполная**
**Статус:** Частично реализована (обновлено)  
**Файлы:**
- [src/selara/application/use_cases/economy/growth.py](src/selara/application/use_cases/economy/growth.py)
- [src/selara/application/use_cases/economy/use_item.py](src/selara/application/use_cases/economy/use_item.py)

**Что уже работает:**
- `growth_stress_pct` — стресс персонажа влияет на кулдаун `/growth` (реализовано)
- `growth_boost_pct` — буст размера (реализован, применяется через предметы: energy_drink, pizza, veggie_salad, corn_chips)
- `growth_cooldown_discount_seconds` — скидка кулдауна (реализована, применяется через cooling_pack)
- Предметы изменяют growth-параметры через `use_item.py`

**Что отсутствует:**
- Нет системы "потребностей" персонажа (питание, уход) которая бы "деградировала" без обслуживания
- Нет механики где персонаж теряет характеристики при игнорировании

---

### 6. **Система достижений НЕ выдает награды**
**Статус:** Реализована, но без вознаграждения  
**Файлы:**
- [src/selara/application/achievements/award.py](src/selara/application/achievements/award.py)
- [src/selara/infrastructure/db/activity_batcher.py](src/selara/infrastructure/db/activity_batcher.py#L172)

**Проблема:**
- `AchievementOrchestrator` обрабатывает события и проверяет условия
- Возвращает `AchievementAwardResult` с `awarded: bool`
- Но **НЕ ПРИВЯЗАНО к экономике** — пользователь не получает монеты/предметы за достижения
- Это просто "значок", нет экономического стимула

**Влияние:**
- Система достижений работает "вхолостую"
- Нет обратной связи в виде вознаграждения

---

### 7. **Недостатки в `/pay` reply-режиме**
**Статус:** Документировано в BUGS.md  
**Файлы:**
- [src/selara/presentation/handlers/economy.py](src/selara/presentation/handlers/economy.py) — pay_command

**Проблема:**
- Reply target имеет **приоритет над @username**, что может привести к случайным переводам не туда
- Ошибка при неправильной сумме приводит к неинформативному сообщению: "Не удалось определить получателя/сумму"
- Пользователь теряется в диагностике

---

### 8. **Неполная реализация текстовых команд с аргументами**
**Статус:** Случайная разработка  
**Файлы:**
- [src/selara/presentation/commands/resolver.py](src/selara/presentation/commands/resolver.py)
- [src/selara/presentation/commands/catalog.py](src/selara/presentation/commands/catalog.py)

**Примеры:**
- `когда был @username` — не работает (exact match требует полного совпадения)
- `роль game_id` — не работает (catalog.py: `return False` для role)
- `топ карма` — работает, но `топ карма 3` — не работает

**Проблема:** Каждая команда обрабатывается отельно, нет unified prefix-matcher-а для текстовых команд с аргументами

---

## 🟡 NICE-TO-HAVE / НИЗКИЙ ПРИОРИТЕТ

### 9. **Web-панель НЕ показывает состояние `interaction_locked`**
**Статус:** Отсутствует в UI  
**Файлы:**
- [src/selara/web/templates/chat.html](src/selara/web/templates/chat.html)

**Проблема:**
- Флаг `chat_write_locked` и `antiraid_enabled` устанавливаются и снимаются через команды `/chatlock, /chat_unlock, /+антирейд, /-антирейд`
- Web-панель не показывает текущее состояние этих флагов
- Администратор должен помнить или вернуться в чат для проверки

---

### 10. **Отсутствует документация: какие команды блокируются в `chat_write_locked`**
**Статус:** Недокументировано  

**Проблема:**
- Не указано четко: какие операции разрешены/запрещены когда чат заблокирован
- Есть ли исключения для модераторов?

**Рекомендация:** Добавить таблицу в USER_GUIDE или ADMIN_GUIDE

---

### 11. **Кэширование триггеров может быть устаревшим**
**Статус:** Эвристическое кэширование (TTL уменьшен)  
**Файлы:**
- [src/selara/presentation/handlers/chat_assistant.py](src/selara/presentation/handlers/chat_assistant.py#L41)
```python
_TRIGGER_CACHE_TTL = timedelta(seconds=45)
```

**Проблема:**
- Триггеры кэшируются на 45 секунд в памяти бота (уменьшено с 5 минут)
- Если администратор обновит триггер в чате, первые 45 секунд старая версия будет использоваться
- Кэш инвалидируется при `/settrigger`, `/deltrigger`, `/rpadd`, `/rpdel` через `invalidate_chat_feature_cache()`

---

### 12. **Интеграции Telegram ↔ Web асинхронны и потенциально несинхронизированы**
**Статус:** Работает, но без гарантий  

**Проблема:**
- Telegram handler и web API работают независимо
- Если web pangel измени данные и в то же время Telegram обновит то же - race condition
- Нет явного лока/версионирования для состояния

---

## 📋 ТАБЛИЦА ПРИОРИТЕТА

| № | Проблема | Критичность | Область | Усилия | Статус |
|---|----------|-------------|---------|--------|--------|
| 1 | chat_write_locked не блокирует команды | 🔴 КРИТИЧ | Moderation | Средние | Частично реализовано |
| 2 | locale=en отключает встроенные команды | 🔴 КРИТИЧ | Text Commands | Средние | Известно, документировано |
| 3 | Асимметричный UX таргетирования | 🔴 КРИТИЧ | UX/DX | Средние | Известно, документировано |
| 4 | Web не знает о chat_write_locked | 🔴 КРИТИЧ | Web Sync | Малые | Отсутствует |
| 5 | Экономика↔персонаж неполная | 🟠 ВАЖНО | Economy/RPG | Средние | Growth boost/cooldown реализованы |
| 6 | Достижения без вознаграждения | 🟠 ВАЖНО | Gamification | Средние | Реализовано пусто |
| 7 | /pay issues (UX + logic) | 🟠 ВАЖНО | Economy | Малые | Известно |
| 8 | Текстовые команды с аргументами | 🟠 ВАЖНО | Text Commands | Средние | Случайная реализация |
| 9 | Web UI для блокировки | 🟡 NICE | Web | Малые | Отсутствует |
| 10 | Документация блокировки | 🟡 NICE | Docs | Малые | Отсутствует |
| 11 | Триггеры TTL кэш (45с) | 🟡 NICE | Caching | Малые | TTL уменьшен, менее критично |
| 12 | Telegram↔Web race conditions | 🟡 NICE | Concurrency | Средние | Теоретическая проблема |

---

## 🔍 РЕКОМЕНДАЦИИ ПО ДАЛЬНЕЙШЕЙ РАБОТЕ

### Фаза 1 (Критичные, 1-2 недели)
1. **Добавить middleware для блокировки команд при `chat_write_locked=true`**
   - Проверка перед всеми handlers (game, economy, relationships, социальные действия)
   - Возврат "Чат заблокирован администратором" с игнорированием действия

2. **Исправить locale=en**
   - Либо реализовать англ. локализацию, либо документировать как `custom_triggers_only_mode`

3. **Унифицировать таргетирование (reply/@username/user_id)**
   - Создать общий адаптер `resolve_target(message, args)` 
   - Использовать его везде вместо разных реализаций

### Фаза 2 (Важные, 2-3 недели)
4. Добавить web-UI для видения **chat_write_locked** и **antiraid_enabled**
5. Привязать систему достижений к экономическому вознаграждению
6. ~~Уточнить логику growth_boost и применять её где нужно~~ — **реализовано**
6. Реализовать систему "потребностей" персонажа (деградация без обслуживания)

### Фаза 3 (Nice-to-have, когда будет время)
7. Расширить текстовые команды через prefix-matcher
8. Добавить версионирование данных для предотвращения race conditions
9. Обновить документацию ADMIN_GUIDE о поведении при блокировке

---

## 📌 ФАЙЛЫ ДЛЯ ФОКУСА

**Ключевые paths для работы:**

**Middleware уровня (блокировка):**
- `src/selara/presentation/middlewares/` — добавить check для `chat_write_locked`

**Текстовые команды:**
- `src/selara/presentation/commands/resolver.py` — унифицировать prefix-matching
- `src/selara/presentation/handlers/text_commands.py` — использовать unified resolver

**Web синхронизация:**
- `src/selara/web/app.py` — читать `chat_write_locked` состояние
- `src/selara/web/presenters.py` — отображать блокировку

**Таргетирование команд:**
- `src/selara/presentation/handlers/relationships.py` — рефакторить `_resolve_target_user`
- `src/selara/presentation/handlers/economy.py` — унифицировать `_resolve_*` функции

**Достижения & вознаграждения:**
- `src/selara/application/achievements/award.py` — добавить экономический вывод
- `src/selara/infrastructure/db/activity_batcher.py` — обработать reward вывод

---

**Дата анализа:** апрель 2026  
**Метод:** Статический анализ кода + сопоставление с документацией (README, USER_GUIDE, BUGS.md, MISSING_LOGIC.md)
