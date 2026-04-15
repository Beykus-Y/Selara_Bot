from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from selara.core.chat_settings import ChatSettings
from selara.domain.entities import ChatPersonaAssignment, UserSnapshot
from selara.presentation.handlers.text_commands import _send_social_action


_BASE_CHAT_SETTINGS = ChatSettings(
    top_limit_default=10,
    top_limit_max=50,
    vote_daily_limit=20,
    leaderboard_hybrid_karma_weight=0.7,
    leaderboard_hybrid_activity_weight=0.3,
    leaderboard_7d_days=7,
    leaderboard_week_start_weekday=0,
    leaderboard_week_start_hour=0,
    mafia_night_seconds=90,
    mafia_day_seconds=120,
    mafia_vote_seconds=60,
    mafia_reveal_eliminated_role=True,
    text_commands_enabled=True,
    text_commands_locale="ru",
    actions_18_enabled=True,
    smart_triggers_enabled=True,
    welcome_enabled=True,
    welcome_text="Привет, {user}! Добро пожаловать в {chat}.",
    welcome_button_text="",
    welcome_button_url="",
    goodbye_enabled=False,
    goodbye_text="Пока, {user}.",
    welcome_cleanup_service_messages=True,
    entry_captcha_enabled=False,
    entry_captcha_timeout_seconds=180,
    entry_captcha_kick_on_fail=True,
    custom_rp_enabled=True,
    family_tree_enabled=True,
    titles_enabled=True,
    title_price=50000,
    craft_enabled=True,
    auctions_enabled=True,
    auction_duration_minutes=10,
    auction_min_increment=100,
    economy_enabled=True,
    economy_mode="global",
    economy_tap_cooldown_seconds=45,
    economy_daily_base_reward=120,
    economy_daily_streak_cap=7,
    economy_lottery_ticket_price=150,
    economy_lottery_paid_daily_limit=10,
    economy_transfer_daily_limit=5000,
    economy_transfer_tax_percent=5,
    economy_market_fee_percent=2,
    economy_negative_event_chance_percent=22,
    economy_negative_event_loss_percent=30,
)


def _chat_settings(*, actions_18_enabled: bool) -> ChatSettings:
    return replace(_BASE_CHAT_SETTINGS, actions_18_enabled=actions_18_enabled)


class _FakeActivityRepo:
    async def get_chat_display_name(self, *, chat_id: int, user_id: int) -> str | None:
        if user_id == 4:
            return "[Коломбина] Columbina"
        return None

    async def get_announcement_recipients(self, *, chat_id: int) -> list[UserSnapshot]:
        return [
            UserSnapshot(
                telegram_user_id=2,
                username="target",
                first_name="Target",
                last_name=None,
                is_bot=False,
                chat_display_name=None,
            ),
            UserSnapshot(
                telegram_user_id=3,
                username="friend",
                first_name="Friend",
                last_name=None,
                is_bot=False,
                chat_display_name=None,
            ),
        ]

    async def find_chat_user_by_username(self, *, chat_id: int, username: str):
        return None

    async def find_chat_persona_owner(self, *, chat_id: int, persona_label: str):
        return None

    async def list_chat_persona_assignments(self, *, chat_id: int) -> list[ChatPersonaAssignment]:
        return [
            ChatPersonaAssignment(
                chat_id=chat_id,
                user=UserSnapshot(
                    telegram_user_id=4,
                    username="columbina_main",
                    first_name="Columbina",
                    last_name=None,
                    is_bot=False,
                    chat_display_name="Коломбина",
                ),
                persona_label="Коломбина",
                persona_label_norm="коломбина",
                granted_by_user_id=1,
            )
        ]


class _DummyMessage:
    def __init__(self, *, text: str | None = None) -> None:
        self.chat = SimpleNamespace(type="group", id=-100123)
        self.from_user = SimpleNamespace(id=1, username="actor", first_name="Actor", last_name=None, is_bot=False)
        self.reply_to_message = SimpleNamespace(
            from_user=SimpleNamespace(id=2, username="target", first_name="Target", last_name=None, is_bot=False)
        )
        self.text = text
        self.caption = None
        self.answers: list[tuple[str, dict[str, object]]] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append((text, kwargs))


@pytest.mark.asyncio
@pytest.mark.parametrize("action_key", ["fuck", "seduce", "makeout", "night", "bend", "suck"])
async def test_send_social_action_blocks_explicit_actions_when_18_disabled(action_key: str) -> None:
    message = _DummyMessage()

    await _send_social_action(
        message,
        _FakeActivityRepo(),
        _chat_settings(actions_18_enabled=False),
        action_key=action_key,
    )

    assert len(message.answers) == 1
    assert message.answers[0][0].startswith("18+ действия отключены")
    assert message.answers[0][1]["parse_mode"] == "HTML"


@pytest.mark.asyncio
@pytest.mark.parametrize("action_key", ["fuck", "seduce", "makeout", "night", "bend", "suck"])
async def test_send_social_action_allows_explicit_actions_when_18_enabled(action_key: str) -> None:
    message = _DummyMessage()

    await _send_social_action(
        message,
        _FakeActivityRepo(),
        _chat_settings(actions_18_enabled=True),
        action_key=action_key,
    )

    assert len(message.answers) == 1
    assert "18+ действия отключены" not in message.answers[0][0]
    assert message.answers[0][1]["parse_mode"] == "HTML"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_key", "text", "action_fragment", "replica"),
    [
        ("hug", "обнять\nБедолага ты наша", "обнял", "Бедолага ты наша"),
        ("kiss", "поцеловать\nты сегодня лапочка", "поцеловал", "ты сегодня лапочка"),
        ("hit", "ударить\nэто за мем", "ударил", "это за мем"),
    ],
)
async def test_send_social_action_includes_replica_tail(
    monkeypatch: pytest.MonkeyPatch,
    action_key: str,
    text: str,
    action_fragment: str,
    replica: str,
) -> None:
    message = _DummyMessage(text=text)
    monkeypatch.setattr("selara.presentation.handlers.text_commands.random.choice", lambda seq: seq[0])

    await _send_social_action(
        message,
        _FakeActivityRepo(),
        _chat_settings(actions_18_enabled=True),
        action_key=action_key,
    )

    assert len(message.answers) == 1
    assert action_fragment in message.answers[0][0]
    assert f"С репликой: «{replica}»" in message.answers[0][0]
    assert message.answers[0][1]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_send_social_action_supports_all_targets_without_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyMessage(text="обнять всех\nвы лучшие")
    message.reply_to_message = None
    monkeypatch.setattr("selara.presentation.handlers.text_commands.random.choice", lambda seq: seq[0])

    await _send_social_action(
        message,
        _FakeActivityRepo(),
        _chat_settings(actions_18_enabled=True),
        action_key="hug",
    )

    assert len(message.answers) == 1
    assert "обнял" in message.answers[0][0]
    assert "tg://user?id=2" in message.answers[0][0]
    assert "tg://user?id=3" in message.answers[0][0]
    assert "С репликой: «вы лучшие»" in message.answers[0][0]
    assert message.answers[0][1]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_send_social_action_supports_single_line_persona_target(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyMessage(text="обнять Коломбина")
    message.reply_to_message = None
    monkeypatch.setattr("selara.presentation.handlers.text_commands.random.choice", lambda seq: seq[0])

    await _send_social_action(
        message,
        _FakeActivityRepo(),
        _chat_settings(actions_18_enabled=True),
        action_key="hug",
    )

    assert len(message.answers) == 1
    assert "обнял" in message.answers[0][0]
    assert "tg://user?id=4" in message.answers[0][0]
    assert "[Коломбина] Columbina" in message.answers[0][0]


@pytest.mark.asyncio
async def test_send_social_action_supports_at_persona_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _DummyMessage(text="обнять @коломбина")
    message.reply_to_message = None
    monkeypatch.setattr("selara.presentation.handlers.text_commands.random.choice", lambda seq: seq[0])

    await _send_social_action(
        message,
        _FakeActivityRepo(),
        _chat_settings(actions_18_enabled=True),
        action_key="hug",
    )

    assert len(message.answers) == 1
    assert "обнял" in message.answers[0][0]
    assert "tg://user?id=4" in message.answers[0][0]
