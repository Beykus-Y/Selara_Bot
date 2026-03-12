import pytest

from selara.presentation.handlers.text_commands import (
    _build_social_action_replica_line,
    _extract_social_action,
    _extract_social_action_request,
)


def test_extract_social_action_basic_commands() -> None:
    assert _extract_social_action("шлепнуть") == "slap"
    assert _extract_social_action("обнять") == "hug"
    assert _extract_social_action("поцеловать!") == "kiss"


def test_extract_social_action_supports_imperative_aliases() -> None:
    assert _extract_social_action("ударь") == "hit"
    assert _extract_social_action("трахни") == "fuck"
    assert _extract_social_action("соблазни") == "seduce"
    assert _extract_social_action("засоси") == "makeout"
    assert _extract_social_action("убей") == "kill"


def test_extract_social_action_supports_new_single_word_actions() -> None:
    assert _extract_social_action("погладь") == "pat"
    assert _extract_social_action("пощекочи") == "tickle"
    assert _extract_social_action("ткни") == "poke"
    assert _extract_social_action("подмигни") == "wink"
    assert _extract_social_action("потанцуй") == "dance"
    assert _extract_social_action("поклонись") == "bow"
    assert _extract_social_action("подбодри") == "cheer"
    assert _extract_social_action("угости") == "treat"
    assert _extract_social_action("похвали") == "praise"


def test_extract_social_action_supports_new_multiword_actions() -> None:
    assert _extract_social_action("пожми руку") == "handshake"
    assert _extract_social_action("дай пять!") == "highfive"
    assert _extract_social_action("дай кулак") == "fistbump"
    assert _extract_social_action("проведи ночь с") == "night"


def test_extract_social_action_ignores_unknown_or_slash() -> None:
    assert _extract_social_action("привет") is None
    assert _extract_social_action("/шлепнуть") is None


def test_extract_social_action_request_supports_multiline_replica() -> None:
    action_key, replica = _extract_social_action_request("Обнять\nБедолага ты наша")

    assert action_key == "hug"
    assert replica == "Бедолага ты наша"


def test_extract_social_action_request_supports_inline_replica_with_punctuation() -> None:
    action_key, replica = _extract_social_action_request("обнять! бедолага ты наша")

    assert action_key == "hug"
    assert replica == "бедолага ты наша"


def test_build_social_action_replica_line_uses_alternative_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "selara.presentation.handlers.text_commands.random.choice",
        lambda seq: seq[1],
    )

    assert _build_social_action_replica_line("привет") == "🗣 И добавил(а): «привет»"
