from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.filters import CommandObject

from selara.domain.entities import GraphRelationship, RelationshipState, UserSnapshot
from selara.presentation.handlers import stats as stats_module
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
        '<b>Семья:</b> брак с <code>Tom &amp; Jerry</code> • родители: <code>user:30</code>, <code>user:40</code> • дети: <code>user:50</code> • питомцы: <code>user:60</code>',
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


@pytest.mark.asyncio
async def test_build_profile_meta_lines_include_active_rest() -> None:
    message = SimpleNamespace(chat=SimpleNamespace(id=777, type="group"))
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))
    activity_repo = SimpleNamespace(
        get_bot_role=AsyncMock(return_value=None),
        get_active_rest_state=AsyncMock(return_value=SimpleNamespace(expires_at=datetime(2026, 4, 15, 9, 30, tzinfo=timezone.utc))),
        get_moderation_state=AsyncMock(return_value=None),
    )

    lines = await stats_module._build_profile_meta_lines(
        message,
        activity_repo,
        bot,
        user_id=10,
        timezone_name="UTC",
    )

    assert any("Активный рест до" in line and "15.04.2026 09:30" in line for line in lines)


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


@pytest.mark.asyncio
async def test_me_command_with_username_routes_to_target_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, int] = {}

    async def fake_send_user_stats(message, activity_repo, bot, settings, chat_settings, *, user_id: int) -> None:
        called["user_id"] = user_id

    async def fake_send_me_stats(*args, **kwargs) -> None:
        raise AssertionError("send_me_stats should not be used for explicit target lookups")

    monkeypatch.setattr(stats_module, "send_user_stats", fake_send_user_stats)
    monkeypatch.setattr(stats_module, "send_me_stats", fake_send_me_stats)

    activity_repo = SimpleNamespace(
        find_chat_user_by_username=AsyncMock(
            return_value=UserSnapshot(
                telegram_user_id=20,
                username="alice",
                first_name="Alice",
                last_name=None,
                is_bot=False,
            )
        ),
        get_chat_display_name=AsyncMock(return_value=None),
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(
            id=10,
            username="actor",
            first_name="Actor",
            last_name=None,
            is_bot=False,
        ),
        reply_to_message=None,
        chat=SimpleNamespace(id=777, type="group", title="Test Chat"),
        answer=AsyncMock(),
    )

    await stats_module.me_command(
        message,
        CommandObject(prefix="/", command="me", mention=None, args="@alice"),
        activity_repo,
        bot=SimpleNamespace(),
        settings=SimpleNamespace(),
        chat_settings=SimpleNamespace(),
    )

    assert called == {"user_id": 20}
    message.answer.assert_not_called()


@pytest.mark.asyncio
async def test_send_user_stats_uses_iris_activity_view_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    message = SimpleNamespace(chat=SimpleNamespace(id=777))
    send_output = AsyncMock()

    monkeypatch.setattr(
        stats_module,
        "get_my_stats",
        AsyncMock(
            return_value=SimpleNamespace(
                chat_id=777,
                user_id=10,
                message_count=12,
                last_seen_at=datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc),
                first_seen_at=None,
                username="actor",
                first_name="Actor",
                last_name=None,
                chat_display_name=None,
            )
        ),
    )
    monkeypatch.setattr(
        stats_module,
        "get_rep_stats",
        AsyncMock(
            return_value=SimpleNamespace(
                activity_1d=917,
                activity_7d=917,
                activity_30d=917,
                activity_all=917,
                rank_all=1,
                rank_7d=2,
                karma_all=5,
                karma_7d=3,
            )
        ),
    )
    monkeypatch.setattr(stats_module, "_resolve_profile_mention", AsyncMock(return_value='<a href="tg://user?id=10">Actor</a>'))
    monkeypatch.setattr(stats_module, "_build_profile_social_lines", AsyncMock(return_value=[]))
    monkeypatch.setattr(stats_module, "_build_profile_meta_lines", AsyncMock(return_value=["<b>Meta:</b> ok"]))
    monkeypatch.setattr(stats_module, "_build_chart_async", AsyncMock(return_value=None))
    monkeypatch.setattr(stats_module, "_send_text_or_photo", send_output)

    await stats_module.send_user_stats(
        message,
        activity_repo=SimpleNamespace(get_user_activity_daily_series=AsyncMock(return_value=[])),
        bot=SimpleNamespace(),
        settings=SimpleNamespace(bot_timezone="UTC"),
        chat_settings=SimpleNamespace(
            top_limit_max=50,
            leaderboard_hybrid_karma_weight=0.7,
            leaderboard_hybrid_activity_weight=0.3,
            leaderboard_7d_days=7,
            iris_view=True,
        ),
        user_id=10,
    )

    html_text = send_output.await_args.kwargs["html_text"]
    assert "<b>Актив (д|н|м|весь):</b> 917 | 917 | 917 | 917" in html_text
    assert "<b>Вся активность:</b>" not in html_text
