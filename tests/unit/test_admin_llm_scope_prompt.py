"""Regression #94: the deployed admin prompt must disclose unavailable live data."""

import pytest

from selara.infrastructure.llm.prompts import ADMIN_SYSTEM_PROMPT


def _prompt() -> str:
    return ADMIN_SYSTEM_PROMPT.format(
        chat_title="Чат", chat_id=-100, admin_tag="@admin", admin_user_id=1,
        doc_files_list="capabilities.md",
    )


@pytest.mark.parametrize("domain,command", [
    ("экономики", "/eco"), ("гачи", "моя гача генш"), ("семьи", "/family"), ("игр", "/game"),
])
def test_admin_prompt_discloses_unavailable_domains_and_redirects(domain, command):
    scope = _prompt().split("Границы доступа:", 1)[1].split("\n", 1)[0]
    assert domain in scope
    assert "не дают прямого доступа к состоянию" in scope
    assert "не можешь проверить или изменить эти данные через инструменты" in scope
    assert "прямо сообщи об этом ограничении" in scope
    assert command in scope


def test_admin_prompt_forbids_claiming_unchecked_state_from_history_or_docs():
    scope = _prompt().split("Границы доступа:", 1)[1].split("\n", 1)[0]
    assert "не утверждай, что проверил данные" in scope
    assert "не придумывай баланс, коллекцию, семейные связи или состояние игры" in scope
    assert "История диалога, словарь и документация не подтверждают текущее состояние" in scope
    assert "только с указанием, что это его слова" in scope
    assert "сначала используй read_bot_doc" in scope
