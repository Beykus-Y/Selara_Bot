from types import SimpleNamespace

from selara.presentation.middlewares.activity_tracker import _is_profile_lookup_message


def _msg(text: str):
    return SimpleNamespace(text=text)


def test_profile_lookup_detects_me_command() -> None:
    assert _is_profile_lookup_message(_msg("/me"))
    assert _is_profile_lookup_message(_msg("/me@selara_bot"))


def test_profile_lookup_detects_who_am_i_text() -> None:
    assert _is_profile_lookup_message(_msg("кто я"))
    assert _is_profile_lookup_message(_msg("Кто   Я?!"))


def test_profile_lookup_ignores_regular_messages() -> None:
    assert not _is_profile_lookup_message(_msg("/help"))
    assert not _is_profile_lookup_message(_msg("кто я такой"))
