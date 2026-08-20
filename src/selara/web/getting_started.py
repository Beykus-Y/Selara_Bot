from __future__ import annotations

from typing import Any

# "Selara за минуту" -- deliberately NOT a shortened USER_GUIDE.md. This page
# exists purely so a first-time user reaches something readable in under a
# minute before being pointed at the full documentation, not to duplicate
# it. Every fact here (example triggers, reply-based social actions, DM
# usage) is drawn from docs/USER_GUIDE.md's existing content, so the two
# stay consistent by construction rather than by manual upkeep.

_STEPS: tuple[dict[str, Any], ...] = (
    {
        "emoji": "👥",
        "title": "Selara работает в группах",
        "text": (
            "Если бот уже есть в вашем чате — ничего настраивать не нужно, просто "
            "попробуйте команды."
        ),
    },
    {
        "emoji": "⌨️",
        "title": "Попробуйте написать",
        "text": "Многие возможности работают и обычными словами — не обязательно запоминать команды с «/».",
        "examples": (
            ("кто я", "посмотреть себя"),
            ("топ", "посмотреть активных участников"),
            ("игра", "открыть игры"),
            ("баланс", "открыть экономику"),
            ("гача генш", "сделать крутку"),
        ),
    },
    {
        "emoji": "💬",
        "title": "Ответьте на сообщение человека",
        "text": (
            "Часть действий работает именно ответом (reply) на чьё-то сообщение — "
            "так бот точно понимает, кому адресовано действие."
        ),
        "examples": (("reply + обнять", None),),
    },
    {
        "emoji": "📩",
        "title": "Иногда Selara пишет в личку",
        "text": (
            "Это нужно, например, для скрытых ролей в играх и некоторых игровых "
            "подсказок — ничего страшного, просто откройте личный чат с ботом, "
            "если он попросит."
        ),
    },
    {
        "emoji": "📚",
        "title": "Хотите узнать больше?",
        "text": "Полная документация — по ссылкам ниже, по разделам.",
    },
)

# Links into the *existing* USER_GUIDE/ADMIN_GUIDE-equivalent web docs
# (user_docs.py / admin_docs.py) -- reusing their real, already-stable
# anchors rather than duplicating content on this page.
_NAV_LINKS: tuple[dict[str, str], ...] = (
    {"emoji": "🚀", "label": "Начало", "href": "/app/docs/user#user-docs-start"},
    {"emoji": "🎮", "label": "Игры и гача", "href": "/app/docs/user#user-docs-games"},
    {"emoji": "💰", "label": "Экономика", "href": "/app/docs/user#user-docs-economy"},
    {"emoji": "💞", "label": "Отношения и семья", "href": "/app/docs/user#user-docs-relationships"},
    {"emoji": "🎭", "label": "Соц. действия", "href": "/app/docs/user#user-docs-social"},
    {"emoji": "🛠", "label": "Для администраторов", "href": "/app/docs/admin"},
)


def build_getting_started_context() -> dict[str, Any]:
    return {
        "page_title": "Selara • Как начать",
        "page_name": "getting-started",
        "hero_title": "Как начать пользоваться Selara",
        "hero_subtitle": "Коротко — за минуту — о том, что умеет бот и как им пользоваться.",
        "steps": _STEPS,
        "nav_links": _NAV_LINKS,
        "user_docs_href": "/app/docs/user",
    }
