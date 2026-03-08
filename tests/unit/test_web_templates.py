from pathlib import Path

from selara.web.rendering import create_template_environment


def test_chat_template_renders_settings_sections_items_key() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)

    html = environment.get_template("chat.html").render(
        page_title="Selara",
        page_name="chat",
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        chat_title="Test Chat",
        hero_subtitle="Test subtitle",
        chat_id=123,
        metrics=[],
        dashboard_panels=[],
        access_rows=[],
        roles=[],
        command_rules=[],
        leaderboards=[],
        aliases=[],
        triggers=[],
        trigger_template_quick_rows=[
            {"token": "{user}", "description": "Отправитель"},
            {"token": "{args}", "description": "Аргументы"},
        ],
        trigger_template_examples=[
            "Сейчас тут {user}, чат: {chat}",
        ],
        trigger_template_docs_url="/app/docs/admin?chat_id=123#docs-trigger-variables",
        audit_rows=[],
        can_manage_settings=False,
        manage_settings_tone="ok",
        manage_settings_note="Настройки доступны.",
        settings_sections=[
            {
                "title": "Основное",
                "items": [
                    {
                        "title": "Режим экономики",
                        "key": "economy_mode",
                        "description": "Описание настройки.",
                        "current_value": "global",
                        "default_value": "global",
                        "hint": "Подсказка.",
                        "editable": False,
                        "input_kind": "text",
                        "options": [],
                    }
                ],
            }
        ],
    )

    assert "Режим экономики" in html
    assert "Смарт-триггеры" in html
    assert "{user}" in html
    assert "/app/docs/admin?chat_id=123#docs-trigger-variables" in html


def test_landing_template_renders_core_sections() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)

    html = environment.get_template("landing.html").render(
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        page_title="Selara • Лендинг",
        page_name="landing",
        home_href="/",
        brand_subtitle="бот для Telegram-групп",
        hero_eyebrow="Платформа для Telegram-сообществ",
        hero_title_primary="Selara",
        hero_title_secondary="бот для групп, игр и экономики",
        hero_subtitle="Описание лендинга.",
        hero_ctas=[
            {"href": "/login", "label": "Войти через Telegram", "variant": "primary"},
            {"href": "/app/docs/user", "label": "Что умеет бот", "variant": "ghost"},
        ],
        session_note=None,
        developer_credit="Разработчик: Beykus",
        signal_cards=[
            {"label": "команды", "value": "slash + text + reply", "note": "маршруты", "tone": "cyan"},
            {"label": "игры", "value": "7 live-режимов", "note": "game hub", "tone": "violet"},
        ],
        metrics=[
            {"label": "Игры", "value": "8", "note": "метрика", "tone": "violet"},
            {"label": "Экономика", "value": "global / local", "note": "метрика", "tone": "cyan"},
        ],
        overview_text="Обзор Selara.",
        overview_pills=["игры", "экономика", "документация"],
        step_cards=[
            {"step": "01", "title": "Откройте бота", "text": "Шаг 1"},
            {"step": "02", "title": "Получите код", "text": "Шаг 2"},
        ],
        feature_cards=[
            {
                "kicker": "слой",
                "title": "Команды",
                "text": "Описание",
                "items": ["/help", "/me"],
                "href": "/app/docs/user",
                "link_label": "Открыть документацию",
            },
            {
                "kicker": "слой",
                "title": "Игры",
                "text": "Описание",
                "items": ["Кто я", "Мафия"],
                "href": "/app/games",
                "link_label": "Открыть игры",
            },
        ],
        route_cards=[
            {
                "title": "Вход",
                "href": "/login",
                "display_href": "/login",
                "description": "Авторизация",
                "note": "без пароля",
            },
            {
                "title": "Игровой центр",
                "href": "/app/games",
                "display_href": "/app/games",
                "description": "Игры",
                "note": "живые игры",
            },
        ],
    )

    assert "Разработчик: Beykus" in html
    assert "бот для групп, игр и экономики" in html
    assert "/app/docs/user" in html
    assert "/app/games" in html
    assert "Войти через Telegram" in html


def test_login_template_renders_bot_username_and_steps() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)

    html = environment.get_template("login.html").render(
        page_title="Selara • Вход",
        page_name="login",
        home_href="/",
        brand_subtitle="бот для Telegram-групп",
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        bot_username="selara_ru_bot",
        bot_dm_url="https://t.me/selara_ru_bot",
    )

    assert "@selara_ru_bot" in html
    assert "Вход в Selara" in html
    assert "Открыть бота" in html
    assert "/login" in html


def test_error_template_renders_status_actions() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)

    html = environment.get_template("error.html").render(
        page_title="Selara • 404",
        page_name="error",
        home_href="/",
        brand_subtitle="бот для Telegram-групп",
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        status_code="404",
        status_label="не найдено",
        headline="Страница не найдена",
        message="Такого адреса нет.",
        action_links=[
            {"href": "/", "label": "На главную", "variant": "ghost"},
            {"href": "/login", "label": "Войти через Telegram", "variant": "primary"},
        ],
    )

    assert "Страница не найдена" in html
    assert "404" in html
    assert "Войти через Telegram" in html


def test_admin_docs_template_renders_trigger_variables() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)

    html = environment.get_template("admin_docs.html").render(
        page_title="Selara • Документация администратора",
        page_name="admin-docs",
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        hero_title="Документация администратора",
        hero_subtitle="Описание",
        origin_chat=None,
        trigger_match_types=[
            {"code": "exact", "label": "Точное совпадение", "description": "Описание"},
        ],
        trigger_template_variable_groups=[
            {
                "title": "Отправитель",
                "items": [
                    {
                        "token": "{user}",
                        "label": "Отправитель упоминанием",
                        "description": "HTML-упоминание автора.",
                        "availability": "смарт-триггеры и RP",
                        "aliases": "{actor}, {sender}",
                    }
                ],
            }
        ],
        docs_sections=[],
        settings_docs_sections=[],
    )

    assert "Переменные шаблонов" in html
    assert "{user}" in html
    assert "{actor}, {sender}" in html


def test_audit_template_renders_rows() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)

    html = environment.get_template("audit.html").render(
        page_title="Selara Audit",
        page_name="audit",
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        chat_title="Test Chat",
        chat_id=123,
        audit_rows=[
            {
                "when": "2026-03-08 10:00 UTC",
                "action": "web_setting_updated",
                "description": "Обновлена настройка.",
                "actor": "1",
                "target": "—",
            }
        ],
    )

    assert "Лента событий" in html
    assert "web_setting_updated" in html


def test_games_template_renders_active_cards() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)

    html = environment.get_template("games.html").render(
        page_title="Selara",
        page_name="games",
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        hero_title="Активные игры",
        hero_subtitle="Проверка шаблона",
        metrics=[],
        game_cards=[
            {
                "title": "Дуэль кубиков",
                "description": "Бросьте кубик",
                "status": "Свободная игра",
                "status_badge": "active",
                "chat_title": "Тестовый чат",
                "chat_id": "123",
                "players_count": 2,
                "round_no": "1",
                "created_at": "2026-03-07 00:00 UTC",
                "started_at": "2026-03-07 00:01 UTC",
                "players_preview": ["u1", "u2"],
                "players_hidden": 0,
                "secret_lines": [],
                "winner_text": None,
                "board_rows": [
                    [
                        {
                            "kind": "action",
                            "label": "🎲 Бросить",
                            "callback_data": "gdice:test:roll",
                            "variant": "primary",
                        }
                    ]
                ],
                "private_rows": [],
                "show_number_guess": False,
                "show_bred_answer": False,
                "game_id": "test",
            }
        ],
    )

    assert "Дуэль кубиков" in html
    assert "🎲 Бросить" in html


def test_games_dashboard_template_renders_whoami_theme_picker() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)

    html = environment.get_template("_games_dashboard.html").render(
        metrics=[],
        game_catalog=[
            {
                "key": "whoami",
                "title": "Кто я",
                "description": "Карточки на лбу и вопросы с ответом да или нет.",
                "min_players_label": "от 2 игроков",
                "mode_label": "скрытые роли",
                "note": "Партия на угадывание персонажа.",
                "tone": "blue",
            }
        ],
        spy_category_options=[],
        whoami_category_options=[
            {"value": "", "label": "Случайная тема", "note": "Без фиксации заранее", "count": "", "is_18_plus": False},
            {"value": "18+ и пикантное", "label": "18+ и пикантное", "note": "Готовая тема для партии", "count": "20", "is_18_plus": True},
            {"value": "Genshin Impact", "label": "Genshin Impact", "note": "Готовая тема для партии", "count": "30", "is_18_plus": False},
            {"value": "Honkai: Star Rail", "label": "Honkai: Star Rail", "note": "Готовая тема для партии", "count": "30", "is_18_plus": False},
        ],
        default_create_kind="whoami",
        default_create_game={
            "key": "whoami",
            "title": "Кто я",
            "description": "Карточки на лбу и вопросы с ответом да или нет.",
            "min_players_label": "от 2 игроков",
            "mode_label": "скрытые роли",
            "note": "Партия на угадывание персонажа.",
            "tone": "blue",
        },
        create_chat_options=[{"chat_id": "1", "title": "Тестовый чат", "actions_18_enabled": "false"}],
        busy_create_chat_options=[],
        has_manageable_chats=True,
        game_cards=[],
        recent_game_cards=[],
    )

    assert "Тема для «Кто я»" in html
    assert "18+ и пикантное" in html
    assert "Genshin Impact" in html
    assert "Honkai: Star Rail" in html
    assert 'data-whoami-category-explicit="true"' in html
    assert 'data-actions-18-enabled="false"' in html
    assert 'data-create-whoami-panel' in html
    assert 'aria-hidden="false"' in html


def test_games_dashboard_template_renders_safe_only_whoami_lobby_picker() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)

    html = environment.get_template("_games_dashboard.html").render(
        metrics=[],
        game_catalog=[],
        spy_category_options=[],
        whoami_category_options=[],
        default_create_kind="spy",
        default_create_game=None,
        create_chat_options=[],
        busy_create_chat_options=[],
        has_manageable_chats=False,
        game_cards=[
            {
                "kind": "whoami",
                "title": "Кто я",
                "description": "Карточки на лбу и вопросы.",
                "status": "Лобби",
                "status_badge": "lobby",
                "chat_title": "Тестовый чат",
                "chat_id": "123",
                "players_count": 3,
                "round_no": "1",
                "created_at": "2026-03-07 00:00 UTC",
                "started_at": None,
                "players_preview": ["u1", "u2", "u3"],
                "players_hidden": 0,
                "secret_lines": [],
                "winner_text": None,
                "main_buttons": [],
                "manage_buttons": [],
                "category_buttons": [],
                "vote_buttons": [],
                "telegram_buttons": [],
                "private_buttons": [],
                "show_number_guess": False,
                "show_bred_answer": False,
                "game_id": "test",
                "spy_theme_picker": None,
                "whoami_theme_picker": {
                    "game_id": "test",
                    "current_value": "",
                    "current_label": "Случайная тема",
                    "options": [
                        {"value": "", "label": "Случайная тема", "count": "", "is_18_plus": False},
                        {"value": "Genshin Impact", "label": "Genshin Impact", "count": "30", "is_18_plus": False},
                    ],
                },
            }
        ],
        recent_game_cards=[],
    )

    assert "Genshin Impact" in html
    assert "18+ и пикантное" not in html


def test_games_dashboard_template_hides_create_whoami_theme_when_other_game_selected() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)

    html = environment.get_template("_games_dashboard.html").render(
        metrics=[],
        game_catalog=[
            {
                "key": "bredovukha",
                "title": "Бредовуха",
                "description": "Блеф, ложь и угадывание правильного факта.",
                "min_players_label": "от 3 игроков",
                "mode_label": "общий экран",
                "note": "Партия на блеф.",
                "tone": "gold",
            },
            {
                "key": "whoami",
                "title": "Кто я",
                "description": "Карточки на лбу и вопросы с ответом да или нет.",
                "min_players_label": "от 2 игроков",
                "mode_label": "скрытые роли",
                "note": "Партия на угадывание персонажа.",
                "tone": "blue",
            },
        ],
        spy_category_options=[
            {"value": "", "label": "Случайная тема", "note": "Без фиксации заранее", "count": ""},
            {"value": "Транспорт и логистика", "label": "Транспорт и логистика", "note": "Готовая тема для партии", "count": "16"},
        ],
        whoami_category_options=[
            {"value": "", "label": "Случайная тема", "note": "Без фиксации заранее", "count": "", "is_18_plus": False},
            {"value": "Genshin Impact", "label": "Genshin Impact", "note": "Готовая тема для партии", "count": "30", "is_18_plus": False},
        ],
        default_create_kind="bredovukha",
        default_create_game={
            "key": "bredovukha",
            "title": "Бредовуха",
            "description": "Блеф, ложь и угадывание правильного факта.",
            "min_players_label": "от 3 игроков",
            "mode_label": "общий экран",
            "note": "Партия на блеф.",
            "tone": "gold",
        },
        create_chat_options=[{"chat_id": "1", "title": "Тестовый чат", "actions_18_enabled": "true"}],
        busy_create_chat_options=[],
        has_manageable_chats=True,
        game_cards=[],
        recent_game_cards=[],
    )

    assert 'data-create-whoami-panel' in html
    assert 'aria-hidden="true"' in html
    assert 'hidden style="display: none;"' in html
    assert 'Бредовуха' in html


def test_games_dashboard_template_renders_spy_theme_picker() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)

    html = environment.get_template("_games_dashboard.html").render(
        metrics=[],
        game_catalog=[
            {
                "key": "spy",
                "title": "Найди шпиона",
                "description": "Шпион не знает локацию, мирные знают.",
                "min_players_label": "от 3 игроков",
                "mode_label": "скрытые роли",
                "note": "Социальная дедукция с одной скрытой ролью.",
                "tone": "pink",
            }
        ],
        spy_category_options=[
            {"value": "", "label": "Случайная тема", "note": "Без фиксации заранее", "count": ""},
            {"value": "Транспорт и логистика", "label": "Транспорт и логистика", "note": "Готовая тема для партии", "count": "16"},
            {"value": "Отдых и туризм", "label": "Отдых и туризм", "note": "Готовая тема для партии", "count": "14"},
        ],
        whoami_category_options=[],
        default_create_kind="spy",
        default_create_game={
            "key": "spy",
            "title": "Найди шпиона",
            "description": "Шпион не знает локацию, мирные знают.",
            "min_players_label": "от 3 игроков",
            "mode_label": "скрытые роли",
            "note": "Социальная дедукция с одной скрытой ролью.",
            "tone": "pink",
        },
        create_chat_options=[{"chat_id": "1", "title": "Тестовый чат", "actions_18_enabled": "true"}],
        busy_create_chat_options=[],
        has_manageable_chats=True,
        game_cards=[],
        recent_game_cards=[],
    )

    assert "Тема для «Найди шпиона»" in html
    assert "Транспорт и логистика" in html
    assert "Отдых и туризм" in html
    assert 'data-create-spy-panel' in html
    assert 'aria-hidden="false"' in html


def test_games_dashboard_template_renders_spy_lobby_picker() -> None:
    template_dir = Path(__file__).resolve().parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)

    html = environment.get_template("_games_dashboard.html").render(
        metrics=[],
        game_catalog=[],
        spy_category_options=[],
        whoami_category_options=[],
        default_create_kind="whoami",
        default_create_game=None,
        create_chat_options=[],
        busy_create_chat_options=[],
        has_manageable_chats=False,
        game_cards=[
            {
                "kind": "spy",
                "title": "Найди шпиона",
                "description": "Шпион не знает локацию.",
                "status": "Лобби",
                "status_badge": "lobby",
                "chat_title": "Тестовый чат",
                "chat_id": "123",
                "players_count": 4,
                "round_no": "1",
                "created_at": "2026-03-07 00:00 UTC",
                "started_at": None,
                "players_preview": ["u1", "u2", "u3", "u4"],
                "players_hidden": 0,
                "secret_lines": [],
                "winner_text": None,
                "main_buttons": [],
                "manage_buttons": [],
                "category_buttons": [],
                "vote_buttons": [],
                "telegram_buttons": [],
                "private_buttons": [],
                "show_number_guess": False,
                "show_bred_answer": False,
                "game_id": "spy-test",
                "spy_theme_picker": {
                    "game_id": "spy-test",
                    "current_value": "Транспорт и логистика",
                    "current_label": "Транспорт и логистика",
                    "options": [
                        {"value": "", "label": "Случайная тема", "count": ""},
                        {"value": "Транспорт и логистика", "label": "Транспорт и логистика", "count": "16"},
                    ],
                },
                "whoami_theme_picker": None,
            }
        ],
        recent_game_cards=[],
    )

    assert "Для «Шпиона» тема определяет пул локаций раунда." in html
    assert 'name="spy_category"' in html
    assert 'data-spy-category-picker' in html
