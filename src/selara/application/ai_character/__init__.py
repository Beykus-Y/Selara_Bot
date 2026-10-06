"""Shared AI character layer: profile, presets and the prompt builder.

Pure Python on purpose (no aiogram, no SQLAlchemy) so the same builder can later serve
a user's private chat, a group character and AI pets.
"""

from selara.application.ai_character.profile import (
    ADDRESS_FORMS,
    MAX_ADDRESS_LENGTH,
    MAX_CUSTOM_CHARACTER_LENGTH,
    MAX_DISPLAY_NAME_LENGTH,
    MODES,
    REPLY_LENGTHS,
    CharacterProfile,
    ProfileValidationError,
    sanitize_profile_text,
    validate_address,
    validate_custom_character,
    validate_display_name,
)
from selara.application.ai_character.presets import CHARACTER_PRESETS, CUSTOM_PRESET_KEY, preset_title
from selara.application.ai_character.prompt_builder import HistoryMessage, build_personal_messages

__all__ = [
    "ADDRESS_FORMS",
    "CHARACTER_PRESETS",
    "CUSTOM_PRESET_KEY",
    "MAX_ADDRESS_LENGTH",
    "MAX_CUSTOM_CHARACTER_LENGTH",
    "MAX_DISPLAY_NAME_LENGTH",
    "MODES",
    "REPLY_LENGTHS",
    "CharacterProfile",
    "HistoryMessage",
    "ProfileValidationError",
    "build_personal_messages",
    "preset_title",
    "sanitize_profile_text",
    "validate_address",
    "validate_custom_character",
    "validate_display_name",
]
