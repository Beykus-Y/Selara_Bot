from selara.presentation.commands.normalizer import normalize_text_command


def test_normalizer_handles_case_spaces_and_punctuation() -> None:
    assert normalize_text_command("  Кто   Я?!  ") == "кто я"


def test_normalizer_keeps_number_argument() -> None:
    assert normalize_text_command(" Актив   15. ") == "актив 15"
