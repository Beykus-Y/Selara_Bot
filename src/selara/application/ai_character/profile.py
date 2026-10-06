from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_DISPLAY_NAME = "Selara"
MAX_DISPLAY_NAME_LENGTH = 32
MAX_CUSTOM_CHARACTER_LENGTH = 500
MAX_ADDRESS_LENGTH = 64

ADDRESS_FORMS = ("ty", "vy")
REPLY_LENGTHS = ("short", "medium", "long")
MODES = ("assistant", "roleplay")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANKS = re.compile(r"[ \t]+")


class ProfileValidationError(ValueError):
    """User-facing validation failure; the message is safe to show in the chat."""


def sanitize_profile_text(text: str) -> str:
    """Neutralise user-written profile text before it is embedded in a prompt as data.

    Angle brackets are swapped for look-alikes so the text cannot close the
    ``<character_profile>`` block or open a fake one.
    """
    cleaned = _CONTROL_CHARS.sub("", text or "")
    cleaned = cleaned.replace("<", "‹").replace(">", "›")
    cleaned = "\n".join(_BLANKS.sub(" ", line).strip() for line in cleaned.splitlines())
    return cleaned.strip()


def _validate(text: str, *, limit: int, label: str, allow_empty: bool = False) -> str:
    cleaned = sanitize_profile_text(text)
    if not cleaned:
        if allow_empty:
            return ""
        raise ProfileValidationError(f"{label} не может быть пустым.")
    if len(cleaned) > limit:
        raise ProfileValidationError(f"{label}: не больше {limit} символов (сейчас {len(cleaned)}).")
    return cleaned


def validate_display_name(text: str) -> str:
    return _validate(" ".join((text or "").split()), limit=MAX_DISPLAY_NAME_LENGTH, label="Имя")


def validate_custom_character(text: str) -> str:
    return _validate(text, limit=MAX_CUSTOM_CHARACTER_LENGTH, label="Описание характера")


def validate_address(text: str) -> str:
    return _validate(" ".join((text or "").split()), limit=MAX_ADDRESS_LENGTH, label="Обращение")


@dataclass(frozen=True, slots=True)
class CharacterProfile:
    """How the assistant presents itself; owner-agnostic (user now, chat or pet later)."""

    display_name: str = DEFAULT_DISPLAY_NAME
    character_preset: str = "assistant"
    character_custom: str | None = None
    address_form: str | None = None
    formality: str = "ty"
    reply_length: str = "medium"
    emoji_enabled: bool = True
    mode: str = "assistant"

    @property
    def thread(self) -> str:
        """Roleplay keeps its own history so a scene never leaks into the assistant dialogue."""
        return "roleplay" if self.mode == "roleplay" else "assistant"
