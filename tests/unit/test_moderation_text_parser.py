from selara.presentation.handlers.moderation import _parse_reply_text_action


def test_parse_reply_text_action_pred_with_reason() -> None:
    parsed = _parse_reply_text_action("пред флуд в чате")
    assert parsed == ("pred", "флуд в чате")


def test_parse_reply_text_action_warn_with_extra_spaces() -> None:
    parsed = _parse_reply_text_action("  снять   варн   ошибка  ")
    assert parsed == ("unwarn", "ошибка")


def test_parse_reply_text_action_returns_none_for_unknown_text() -> None:
    assert _parse_reply_text_action("привет") is None

