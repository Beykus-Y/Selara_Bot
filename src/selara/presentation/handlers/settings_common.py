from __future__ import annotations

from dataclasses import dataclass
from html import escape

from selara.core.chat_settings import CHAT_SETTINGS_KEYS, ChatSettings, parse_chat_setting_value

CFG_BOOL_KEYS: set[str] = {
    "text_commands_enabled",
    "leaderboard_hybrid_buttons_enabled",
    "mafia_reveal_eliminated_role",
    "iris_view",
    "actions_18_enabled",
    "smart_triggers_enabled",
    "welcome_enabled",
    "goodbye_enabled",
    "welcome_cleanup_service_messages",
    "entry_captcha_enabled",
    "entry_captcha_kick_on_fail",
    "antiraid_enabled",
    "chat_write_locked",
    "custom_rp_enabled",
    "family_tree_enabled",
    "persona_enabled",
    "save_message",
    "interesting_facts_enabled",
    "titles_enabled",
    "craft_enabled",
    "auctions_enabled",
    "economy_enabled",
    "cleanup_economy_commands",
}

CFG_ENUM_VALUES: dict[str, tuple[str, ...]] = {
    "text_commands_locale": ("ru", "en"),
    "economy_mode": ("global", "local"),
    "persona_display_mode": ("image_only", "image_name", "title_image_name"),
}

CFG_TEXTAREA_KEYS: set[str] = {
    "welcome_text",
    "goodbye_text",
}


@dataclass(frozen=True)
class SettingMeta:
    title_ru: str
    short_ru: str
    description_ru: str
    value_hint_ru: str


SETTING_META: dict[str, SettingMeta] = {
    "top_limit_default": SettingMeta(
        title_ru="Топ по умолчанию",
        short_ru="Топ по умолчанию",
        description_ru="Сколько участников показывать в топе, если пользователь не указал число.",
        value_hint_ru="Целое число > 0.",
    ),
    "top_limit_max": SettingMeta(
        title_ru="Максимум в топе",
        short_ru="Максимум в топе",
        description_ru="Верхний предел, который можно запросить в командах топа.",
        value_hint_ru="Целое число > 0.",
    ),
    "vote_daily_limit": SettingMeta(
        title_ru="Лимит голосов кармы в день",
        short_ru="Лимит кармы/день",
        description_ru="Сколько голосов +/- один пользователь может выдать за сутки.",
        value_hint_ru="Целое число > 0.",
    ),
    "leaderboard_hybrid_buttons_enabled": SettingMeta(
        title_ru="Кнопки у гибридного топа",
        short_ru="Кнопки гибрида",
        description_ru="Показывать ли inline-кнопки переключения режима и периода под гибридным топом.",
        value_hint_ru="true/false.",
    ),
    "leaderboard_hybrid_karma_weight": SettingMeta(
        title_ru="Вес кармы в гибриде",
        short_ru="Вес кармы",
        description_ru="Насколько карма влияет на гибридный рейтинг.",
        value_hint_ru="Число 0..1. Сумма с весом активности должна быть 1.0.",
    ),
    "leaderboard_hybrid_activity_weight": SettingMeta(
        title_ru="Вес активности в гибриде",
        short_ru="Вес активности",
        description_ru="Насколько активность влияет на гибридный рейтинг.",
        value_hint_ru="Число 0..1. Сумма с весом кармы должна быть 1.0.",
    ),
    "leaderboard_7d_days": SettingMeta(
        title_ru="Длина окна рейтинга 7д",
        short_ru="Окно 7д",
        description_ru="За сколько дней считать активность и карму в коротком рейтинге.",
        value_hint_ru="Целое число > 0.",
    ),
    "leaderboard_week_start_weekday": SettingMeta(
        title_ru="Старт недели: день",
        short_ru="Старт недели: день",
        description_ru="С какого дня недели считать текущую неделю для недельных топов (0=пн ... 6=вс).",
        value_hint_ru="Целое число 0..6.",
    ),
    "leaderboard_week_start_hour": SettingMeta(
        title_ru="Старт недели: час (UTC)",
        short_ru="Старт недели: час",
        description_ru="С какого часа (UTC) начинается недельный отсчёт.",
        value_hint_ru="Целое число 0..23.",
    ),
    "mafia_night_seconds": SettingMeta(
        title_ru="Мафия: длительность ночи",
        short_ru="Мафия: ночь",
        description_ru="Сколько секунд длится ночная фаза в мафии.",
        value_hint_ru="Целое число > 0 (сек).",
    ),
    "mafia_day_seconds": SettingMeta(
        title_ru="Мафия: длительность дня",
        short_ru="Мафия: день",
        description_ru="Сколько секунд длится дневное обсуждение в мафии.",
        value_hint_ru="Целое число > 0 (сек).",
    ),
    "mafia_vote_seconds": SettingMeta(
        title_ru="Мафия: длительность голосования",
        short_ru="Мафия: голосование",
        description_ru="Сколько секунд длится голосование за исключение в мафии.",
        value_hint_ru="Целое число > 0 (сек).",
    ),
    "mafia_reveal_eliminated_role": SettingMeta(
        title_ru="Мафия: показывать роль выбывшего",
        short_ru="Мафия: показывать роль",
        description_ru="Показывать ли роль игрока после исключения.",
        value_hint_ru="true/false.",
    ),
    "text_commands_enabled": SettingMeta(
        title_ru="Текстовые команды включены",
        short_ru="Текстовые команды",
        description_ru="Разрешить команды без /, например «кто я», «топ», «ферма».",
        value_hint_ru="true/false.",
    ),
    "text_commands_locale": SettingMeta(
        title_ru="Язык текстовых команд",
        short_ru="Язык команд",
        description_ru="Язык распознавания текстовых команд.",
        value_hint_ru="ru/en.",
    ),
    "iris_view": SettingMeta(
        title_ru="Стиль активности Iris в профиле",
        short_ru="Iris-вид профиля",
        description_ru="В Telegram-профиле «кто я / кто ты / /me» показывает строку активности в стиле Iris: д | н | м | весь.",
        value_hint_ru="true/false.",
    ),
    "actions_18_enabled": SettingMeta(
        title_ru="Разрешить 18+ действия",
        short_ru="18+ действия",
        description_ru="Включает встроенные 18+ reply-реакции, ростовое действие «дрочка» и пикантные темы для «Кто я».",
        value_hint_ru="true/false.",
    ),
    "smart_triggers_enabled": SettingMeta(
        title_ru="Смарт-триггеры включены",
        short_ru="Смарт-триггеры",
        description_ru="Разрешает автоматические ответы по ключевым словам, фразам, media_file_id и шаблонным переменным вроде {user}, {chat}, {args}.",
        value_hint_ru="true/false.",
    ),
    "welcome_enabled": SettingMeta(
        title_ru="Приветствия включены",
        short_ru="Приветствия",
        description_ru="После входа бот отправляет кастомное приветствие в чат.",
        value_hint_ru="true/false.",
    ),
    "welcome_text": SettingMeta(
        title_ru="Текст приветствия",
        short_ru="Текст приветствия",
        description_ru="Поддерживает переменные {user} и {chat}. При включённой капче отправляется после успешной проверки.",
        value_hint_ru="Строка до 1000 символов.",
    ),
    "welcome_button_text": SettingMeta(
        title_ru="Текст кнопки приветствия",
        short_ru="Кнопка приветствия",
        description_ru="Необязательный текст кнопки под welcome-сообщением.",
        value_hint_ru="Пусто или короткая строка.",
    ),
    "welcome_button_url": SettingMeta(
        title_ru="Ссылка кнопки приветствия",
        short_ru="Ссылка кнопки",
        description_ru="URL для кнопки в welcome-сообщении.",
        value_hint_ru="Пусто или https://...",
    ),
    "goodbye_enabled": SettingMeta(
        title_ru="Прощания включены",
        short_ru="Прощания",
        description_ru="После выхода участника бот отправляет кастомное сообщение.",
        value_hint_ru="true/false.",
    ),
    "goodbye_text": SettingMeta(
        title_ru="Текст прощания",
        short_ru="Текст прощания",
        description_ru="Поддерживает переменные {user} и {chat}.",
        value_hint_ru="Строка до 1000 символов.",
    ),
    "welcome_cleanup_service_messages": SettingMeta(
        title_ru="Удалять сервисные сообщения входа/выхода",
        short_ru="Чистка сервисных сообщений",
        description_ru="Бот пытается удалить стандартные telegram-плашки о входе/выходе участников.",
        value_hint_ru="true/false.",
    ),
    "entry_captcha_enabled": SettingMeta(
        title_ru="Капча при входе",
        short_ru="Капча",
        description_ru="Новые участники попадают в карантин до выбора правильного эмодзи.",
        value_hint_ru="true/false.",
    ),
    "entry_captcha_timeout_seconds": SettingMeta(
        title_ru="Таймаут капчи",
        short_ru="Таймаут капчи",
        description_ru="Сколько секунд ждать ответа новичка перед исключением или сбросом.",
        value_hint_ru="Целое число > 0 (сек).",
    ),
    "entry_captcha_kick_on_fail": SettingMeta(
        title_ru="Исключать при провале капчи",
        short_ru="Кик по капче",
        description_ru="Если false, бот просто оставит пользователя в ограничении до ручного вмешательства.",
        value_hint_ru="true/false.",
    ),
    "antiraid_enabled": SettingMeta(
        title_ru="Антирейд включён",
        short_ru="Антирейд",
        description_ru="При входе новых участников бот автоматически закрывает чат и банит новых не-админов.",
        value_hint_ru="true/false.",
    ),
    "antiraid_recent_window_minutes": SettingMeta(
        title_ru="Окно добана антирейда",
        short_ru="Окно антирейда",
        description_ru="Сколько минут назад проверять уже вошедших участников при включении антирейда.",
        value_hint_ru="Только 5 или 10.",
    ),
    "chat_write_locked": SettingMeta(
        title_ru="Чат закрыт для записи",
        short_ru="Lock чата",
        description_ru="Если включено, писать могут только админы Telegram; участники получают дефолтный read-only режим.",
        value_hint_ru="true/false.",
    ),
    "custom_rp_enabled": SettingMeta(
        title_ru="Кастомные RP-действия",
        short_ru="Кастомные RP",
        description_ru="Разрешает админские кастомные соц-действия вроде «куснуть».",
        value_hint_ru="true/false.",
    ),
    "family_tree_enabled": SettingMeta(
        title_ru="Семейное древо",
        short_ru="Семья",
        description_ru="Включает команды усыновления, питомцев и генерацию древа.",
        value_hint_ru="true/false.",
    ),
    "persona_enabled": SettingMeta(
        title_ru="Образы включены",
        short_ru="Образы",
        description_ru="Админы могут выдавать чатовые декоративные образы участникам.",
        value_hint_ru="true/false.",
    ),
    "persona_display_mode": SettingMeta(
        title_ru="Режим отображения образа",
        short_ru="Режим образа",
        description_ru="Определяет, показывать ли только образ, образ с ником или титул + образ + ник.",
        value_hint_ru="image_only/image_name/title_image_name.",
    ),
    "save_message": SettingMeta(
        title_ru="Сохранять сообщения",
        short_ru="Архив сообщений",
        description_ru="Сохраняет в БД все пользовательские сообщения группы и снимки их правок для последующего анализа.",
        value_hint_ru="true/false.",
    ),
    "interesting_facts_enabled": SettingMeta(
        title_ru="Автофакты включены",
        short_ru="Автофакты",
        description_ru="Бот периодически вкидывает случайный интересный факт в чат по гибридному антиспам-правилу.",
        value_hint_ru="true/false.",
    ),
    "interesting_facts_interval_minutes": SettingMeta(
        title_ru="Минимальный интервал автофактов",
        short_ru="Интервал автофактов",
        description_ru="Минимальная пауза между двумя автофактами в этом чате.",
        value_hint_ru="Целое число > 0 (мин).",
    ),
    "interesting_facts_target_messages": SettingMeta(
        title_ru="Цель сообщений между автофактами",
        short_ru="Сообщения до факта",
        description_ru="Сколько сообщений пользователей должно накопиться после прошлого автофакта, чтобы бот мог прислать следующий раньше фазы тишины.",
        value_hint_ru="Целое число > 0.",
    ),
    "interesting_facts_sleep_cap_minutes": SettingMeta(
        title_ru="Предел сна для автофактов",
        short_ru="Предел сна автофактов",
        description_ru="Если в чате не было человеческой активности дольше этого окна, бот перестаёт пытаться оживлять его фактами.",
        value_hint_ru="Целое число > 0 (мин).",
    ),
    "titles_enabled": SettingMeta(
        title_ru="Титулы включены",
        short_ru="Титулы",
        description_ru="Пользователи могут покупать титул и видеть его перед именем в чате.",
        value_hint_ru="true/false.",
    ),
    "title_price": SettingMeta(
        title_ru="Цена титула",
        short_ru="Цена титула",
        description_ru="Стоимость первой установки титула в монетах экономики.",
        value_hint_ru="Целое число > 0.",
    ),
    "craft_enabled": SettingMeta(
        title_ru="Крафт включён",
        short_ru="Крафт",
        description_ru="Разрешает синтез рецептов из ресурсов фермы.",
        value_hint_ru="true/false.",
    ),
    "auctions_enabled": SettingMeta(
        title_ru="Аукционы включены",
        short_ru="Аукционы",
        description_ru="Разрешает запуск live-аукционов прямо в чате.",
        value_hint_ru="true/false.",
    ),
    "auction_duration_minutes": SettingMeta(
        title_ru="Длительность аукциона",
        short_ru="Длительность аукциона",
        description_ru="Сколько минут по умолчанию длится live-аукцион.",
        value_hint_ru="Целое число > 0 (мин).",
    ),
    "auction_min_increment": SettingMeta(
        title_ru="Минимальный шаг аукциона",
        short_ru="Шаг аукциона",
        description_ru="Минимальное увеличение ставки по сравнению с текущей.",
        value_hint_ru="Целое число > 0.",
    ),
    "economy_enabled": SettingMeta(
        title_ru="Экономика включена",
        short_ru="Экономика",
        description_ru="Глобально включает или выключает экономические команды в чате.",
        value_hint_ru="true/false.",
    ),
    "economy_mode": SettingMeta(
        title_ru="Режим экономики",
        short_ru="Режим экономики",
        description_ru="global — общий баланс, local — отдельная экономика для каждой группы.",
        value_hint_ru="global/local.",
    ),
    "economy_tap_cooldown_seconds": SettingMeta(
        title_ru="Кулдаун /tap",
        short_ru="Кулдаун tap",
        description_ru="Минимальная пауза между нажатиями /tap.",
        value_hint_ru="Целое число >= 10 (сек).",
    ),
    "economy_daily_base_reward": SettingMeta(
        title_ru="Базовая награда /daily",
        short_ru="Награда daily",
        description_ru="Сколько монет выдаётся за ежедневный бонус до модификаторов.",
        value_hint_ru="Целое число > 0.",
    ),
    "economy_daily_streak_cap": SettingMeta(
        title_ru="Максимум стрика /daily",
        short_ru="Лимит стрика daily",
        description_ru="Потолок серии ежедневных наград.",
        value_hint_ru="Целое число >= 1.",
    ),
    "economy_lottery_ticket_price": SettingMeta(
        title_ru="Цена лотерейного билета",
        short_ru="Цена билета",
        description_ru="Стоимость платной попытки лотереи.",
        value_hint_ru="Целое число > 0.",
    ),
    "economy_lottery_paid_daily_limit": SettingMeta(
        title_ru="Лимит платной лотереи в день",
        short_ru="Лимит лотереи/день",
        description_ru="Сколько платных билетов можно использовать за сутки.",
        value_hint_ru="Целое число > 0.",
    ),
    "economy_transfer_daily_limit": SettingMeta(
        title_ru="Лимит переводов в день",
        short_ru="Лимит переводов/день",
        description_ru="Максимальная сумма монет, которую пользователь может перевести за день.",
        value_hint_ru="Целое число > 0.",
    ),
    "economy_transfer_tax_percent": SettingMeta(
        title_ru="Налог на переводы",
        short_ru="Налог переводов",
        description_ru="Процент комиссии при переводе монет между игроками.",
        value_hint_ru="Целое число 0..100 (%).",
    ),
    "economy_market_fee_percent": SettingMeta(
        title_ru="Комиссия рынка",
        short_ru="Комиссия рынка",
        description_ru="Процент комиссии при сделках на рынке.",
        value_hint_ru="Целое число 0..100 (%).",
    ),
    "economy_negative_event_chance_percent": SettingMeta(
        title_ru="Шанс негативного события на ферме",
        short_ru="Шанс негатива",
        description_ru="Вероятность негативного события при сборе урожая.",
        value_hint_ru="Целое число 0..100 (%).",
    ),
    "economy_negative_event_loss_percent": SettingMeta(
        title_ru="Потери от негативного события",
        short_ru="Потери негатива",
        description_ru="Какую часть урожая игрок теряет при негативном событии.",
        value_hint_ru="Целое число 0..100 (%).",
    ),
    "cleanup_economy_commands": SettingMeta(
        title_ru="Чистить успешные команды экономики",
        short_ru="Чистка экономики",
        description_ru="Удалять успешную эконом-команду пользователя и ответ бота через короткую задержку, чтобы чат не захламлялся.",
        value_hint_ru="true/false.",
    ),
}


SETTINGS_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Статистика и рейтинги",
        (
            "top_limit_default",
            "top_limit_max",
            "vote_daily_limit",
            "leaderboard_hybrid_buttons_enabled",
            "leaderboard_hybrid_karma_weight",
            "leaderboard_hybrid_activity_weight",
            "leaderboard_7d_days",
            "leaderboard_week_start_weekday",
            "leaderboard_week_start_hour",
        ),
    ),
    (
        "Мафия",
        (
            "mafia_night_seconds",
            "mafia_day_seconds",
            "mafia_vote_seconds",
            "mafia_reveal_eliminated_role",
        ),
    ),
    (
        "Текстовые команды и контент",
        (
            "text_commands_enabled",
            "text_commands_locale",
            "iris_view",
            "actions_18_enabled",
            "smart_triggers_enabled",
            "welcome_enabled",
            "welcome_text",
            "welcome_button_text",
            "welcome_button_url",
            "goodbye_enabled",
            "goodbye_text",
            "welcome_cleanup_service_messages",
            "entry_captcha_enabled",
            "entry_captcha_timeout_seconds",
            "entry_captcha_kick_on_fail",
            "antiraid_enabled",
            "antiraid_recent_window_minutes",
            "chat_write_locked",
            "custom_rp_enabled",
            "family_tree_enabled",
            "save_message",
            "interesting_facts_enabled",
            "interesting_facts_interval_minutes",
            "interesting_facts_target_messages",
            "interesting_facts_sleep_cap_minutes",
        ),
    ),
    (
        "Социальные и статусные механики",
        (
            "persona_enabled",
            "persona_display_mode",
            "titles_enabled",
            "title_price",
        ),
    ),
    (
        "Экономика",
        (
            "craft_enabled",
            "auctions_enabled",
            "auction_duration_minutes",
            "auction_min_increment",
            "economy_enabled",
            "economy_mode",
            "economy_tap_cooldown_seconds",
            "economy_daily_base_reward",
            "economy_daily_streak_cap",
            "economy_lottery_ticket_price",
            "economy_lottery_paid_daily_limit",
            "economy_transfer_daily_limit",
            "economy_transfer_tax_percent",
            "economy_market_fee_percent",
            "economy_negative_event_chance_percent",
            "economy_negative_event_loss_percent",
            "cleanup_economy_commands",
        ),
    ),
)


def setting_title_ru(key: str) -> str:
    meta = SETTING_META.get(key)
    return meta.title_ru if meta is not None else key


def setting_short_ru(key: str) -> str:
    meta = SETTING_META.get(key)
    return meta.short_ru if meta is not None else key


def setting_description_ru(key: str) -> str:
    meta = SETTING_META.get(key)
    return meta.description_ru if meta is not None else "Описание отсутствует."


def setting_value_hint_ru(key: str) -> str:
    meta = SETTING_META.get(key)
    return meta.value_hint_ru if meta is not None else "Формат значения: смотрите /settings."


def render_setting_editor_text(*, chat_id: int, key: str, current_value: object) -> str:
    return (
        "<b>Редактирование настройки</b>\n"
        f"Группа: <code>{chat_id}</code>\n"
        f"<b>{escape(setting_title_ru(key))}</b>\n"
        f"Ключ: <code>{escape(key)}</code>\n"
        f"Текущее значение: <code>{escape(str(current_value))}</code>\n\n"
        f"{escape(setting_description_ru(key))}\n"
        f"<i>{escape(setting_value_hint_ru(key))}</i>"
    )


def settings_to_dict(value: ChatSettings) -> dict[str, object]:
    return {
        "top_limit_default": value.top_limit_default,
        "top_limit_max": value.top_limit_max,
        "vote_daily_limit": value.vote_daily_limit,
        "leaderboard_hybrid_buttons_enabled": value.leaderboard_hybrid_buttons_enabled,
        "leaderboard_hybrid_karma_weight": value.leaderboard_hybrid_karma_weight,
        "leaderboard_hybrid_activity_weight": value.leaderboard_hybrid_activity_weight,
        "leaderboard_7d_days": value.leaderboard_7d_days,
        "leaderboard_week_start_weekday": value.leaderboard_week_start_weekday,
        "leaderboard_week_start_hour": value.leaderboard_week_start_hour,
        "mafia_night_seconds": value.mafia_night_seconds,
        "mafia_day_seconds": value.mafia_day_seconds,
        "mafia_vote_seconds": value.mafia_vote_seconds,
        "mafia_reveal_eliminated_role": value.mafia_reveal_eliminated_role,
        "text_commands_enabled": value.text_commands_enabled,
        "text_commands_locale": value.text_commands_locale,
        "iris_view": value.iris_view,
        "actions_18_enabled": value.actions_18_enabled,
        "smart_triggers_enabled": value.smart_triggers_enabled,
        "welcome_enabled": value.welcome_enabled,
        "welcome_text": value.welcome_text,
        "welcome_button_text": value.welcome_button_text,
        "welcome_button_url": value.welcome_button_url,
        "goodbye_enabled": value.goodbye_enabled,
        "goodbye_text": value.goodbye_text,
        "welcome_cleanup_service_messages": value.welcome_cleanup_service_messages,
        "entry_captcha_enabled": value.entry_captcha_enabled,
        "entry_captcha_timeout_seconds": value.entry_captcha_timeout_seconds,
        "entry_captcha_kick_on_fail": value.entry_captcha_kick_on_fail,
        "antiraid_enabled": value.antiraid_enabled,
        "antiraid_recent_window_minutes": value.antiraid_recent_window_minutes,
        "chat_write_locked": value.chat_write_locked,
        "custom_rp_enabled": value.custom_rp_enabled,
        "family_tree_enabled": value.family_tree_enabled,
        "persona_enabled": value.persona_enabled,
        "persona_display_mode": value.persona_display_mode,
        "save_message": value.save_message,
        "interesting_facts_enabled": value.interesting_facts_enabled,
        "interesting_facts_interval_minutes": value.interesting_facts_interval_minutes,
        "interesting_facts_target_messages": value.interesting_facts_target_messages,
        "interesting_facts_sleep_cap_minutes": value.interesting_facts_sleep_cap_minutes,
        "titles_enabled": value.titles_enabled,
        "title_price": value.title_price,
        "craft_enabled": value.craft_enabled,
        "auctions_enabled": value.auctions_enabled,
        "auction_duration_minutes": value.auction_duration_minutes,
        "auction_min_increment": value.auction_min_increment,
        "economy_enabled": value.economy_enabled,
        "economy_mode": value.economy_mode,
        "economy_tap_cooldown_seconds": value.economy_tap_cooldown_seconds,
        "economy_daily_base_reward": value.economy_daily_base_reward,
        "economy_daily_streak_cap": value.economy_daily_streak_cap,
        "economy_lottery_ticket_price": value.economy_lottery_ticket_price,
        "economy_lottery_paid_daily_limit": value.economy_lottery_paid_daily_limit,
        "economy_transfer_daily_limit": value.economy_transfer_daily_limit,
        "economy_transfer_tax_percent": value.economy_transfer_tax_percent,
        "economy_market_fee_percent": value.economy_market_fee_percent,
        "economy_negative_event_chance_percent": value.economy_negative_event_chance_percent,
        "economy_negative_event_loss_percent": value.economy_negative_event_loss_percent,
        "cleanup_economy_commands": value.cleanup_economy_commands,
    }


def render_settings(current: ChatSettings, defaults: ChatSettings) -> str:
    lines = ["<b>Настройки чата</b>", "<i>Пояснения на русском, чтобы было проще настраивать.</i>"]
    current_map = settings_to_dict(current)
    default_map = settings_to_dict(defaults)

    grouped_keys: set[str] = set()
    for group_title, keys in SETTINGS_GROUPS:
        lines.append("")
        lines.append(f"<b>{escape(group_title)}</b>")
        for key in keys:
            if key not in current_map:
                continue
            grouped_keys.add(key)
            cur = current_map[key]
            dflt = default_map[key]
            marker = " (по умолчанию)" if cur == dflt else ""
            lines.append(f"• <b>{escape(setting_title_ru(key))}</b>")
            lines.append(
                f"  <code>{escape(key)}</code>: <code>{escape(str(cur))}</code> "
                f"[env: <code>{escape(str(dflt))}</code>]{marker}"
            )
            lines.append(f"  {escape(setting_description_ru(key))}")
            lines.append(f"  <i>{escape(setting_value_hint_ru(key))}</i>")

    # Fallback for any future keys that were not assigned to a group yet.
    remaining = [key for key in CHAT_SETTINGS_KEYS if key not in grouped_keys]
    if remaining:
        lines.append("")
        lines.append("<b>Прочее</b>")
        for key in remaining:
            cur = current_map[key]
            dflt = default_map[key]
            marker = " (по умолчанию)" if cur == dflt else ""
            lines.append(
                f"• <code>{escape(key)}</code>: <code>{escape(str(cur))}</code> "
                f"[env: <code>{escape(str(dflt))}</code>]{marker}"
            )

    lines.append("")
    lines.append("Изменить: <code>/setcfg &lt;key&gt; &lt;value&gt;</code>")
    lines.append("Подсказка: копируйте ключ прямо из списка выше.")
    lines.append("Пример: <code>/setcfg vote_daily_limit 30</code>")
    lines.append("Сброс к env: <code>/setcfg &lt;key&gt; default</code>")
    return "\n".join(lines)


def render_settings_compact(current: ChatSettings, defaults: ChatSettings) -> str:
    lines = ["<b>Настройки чата (кратко)</b>"]
    current_map = settings_to_dict(current)
    default_map = settings_to_dict(defaults)

    grouped_keys: set[str] = set()
    for group_title, keys in SETTINGS_GROUPS:
        lines.append("")
        lines.append(f"<b>{escape(group_title)}</b>")
        for key in keys:
            if key not in current_map:
                continue
            grouped_keys.add(key)
            cur = current_map[key]
            dflt = default_map[key]
            marker = " (по умолчанию)" if cur == dflt else ""
            lines.append(
                f"• {escape(setting_short_ru(key))}: <code>{escape(str(cur))}</code>"
                f" | <code>{escape(key)}</code>{marker}"
            )

    remaining = [key for key in CHAT_SETTINGS_KEYS if key not in grouped_keys]
    if remaining:
        lines.append("")
        lines.append("<b>Прочее</b>")
        for key in remaining:
            cur = current_map[key]
            dflt = default_map[key]
            marker = " (по умолчанию)" if cur == dflt else ""
            lines.append(
                f"• {escape(setting_short_ru(key))}: <code>{escape(str(cur))}</code>"
                f" | <code>{escape(key)}</code>{marker}"
            )

    lines.append("")
    lines.append("Подробно: <code>/settings</code>")
    return "\n".join(lines)


def split_html_message(text: str, *, max_len: int = 3500) -> list[str]:
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current_lines: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        extra = len(line) + (1 if current_lines else 0)
        if current_lines and current_len + extra > max_len:
            chunks.append("\n".join(current_lines))
            current_lines = [line]
            current_len = len(line)
            continue
        current_lines.append(line)
        current_len += extra

    if current_lines:
        chunks.append("\n".join(current_lines))

    return chunks


def validate_settings_payload(current: dict[str, object]) -> str | None:
    top_default = int(current["top_limit_default"])
    top_max = int(current["top_limit_max"])
    if top_default > top_max:
        return "top_limit_default не может быть больше top_limit_max"

    karma_weight = float(current["leaderboard_hybrid_karma_weight"])
    activity_weight = float(current["leaderboard_hybrid_activity_weight"])
    if abs((karma_weight + activity_weight) - 1.0) > 0.001:
        return "Сумма весов leaderboard_hybrid_karma_weight и leaderboard_hybrid_activity_weight должна быть 1.0"

    week_start_weekday = int(current["leaderboard_week_start_weekday"])
    if not 0 <= week_start_weekday <= 6:
        return "leaderboard_week_start_weekday должен быть в диапазоне 0..6"

    week_start_hour = int(current["leaderboard_week_start_hour"])
    if not 0 <= week_start_hour <= 23:
        return "leaderboard_week_start_hour должен быть в диапазоне 0..23"

    if int(current["economy_daily_streak_cap"]) < 1:
        return "economy_daily_streak_cap должен быть >= 1"

    if int(current["economy_tap_cooldown_seconds"]) < 10:
        return "economy_tap_cooldown_seconds должен быть >= 10"

    if int(current["entry_captcha_timeout_seconds"]) < 30:
        return "entry_captcha_timeout_seconds должен быть >= 30"

    if int(current["antiraid_recent_window_minutes"]) not in {5, 10}:
        return "antiraid_recent_window_minutes должен быть равен 5 или 10"

    if int(current["title_price"]) < 1:
        return "title_price должен быть >= 1"

    if int(current["auction_duration_minutes"]) < 1:
        return "auction_duration_minutes должен быть >= 1"

    if int(current["auction_min_increment"]) < 1:
        return "auction_min_increment должен быть >= 1"

    return None


def apply_setting_update(
    *,
    key: str,
    raw_value: str,
    current: dict[str, object],
    defaults: dict[str, object],
) -> tuple[dict[str, object] | None, str | None]:
    if key not in CHAT_SETTINGS_KEYS:
        return None, "Неизвестный ключ. Откройте /settings и скопируйте ключ из списка настроек."

    try:
        value = defaults[key] if raw_value.strip().lower() == "default" else parse_chat_setting_value(key, raw_value)
    except ValueError as exc:
        return None, str(exc)

    updated = dict(current)
    updated[key] = value

    validation_error = validate_settings_payload(updated)
    if validation_error is not None:
        return None, validation_error
    return updated, None
