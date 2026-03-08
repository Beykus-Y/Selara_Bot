import pytest

from selara.core.trigger_templates import (
    build_trigger_template_variable_groups,
    render_template_variables,
    validate_template_variables,
)


def test_validate_template_variables_accepts_known_variables_and_literals() -> None:
    validate_template_variables(
        "Привет, {user}. Цель: {target}. Аргументы: {args}. Скобки: {{ok}}. Время: {time}."
    )


def test_validate_template_variables_rejects_unknown_variable() -> None:
    with pytest.raises(ValueError, match="Неизвестные переменные шаблона"):
        validate_template_variables("Неизвестно: {unknown_value}")


def test_render_template_variables_inserts_values() -> None:
    rendered = render_template_variables(
        "{user} -> {args} -> {weekday}",
        {
            "user": "<a href=\"tg://user?id=1\">Beykus</a>",
            "args": "доп. текст",
            "weekday": "воскресенье",
        },
    )

    assert rendered == '<a href="tg://user?id=1">Beykus</a> -> доп. текст -> воскресенье'


def test_build_trigger_template_variable_groups_contains_core_tokens() -> None:
    groups = build_trigger_template_variable_groups()
    tokens = {
        item["token"]
        for group in groups
        for item in group["items"]
    }

    assert "{user}" in tokens
    assert "{reply_user}" in tokens
    assert "{chat}" in tokens
    assert "{args}" in tokens
