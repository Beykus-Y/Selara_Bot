from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.domain.entities import UserSnapshot
from selara.domain.economy_entities import ChatAuction
from selara.presentation.handlers.economy import _extract_owner_from_parts, _resolve_auction_leader_label, _with_owner_suffix


def test_owner_suffix_roundtrip() -> None:
    encoded = _with_owner_suffix("eco:dash:l", 42)
    parts, owner_user_id = _extract_owner_from_parts(encoded.split(":"))
    assert ":".join(parts) == "eco:dash:l"
    assert owner_user_id == 42


def test_owner_suffix_compat_without_owner() -> None:
    parts, owner_user_id = _extract_owner_from_parts("eco:dash:l".split(":"))
    assert parts == ["eco", "dash", "l"]
    assert owner_user_id is None


@pytest.mark.asyncio
async def test_resolve_auction_leader_label_prefers_first_name_over_username() -> None:
    activity_repo = SimpleNamespace(
        get_chat_display_name=AsyncMock(return_value=None),
        get_user_snapshot=AsyncMock(
            return_value=UserSnapshot(
                telegram_user_id=77,
                username="viewer77",
                first_name="Вика",
                last_name=None,
                is_bot=False,
            )
        ),
    )
    auction = ChatAuction(
        id=1,
        chat_id=-1001,
        scope_id="chat:-1001",
        scope_type="chat",
        seller_user_id=10,
        item_code="item:pizza",
        quantity=1,
        start_price=5,
        current_bid=5,
        highest_bid_user_id=77,
        min_increment=1,
        status="active",
        message_id=None,
        ends_at=datetime(2026, 3, 13, 12, 0, tzinfo=timezone.utc),
        closed_at=None,
        created_at=datetime(2026, 3, 13, 11, 0, tzinfo=timezone.utc),
        updated_at=None,
    )

    label = await _resolve_auction_leader_label(activity_repo, auction)

    assert label == "Вика"
