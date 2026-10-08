"""Shared navigation constants and callback-data helper.

Keeping the labels and the 64-byte callback limit in one place lets the /start
and /help screens use the same wording and the same back/home behaviour.
Nothing in this module talks to Telegram.
"""

from __future__ import annotations

BACK_LABEL = "⬅️ Назад"
HOME_LABEL = "🏠 Главное"
# Inside the feature catalog "home" means the list of areas, not the bot's main panel.
SECTIONS_LABEL = "🏠 Разделы"
CANCEL_LABEL = "❌ Отмена"

# Telegram rejects callback_data longer than 64 bytes (counted in UTF-8).
MAX_CALLBACK_DATA_BYTES = 64
CALLBACK_SEPARATOR = ":"

# New prefix for tree navigation. It must not collide with the existing
# prefixes (pm, help, pai, premium) or the deep links game_/eco_.
NAV_CALLBACK_PREFIX = "nv"


def callback_data_size(data: str) -> int:
    return len(data.encode("utf-8"))


def safe_callback(prefix: str, *parts: str) -> str:
    """Join callback parts with ':' and refuse values Telegram would reject."""
    for part in (prefix, *parts):
        if CALLBACK_SEPARATOR in part:
            raise ValueError(f"callback part {part!r} must not contain {CALLBACK_SEPARATOR!r}")
    data = CALLBACK_SEPARATOR.join((prefix, *parts))
    size = callback_data_size(data)
    if size > MAX_CALLBACK_DATA_BYTES:
        raise ValueError(f"callback_data is {size} bytes, limit is {MAX_CALLBACK_DATA_BYTES}: {data!r}")
    return data
