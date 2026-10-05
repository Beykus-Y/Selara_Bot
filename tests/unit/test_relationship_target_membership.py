from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.filters import CommandObject

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.presentation.handlers import chat_assistant, relationships


def _message(*, reply_user=None) -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100, type="group", title="Chat"),
        from_user=SimpleNamespace(id=10, username="actor", first_name="Actor", last_name=None, is_bot=False),
        reply_to_message=SimpleNamespace(from_user=reply_user) if reply_user is not None else None,
        answer=AsyncMock(),
    )


def _repo(*, is_member: bool) -> SimpleNamespace:
    return SimpleNamespace(
        get_user_snapshot=AsyncMock(return_value=None),
        get_chat_display_name=AsyncMock(return_value=None),
        find_chat_user_by_username=AsyncMock(return_value=None),
        is_active_chat_member=AsyncMock(return_value=is_member),
        create_marriage_proposal=AsyncMock(return_value=(SimpleNamespace(), None)),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["pair", "marriage"])
async def test_proposal_rejects_numeric_id_of_non_member(kind: str) -> None:
    message = _message()
    repo = _repo(is_member=False)

    await relationships._send_relationship_proposal(message, activity_repo=repo, kind=kind, args="424242")

    repo.create_marriage_proposal.assert_not_awaited()
    assert "не является активным участником" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_proposal_for_active_member_numeric_id_is_created(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message()
    repo = _repo(is_member=True)
    monkeypatch.setattr(relationships, "_mention", AsyncMock(return_value="@x"))

    await relationships._send_relationship_proposal(message, activity_repo=repo, kind="pair", args="424242")

    repo.create_marriage_proposal.assert_awaited_once()


@pytest.mark.asyncio
async def test_proposal_for_reply_target_does_not_require_membership_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message(reply_user=SimpleNamespace(id=20, username="t", first_name="T", last_name=None, is_bot=False))
    repo = _repo(is_member=False)
    monkeypatch.setattr(relationships, "_mention", AsyncMock(return_value="@x"))

    await relationships._send_relationship_proposal(message, activity_repo=repo, kind="pair", args=None)

    repo.create_marriage_proposal.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("relation_type", "extra"),
    [("parent", {"adopt_verb": "усыновить", "child_role": "сын"}), ("pet", {})],
)
async def test_family_request_rejects_numeric_id_of_non_member(relation_type: str, extra: dict) -> None:
    chat_assistant._FAMILY_REQUESTS.clear()
    message = _message()
    repo = _repo(is_member=False)

    await chat_assistant._send_family_request(
        message, activity_repo=repo, relation_type=relation_type, raw_args="424242", **extra
    )

    assert chat_assistant._FAMILY_REQUESTS == {}
    assert "не является активным участником" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_family_request_rejects_reply_to_bot() -> None:
    chat_assistant._FAMILY_REQUESTS.clear()
    message = _message(reply_user=SimpleNamespace(id=777, username="somebot", first_name="Bot", last_name=None, is_bot=True))
    repo = _repo(is_member=True)

    await chat_assistant._send_family_request(
        message, activity_repo=repo, relation_type="parent", raw_args=None, adopt_verb="усыновить", child_role="сын"
    )

    assert chat_assistant._FAMILY_REQUESTS == {}
