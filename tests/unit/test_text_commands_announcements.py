from selara.presentation.handlers.text_commands import _extract_announcement_body


def test_extract_announcement_body_with_quotes_and_newlines() -> None:
    body, error = _extract_announcement_body('объява "Первая строка\nВторая строка"')
    assert error is None
    assert body == "Первая строка\nВторая строка"


def test_extract_announcement_body_without_quotes() -> None:
    body, error = _extract_announcement_body("объява срочно всем проверить чат")
    assert error is None
    assert body == "срочно всем проверить чат"


def test_extract_announcement_body_rejects_missing_body() -> None:
    body, error = _extract_announcement_body("объява")
    assert body is None
    assert error is not None


def test_extract_announcement_body_rejects_unclosed_quote() -> None:
    body, error = _extract_announcement_body('объява "тест')
    assert body is None
    assert error is not None

