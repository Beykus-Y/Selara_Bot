from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.domain.entities import ChatInterestingFactState
from selara.presentation.handlers import settings as settings_handler
from selara.presentation.interesting_facts import (
    InterestingFactCatalog,
    evaluate_interesting_fact_eligibility,
    parse_interesting_facts,
    select_next_interesting_fact,
)


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
        }
    )


def _chat_settings():
    return replace(default_chat_settings(_settings()), interesting_facts_enabled=True)


def test_interesting_fact_catalog_reloads_and_dedupes(tmp_path) -> None:
    path = tmp_path / "facts.json"
    path.write_text(json.dumps([" Alpha ", "alpha", "", "Beta"], ensure_ascii=False), encoding="utf-8")
    catalog = InterestingFactCatalog(path)

    first = catalog.get_facts()
    assert [item.text for item in first] == ["Alpha", "Beta"]

    path.write_text(json.dumps(["Gamma"], ensure_ascii=False), encoding="utf-8")
    second = catalog.get_facts()
    assert [item.text for item in second] == ["Gamma"]


def test_select_next_interesting_fact_resets_cycle_without_immediate_repeat() -> None:
    facts = parse_interesting_facts(["Первый", "Второй", "Третий"])
    previous = facts[-1]
    state = ChatInterestingFactState(
        chat_id=-100,
        last_sent_at=datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc),
        last_fact_id=previous.fact_id,
        used_fact_ids=tuple(item.fact_id for item in facts),
    )

    fact, used_ids = select_next_interesting_fact(facts=facts, state=state)

    assert fact is not None
    assert fact.fact_id != previous.fact_id
    assert used_ids == (fact.fact_id,)


@pytest.mark.parametrize(
    ("state", "messages_since_reference", "messages_in_interval", "messages_in_sleep_cap", "has_active_game", "eligible", "reason"),
    [
        (
            ChatInterestingFactState(
                chat_id=1,
                last_sent_at=datetime(2026, 3, 19, 10, 30, tzinfo=timezone.utc),
                last_fact_id="fact_1",
                used_fact_ids=("fact_1",),
            ),
            200,
            200,
            200,
            False,
            False,
            "cooldown",
        ),
        (
            ChatInterestingFactState(
                chat_id=1,
                last_sent_at=datetime(2026, 3, 19, 8, 0, tzinfo=timezone.utc),
                last_fact_id="fact_1",
                used_fact_ids=("fact_1",),
            ),
            150,
            40,
            150,
            False,
            True,
            "target_messages",
        ),
        (
            ChatInterestingFactState(
                chat_id=1,
                last_sent_at=datetime(2026, 3, 19, 8, 0, tzinfo=timezone.utc),
                last_fact_id="fact_1",
                used_fact_ids=("fact_1",),
            ),
            20,
            0,
            20,
            False,
            True,
            "quiet_interval",
        ),
        (
            ChatInterestingFactState(
                chat_id=1,
                last_sent_at=datetime(2026, 3, 18, 8, 0, tzinfo=timezone.utc),
                last_fact_id="fact_1",
                used_fact_ids=("fact_1",),
            ),
            0,
            0,
            0,
            False,
            False,
            "sleep_cap",
        ),
        (
            ChatInterestingFactState(
                chat_id=1,
                last_sent_at=datetime(2026, 3, 19, 8, 0, tzinfo=timezone.utc),
                last_fact_id="fact_1",
                used_fact_ids=("fact_1",),
            ),
            200,
            200,
            200,
            True,
            False,
            "active_game",
        ),
    ],
)
def test_evaluate_interesting_fact_eligibility(
    state: ChatInterestingFactState,
    messages_since_reference: int,
    messages_in_interval: int,
    messages_in_sleep_cap: int,
    has_active_game: bool,
    eligible: bool,
    reason: str,
) -> None:
    now = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)

    result = evaluate_interesting_fact_eligibility(
        now=now,
        settings=_chat_settings(),
        state=state,
        has_facts=True,
        has_active_game=has_active_game,
        messages_since_reference=messages_since_reference,
        messages_in_interval=messages_in_interval,
        messages_in_sleep_cap=messages_in_sleep_cap,
    )

    assert result.eligible is eligible
    assert result.reason == reason


@pytest.mark.asyncio
async def test_facttest_command_does_not_mutate_state(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = parse_interesting_facts(["Первый факт", "Второй факт"])
    state = ChatInterestingFactState(
        chat_id=-100123,
        last_sent_at=datetime(2026, 3, 19, 8, 0, tzinfo=timezone.utc),
        last_fact_id=facts[0].fact_id,
        used_fact_ids=(facts[0].fact_id,),
    )
    activity_repo = SimpleNamespace(
        get_chat_interesting_fact_state=AsyncMock(return_value=state),
        upsert_chat_interesting_fact_state=AsyncMock(),
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100123, type="group", title="Facts"),
        from_user=SimpleNamespace(id=1, username="owner", first_name="Owner", last_name=None, is_bot=False),
        answer=AsyncMock(),
    )

    monkeypatch.setattr(settings_handler, "has_permission", AsyncMock(return_value=(True, "owner", False)))
    monkeypatch.setattr(settings_handler, "INTERESTING_FACT_CATALOG", SimpleNamespace(get_facts=lambda: facts))

    await settings_handler.facttest_command(message, activity_repo)

    activity_repo.get_chat_interesting_fact_state.assert_awaited_once_with(chat_id=-100123)
    activity_repo.upsert_chat_interesting_fact_state.assert_not_called()
    message.answer.assert_awaited_once()
    assert "Интересный факт:" in message.answer.await_args.args[0]
