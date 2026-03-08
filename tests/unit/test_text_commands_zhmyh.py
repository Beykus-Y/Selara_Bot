from selara.presentation.handlers.text_commands import _extract_zhmyh_level


def test_extract_zhmyh_level_default() -> None:
    matched, level, error = _extract_zhmyh_level("жмых")
    assert matched is True
    assert error is None
    assert level == 3


def test_extract_zhmyh_level_explicit() -> None:
    matched, level, error = _extract_zhmyh_level(" ЖМЫХ 6 ")
    assert matched is True
    assert error is None
    assert level == 6


def test_extract_zhmyh_level_rejects_out_of_range() -> None:
    matched, level, error = _extract_zhmyh_level("жмых 9")
    assert matched is True
    assert level is None
    assert error is not None


def test_extract_zhmyh_level_rejects_bad_format() -> None:
    matched, level, error = _extract_zhmyh_level("жмых сильно")
    assert matched is True
    assert level is None
    assert error is not None


def test_extract_zhmyh_level_ignores_other_text() -> None:
    matched, level, error = _extract_zhmyh_level("привет")
    assert matched is False
    assert level is None
    assert error is None
