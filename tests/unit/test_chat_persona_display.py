from selara.core.chat_settings import (
    PERSONA_DISPLAY_MODE_IMAGE_NAME,
    PERSONA_DISPLAY_MODE_IMAGE_ONLY,
    PERSONA_DISPLAY_MODE_TITLE_IMAGE_NAME,
)
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository


def test_compose_chat_display_name_image_only_mode() -> None:
    value = SqlAlchemyActivityRepository._compose_chat_display_name(
        user_id=10,
        username="real_username",
        first_name="Real",
        last_name="Name",
        chat_display_name=None,
        title_prefix="Лорд",
        persona_label="Венти",
        persona_enabled=True,
        persona_display_mode=PERSONA_DISPLAY_MODE_IMAGE_ONLY,
    )

    assert value == "[Венти]"


def test_compose_chat_display_name_image_name_mode() -> None:
    value = SqlAlchemyActivityRepository._compose_chat_display_name(
        user_id=10,
        username="real_username",
        first_name="Real",
        last_name="Name",
        chat_display_name=None,
        title_prefix=None,
        persona_label="Венти",
        persona_enabled=True,
        persona_display_mode=PERSONA_DISPLAY_MODE_IMAGE_NAME,
    )

    assert value == "[Венти] Real Name"


def test_compose_chat_display_name_title_image_name_mode() -> None:
    value = SqlAlchemyActivityRepository._compose_chat_display_name(
        user_id=10,
        username="real_username",
        first_name="Real",
        last_name="Name",
        chat_display_name=None,
        title_prefix="Лорд",
        persona_label="Венти",
        persona_enabled=True,
        persona_display_mode=PERSONA_DISPLAY_MODE_TITLE_IMAGE_NAME,
    )

    assert value == "[Лорд] [Венти] Real Name"


def test_compose_chat_display_name_title_image_name_mode_without_title_falls_back_to_image_name() -> None:
    value = SqlAlchemyActivityRepository._compose_chat_display_name(
        user_id=10,
        username="real_username",
        first_name="Real",
        last_name="Name",
        chat_display_name=None,
        title_prefix=None,
        persona_label="Венти",
        persona_enabled=True,
        persona_display_mode=PERSONA_DISPLAY_MODE_TITLE_IMAGE_NAME,
    )

    assert value == "[Венти] Real Name"
