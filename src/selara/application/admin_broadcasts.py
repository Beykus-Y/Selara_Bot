from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from html import escape
from io import BytesIO
from typing import Literal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from PIL import Image, UnidentifiedImageError

from selara.domain.reactions import normalize_telegram_reaction_emoji

ReactionMode = Literal["none", "native", "inline"]

_BLOCK_OPEN = "[reactions]"
_BLOCK_CLOSE = "[/reactions]"
_MAX_OPTIONS = 6
_MAX_LABEL_LENGTH = 64
_MAX_EMOJI_LENGTH = 32
_MAX_PHOTO_BYTES = 10 * 1024 * 1024
_EMOJI_CODEPOINT_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002300-\U000023FF"
    "\U00002B00-\U00002BFF"
    "]"
)
_REGIONAL_INDICATOR_RE = re.compile("[\U0001F1E6-\U0001F1FF]")


class BroadcastFormatError(ValueError):
    """The administrator supplied an unsafe or ambiguous broadcast source."""


@dataclass(frozen=True, slots=True)
class BroadcastReactionOption:
    key: str
    emoji: str
    label: str


@dataclass(frozen=True, slots=True)
class ParsedBroadcast:
    body: str
    rendered_text: str
    options: tuple[BroadcastReactionOption, ...]


@dataclass(frozen=True, slots=True)
class ValidatedPhoto:
    filename: str
    content_type: str
    format: str
    width: int
    height: int
    size: int


def _looks_like_standard_emoji(value: str) -> bool:
    if not value or len(value) > _MAX_EMOJI_LENGTH or any(char.isspace() for char in value):
        return False
    if any(char.isalpha() for char in value):
        return False
    ascii_chars = [char for char in value if ord(char) < 128]
    is_keycap = "\u20e3" in value and value[0] in "#*0123456789"
    if ascii_chars and not (is_keycap and ascii_chars == [value[0]]):
        return False
    if _EMOJI_CODEPOINT_RE.search(value) or _REGIONAL_INDICATOR_RE.search(value):
        return True
    # Keycap emoji are made from an ASCII digit/#/*, an optional variation selector,
    # and COMBINING ENCLOSING KEYCAP.
    return is_keycap


def parse_broadcast_source(source: str) -> ParsedBroadcast:
    normalized = (source or "").strip()
    if not normalized:
        raise BroadcastFormatError("Введите текст сообщения.")

    open_count = normalized.count(_BLOCK_OPEN)
    close_count = normalized.count(_BLOCK_CLOSE)
    if open_count == 0 and close_count == 0:
        return ParsedBroadcast(body=normalized, rendered_text=normalized, options=())
    if open_count == 0:
        raise BroadcastFormatError("Найден закрывающий блок [/reactions] без открывающего.")
    if close_count == 0:
        raise BroadcastFormatError("Блок [reactions] не закрыт тегом [/reactions].")
    if open_count != 1 or close_count != 1:
        raise BroadcastFormatError("В сообщении разрешён только один блок [reactions].")

    open_at = normalized.index(_BLOCK_OPEN)
    close_at = normalized.index(_BLOCK_CLOSE)
    if close_at < open_at:
        raise BroadcastFormatError("Закрывающий блок реакций расположен раньше открывающего.")
    if normalized[close_at + len(_BLOCK_CLOSE) :].strip():
        raise BroadcastFormatError("Блок [reactions] должен находиться в конце сообщения.")

    body = normalized[:open_at].strip()
    if not body:
        raise BroadcastFormatError("Перед блоком реакций должен быть текст сообщения.")

    raw_options = normalized[open_at + len(_BLOCK_OPEN) : close_at].strip()
    lines = [line.strip() for line in raw_options.splitlines() if line.strip()]
    if len(lines) < 2:
        raise BroadcastFormatError("Укажите минимум 2 варианта реакции.")
    if len(lines) > _MAX_OPTIONS:
        raise BroadcastFormatError(f"Разрешено не больше {_MAX_OPTIONS} вариантов реакции.")

    options: list[BroadcastReactionOption] = []
    seen_emoji: set[str] = set()
    for position, line in enumerate(lines, start=1):
        if "=" not in line:
            raise BroadcastFormatError(
                f"Строка {position} блока реакций должна иметь вид: emoji = описание."
            )
        emoji, label = (part.strip() for part in line.split("=", 1))
        if not _looks_like_standard_emoji(emoji):
            raise BroadcastFormatError(f"В строке {position} слева должен быть обычный emoji.")
        emoji_identity = normalize_telegram_reaction_emoji(emoji)
        if emoji_identity in seen_emoji:
            raise BroadcastFormatError(f"Emoji {emoji} повторяется в блоке реакций.")
        if not label:
            raise BroadcastFormatError(f"У реакции {emoji} отсутствует описание.")
        if len(label) > _MAX_LABEL_LENGTH:
            raise BroadcastFormatError(
                f"Описание реакции {emoji} длиннее {_MAX_LABEL_LENGTH} символов."
            )
        seen_emoji.add(emoji_identity)
        options.append(BroadcastReactionOption(key=f"r{position}", emoji=emoji, label=label))

    footer = "<b>Реакции:</b>\n" + "\n".join(
        f"{option.emoji} — {escape(option.label)}" for option in options
    )
    return ParsedBroadcast(
        body=body,
        rendered_text=f"{body}\n\n{footer}",
        options=tuple(options),
    )


def resolve_reaction_mode(
    *,
    options: Sequence[BroadcastReactionOption],
    bot_is_admin: bool,
    available_reactions: Collection[str] | None,
) -> ReactionMode:
    if not options:
        return "none"
    if not bot_is_admin:
        return "inline"
    if available_reactions is None:
        return "native"
    requested = {normalize_telegram_reaction_emoji(option.emoji) for option in options}
    available = {normalize_telegram_reaction_emoji(emoji) for emoji in available_reactions}
    return "native" if requested.issubset(available) else "inline"


def build_inline_keyboard(
    *,
    delivery_id: int,
    options: Sequence[BroadcastReactionOption],
) -> InlineKeyboardMarkup:
    if delivery_id <= 0:
        raise ValueError("delivery_id must be positive")
    buttons = [
        InlineKeyboardButton(
            text=option.emoji,
            callback_data=f"abr:{delivery_id}:{option.key}",
        )
        for option in options
    ]
    if any(len((button.callback_data or "").encode("utf-8")) > 64 for button in buttons):
        raise ValueError("Telegram callback_data limit exceeded")
    return InlineKeyboardMarkup(inline_keyboard=[buttons[index : index + 3] for index in range(0, len(buttons), 3)])


def validate_broadcast_photo(*, filename: str, content_type: str, content: bytes) -> ValidatedPhoto:
    safe_name = (filename or "").strip()
    if not safe_name or not content:
        raise BroadcastFormatError("Файл фотографии не выбран.")
    if len(content) > _MAX_PHOTO_BYTES:
        raise BroadcastFormatError("Фотография должна быть не больше 10 МБ.")
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_type not in {"image/jpeg", "image/png"}:
        raise BroadcastFormatError("Поддерживаются только фотографии JPEG или PNG.")
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
        with Image.open(BytesIO(content)) as image:
            image_format = (image.format or "").upper()
            width, height = image.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise BroadcastFormatError("Файл фотографии повреждён или имеет неизвестный формат.") from exc
    if image_format not in {"JPEG", "PNG"}:
        raise BroadcastFormatError("Поддерживаются только фотографии JPEG или PNG.")
    if width <= 0 or height <= 0 or width + height > 10_000:
        raise BroadcastFormatError("Недопустимые размеры фотографии: сумма сторон должна быть не больше 10000.")
    ratio = max(width / height, height / width)
    if ratio > 20:
        raise BroadcastFormatError("Соотношение сторон фотографии не должно превышать 20:1.")
    expected_type = "image/jpeg" if image_format == "JPEG" else "image/png"
    if normalized_type != expected_type:
        raise BroadcastFormatError("Тип файла не совпадает с фактическим форматом фотографии.")
    return ValidatedPhoto(
        filename=safe_name[:255],
        content_type=expected_type,
        format=image_format,
        width=int(width),
        height=int(height),
        size=len(content),
    )
