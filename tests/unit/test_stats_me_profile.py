from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.domain.entities import GraphRelationship, RelationshipState, UserSnapshot
from selara.presentation.handlers.stats import (
    _build_profile_social_lines,
    _extract_linked_user_label_from_message,
    _resolve_profile_mention,
)


def _message() -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=777),
        from_user=SimpleNamespace(id=10),
    )


@pytest.mark.asyncio
async def test_build_profile_social_lines_include_compact_clan_and_family_summary() -> None:
    now = datetime.now(timezone.utc)

    async def get_chat_display_name(*, chat_id: int, user_id: int) -> str | None:
        assert chat_id == 777
        return {20: "Tom & Jerry"}.get(user_id)

    activity_repo = SimpleNamespace(
        get_chat_title_prefix=AsyncMock(return_value="N<7>"),
        get_active_relationship=AsyncMock(
            return_value=RelationshipState(
                kind="marriage",
                id=1,
                user_low_id=10,
                user_high_id=20,
                chat_id=777,
                started_at=now,
                affection_points=42,
                last_affection_at=None,
                last_affection_by_user_id=None,
            )
        ),
        list_graph_relationships=AsyncMock(
            return_value=[
                GraphRelationship(1, 777, 10, 20, "spouse", None, now),
                GraphRelationship(2, 777, 30, 10, "parent", None, now),
                GraphRelationship(3, 777, 40, 10, "parent", None, now),
                GraphRelationship(4, 777, 10, 50, "parent", None, now),
                GraphRelationship(5, 777, 10, 60, "pet", None, now),
            ]
        ),
        get_chat_display_name=AsyncMock(side_effect=get_chat_display_name),
        get_user_snapshot=AsyncMock(return_value=None),
    )

    lines = await _build_profile_social_lines(_message(), activity_repo, user_id=10)

    assert lines == [
        "<b>Титул:</b> <code>[N&lt;7&gt;]</code>",
        '<b>Семья:</b> брак с <a href="tg://user?id=20">Tom &amp; Jerry</a> • родители 2 • дети 1 • питомцы 1',
    ]


@pytest.mark.asyncio
async def test_build_profile_social_lines_skip_empty_profile_bits() -> None:
    activity_repo = SimpleNamespace(
        get_chat_title_prefix=AsyncMock(return_value=None),
        get_active_relationship=AsyncMock(return_value=None),
        list_graph_relationships=AsyncMock(return_value=[]),
    )

    lines = await _build_profile_social_lines(_message(), activity_repo, user_id=10)

    assert lines == []


@pytest.mark.asyncio
async def test_resolve_profile_mention_prefers_telegram_name_over_username() -> None:
    activity_repo = SimpleNamespace(
        get_chat_display_name=AsyncMock(return_value=None),
        get_user_snapshot=AsyncMock(
            return_value=UserSnapshot(
                telegram_user_id=10,
                username="Hislorr",
                first_name="Крис",
                last_name=None,
                is_bot=False,
            )
        ),
    )

    mention = await _resolve_profile_mention(activity_repo, chat_id=777, user_id=10, cache={})

    assert mention == '<a href="tg://user?id=10">Крис</a>'


@pytest.mark.asyncio
async def test_resolve_profile_mention_uses_fallback_user_when_repo_has_no_snapshot() -> None:
    activity_repo = SimpleNamespace(
        get_chat_display_name=AsyncMock(return_value=None),
        get_user_snapshot=AsyncMock(return_value=None),
    )

    mention = await _resolve_profile_mention(
        activity_repo,
        chat_id=777,
        user_id=10,
        cache={},
        fallback_user=UserSnapshot(
            telegram_user_id=10,
            username="Hislorr",
            first_name="Крис",
            last_name=None,
            is_bot=False,
        ),
    )

    assert mention == '<a href="tg://user?id=10">Крис</a>'


def test_extract_linked_user_label_from_message_reads_tg_text_link() -> None:
    message = SimpleNamespace(
        text="Крис",
        caption=None,
        entities=(
            SimpleNamespace(
                type="text_link",
                offset=0,
                length=4,
                url="tg://user?id=10",
            ),
        ),
        caption_entities=(),
    )

    label = _extract_linked_user_label_from_message(message, user_id=10)

    assert label == "Крис"
