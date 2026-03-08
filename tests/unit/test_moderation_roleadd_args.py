from selara.presentation.handlers.moderation import _parse_roleadd_args


def test_parse_roleadd_args_keeps_role_first_shape() -> None:
    assert _parse_roleadd_args('"Сучара" @Jullusionist') == [
        ("Сучара", "@Jullusionist"),
        ("@Jullusionist", "Сучара"),
    ]


def test_parse_roleadd_args_supports_user_first_shape() -> None:
    assert _parse_roleadd_args("@Jullusionist 12") == [
        ("@Jullusionist", "12"),
        ("12", "@Jullusionist"),
    ]


def test_parse_roleadd_args_handles_reply_only_role_token() -> None:
    assert _parse_roleadd_args("12") == [("12", None)]
