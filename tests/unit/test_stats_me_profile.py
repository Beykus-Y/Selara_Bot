from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.domain.entities import GraphRelationship, RelationshipState
from selara.presentation.handlers.stats import _build_profile_social_lines


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
        "<b>Семья:</b> брак с Tom &amp; Jerry • родители 2 • дети 1 • питомцы 1",
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
