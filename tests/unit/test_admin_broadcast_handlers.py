from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation.handlers.admin_broadcasts import (
    admin_broadcast_native_reaction,
    admin_broadcast_native_reaction_count,
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


@pytest.mark.asyncio
async def test_native_reaction_handler_keeps_standard_custom_and_paid_reactions() -> None:
    repo = SimpleNamespace(replace_admin_broadcast_native_reactions=AsyncMock(return_value=True))
    update = SimpleNamespace(
        chat=SimpleNamespace(id=-1007001, type="supergroup", title="Reactions"),
        user=SimpleNamespace(
            id=701,
            username="reactor",
            first_name="Reactor",
            last_name=None,
            is_bot=False,
        ),
        actor_chat=None,
        message_id=9002,
        date=datetime(2026, 8, 14, tzinfo=UTC),
        new_reaction=[
            SimpleNamespace(type="emoji", emoji="❤"),
            SimpleNamespace(type="custom_emoji", custom_emoji_id="5368324170671202286"),
            SimpleNamespace(type="paid"),
        ],
    )

    await admin_broadcast_native_reaction(update, repo)

    call = repo.replace_admin_broadcast_native_reactions.await_args.kwargs
    assert call["user"].telegram_user_id == 701
    assert call["actor_chat_id"] is None
    reactions = call["reactions"]
    assert {
        (item.reaction_type, item.value, item.display)
        for item in reactions
    } == {
        ("emoji", "❤", "❤"),
        ("custom_emoji", "5368324170671202286", "✨"),
        ("paid", "paid", "⭐"),
    }


@pytest.mark.asyncio
async def test_native_reaction_handler_accepts_chat_actor_without_user() -> None:
    repo = SimpleNamespace(replace_admin_broadcast_native_reactions=AsyncMock(return_value=True))
    update = SimpleNamespace(
        chat=SimpleNamespace(id=-1007001, type="supergroup", title="Reactions"),
        user=None,
        actor_chat=SimpleNamespace(id=-1007999),
        message_id=9002,
        date=datetime(2026, 8, 14, tzinfo=UTC),
        new_reaction=[SimpleNamespace(type="emoji", emoji="🔥")],
    )

    await admin_broadcast_native_reaction(update, repo)

    call = repo.replace_admin_broadcast_native_reactions.await_args.kwargs
    assert call["user"] is None
    assert call["actor_chat_id"] == -1007999
    assert {(item.reaction_type, item.value) for item in call["reactions"]} == {
        ("emoji", "🔥")
    }


@pytest.mark.asyncio
async def test_native_reaction_count_handler_keeps_all_reaction_types() -> None:
    repo = SimpleNamespace(replace_admin_broadcast_reaction_counts=AsyncMock(return_value=True))
    update = SimpleNamespace(
        chat=SimpleNamespace(id=-1007001),
        message_id=9002,
        date=datetime(2026, 8, 14, tzinfo=UTC),
        reactions=[
            SimpleNamespace(type=SimpleNamespace(type="emoji", emoji="❤"), total_count=3),
            SimpleNamespace(
                type=SimpleNamespace(type="custom_emoji", custom_emoji_id="5368324170671202286"),
                total_count=2,
            ),
            SimpleNamespace(type=SimpleNamespace(type="paid"), total_count=1),
        ],
    )

    await admin_broadcast_native_reaction_count(update, repo)

    totals = repo.replace_admin_broadcast_reaction_counts.await_args.kwargs["reactions"]
    assert {
        (item.reaction.reaction_type, item.reaction.value, item.reaction.display, item.count)
        for item in totals
    } == {
        ("emoji", "❤", "❤", 3),
        ("custom_emoji", "5368324170671202286", "✨", 2),
        ("paid", "paid", "⭐", 1),
    }
