from selara.presentation.handlers.text_commands import _extract_social_action


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
