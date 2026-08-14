from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation.handlers.admin_broadcasts import (
    admin_broadcast_reaction_callback,
    decode_broadcast_reaction_callback,
)
from selara.presentation.routers import build_router


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("abr:42:r1", (42, "r1")),
        ("abr:999999:r6", (999999, "r6")),
        ("abr:0:r1", None),
        ("abr:42:r7", None),
        ("abr:abc:r1", None),
        ("other:42:r1", None),
        (None, None),
    ],
)
def test_callback_payload_decoder_is_strict(value: str | None, expected: tuple[int, str] | None) -> None:
    assert decode_broadcast_reaction_callback(value) == expected


@pytest.mark.asyncio
async def test_inline_callback_records_user_and_answers_quickly() -> None:
    repo = SimpleNamespace(toggle_admin_broadcast_inline_reaction=AsyncMock(return_value="selected"))
    query = SimpleNamespace(
        data="abr:42:r2",
        from_user=SimpleNamespace(
            id=700,
            username="reader",
            first_name="Reader",
            last_name=None,
            is_bot=False,
        ),
        message=SimpleNamespace(
            date=datetime(2026, 8, 14, tzinfo=UTC),
            message_id=9001,
            chat=SimpleNamespace(id=-1007001),
        ),
        answer=AsyncMock(),
    )

    await admin_broadcast_reaction_callback(query, repo)

    call = repo.toggle_admin_broadcast_inline_reaction.await_args.kwargs
    assert call["delivery_id"] == 42
    assert call["option_key"] == "r2"
    assert call["chat_id"] == -1007001
    assert call["telegram_message_id"] == 9001
    assert call["user"].telegram_user_id == 700
    query.answer.assert_awaited_once_with("Реакция сохранена.")


def test_router_subscribes_to_native_reaction_updates() -> None:
    router = build_router(None, activity_batcher=SimpleNamespace())  # type: ignore[arg-type]

    used = set(router.resolve_used_update_types())

    assert "message_reaction" in used
    assert "message_reaction_count" in used
