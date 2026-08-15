from selara.core.roles import (
    BOT_PERMISSIONS,
    PERMISSION_LABELS_RU,
    permission_label_ru,
    permissions_text_ru,
)


def test_permission_label_ru_covers_every_known_permission() -> None:
    for permission in BOT_PERMISSIONS:
        assert permission in PERMISSION_LABELS_RU
        assert permission_label_ru(permission) == PERMISSION_LABELS_RU[permission]


def test_permission_label_ru_falls_back_to_readable_default_for_unknown() -> None:
    assert permission_label_ru("some_new_permission") == "some new permission"


def test_permissions_text_ru_translates_in_input_order() -> None:
    # Preserves caller-provided order (matches the pre-existing behavior this
    # was moved from selara.web.presenters without changing).
    assert permissions_text_ru(["announce", "manage_games"]) == "объявления, управление играми"


def test_permissions_text_ru_empty_means_no_rights() -> None:
    assert permissions_text_ru([]) == "нет прав"
    assert permissions_text_ru(frozenset()) == "нет прав"
