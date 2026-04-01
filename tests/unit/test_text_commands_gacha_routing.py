from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.presentation.handlers import text_commands


class _DummyMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.chat = SimpleNamespace(type="group", id=-100123, title="Test chat")
        self.from_user = SimpleNamespace(id=1, username="actor", first_name="Actor", last_name=None, is_bot=False)
        self.reply_to_message = None
        self.answers: list[tuple[str, dict[str, object]]] = []

    async def answer(self, text: str, **kwargs) -> None:
        self.answers.append((text, kwargs))


_CHAT_SETTINGS = SimpleNamespace(
    text_commands_enabled=True,
    text_commands_locale="ru",
    custom_rp_enabled=False,
    smart_triggers_enabled=False,
    top_limit_default=10,
    top_limit_max=50,
    gacha_enabled=True,
    gacha_restore_at=None,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message_text", "handler_attr", "banner"),
    [
        ("гача генш", "_send_gacha_pull", "genshin"),
        ("гача геншин", "_send_gacha_pull", "genshin"),
        ("гача хср", "_send_gacha_pull", "hsr"),
        ("моя гача генш", "_send_gacha_profile", "genshin"),
        ("моя гача геншин", "_send_gacha_profile", "genshin"),
        ("моя гача хср", "_send_gacha_profile", "hsr"),
        ("гача инфо", "_send_gacha_info", None),
        ("гача скип генш", "_send_gacha_skip", "genshin"),
        ("гача скип хср @alice", "_send_gacha_skip", "hsr"),
    ],
)
async def test_text_commands_handler_routes_gacha_commands(
    monkeypatch: pytest.MonkeyPatch,
    message_text: str,
    handler_attr: str,
    banner: str,
) -> None:
    message = _DummyMessage(text=message_text)
    activity_repo = SimpleNamespace(
        get_chat_alias_mode=AsyncMock(return_value="both"),
        list_chat_aliases=AsyncMock(return_value=[]),
    )
    target_handler = AsyncMock()
    settings = SimpleNamespace(supported_chat_types={"private", "group", "supergroup"})

    monkeypatch.setattr(text_commands, "_enforce_command_access", AsyncMock(return_value=True))
    monkeypatch.setattr(text_commands, "_handle_command_rank_phrase", AsyncMock(return_value=False))
    monkeypatch.setattr(text_commands, handler_attr, target_handler)

    await text_commands.text_commands_handler(
        message,
        activity_repo=activity_repo,
        economy_repo=object(),
        bot=object(),
        settings=settings,
        chat_settings=_CHAT_SETTINGS,
        session_factory=object(),
    )

    target_handler.assert_awaited_once()
    assert target_handler.await_args.args[0] is message
    if handler_attr == "_send_gacha_skip":
        assert target_handler.await_args.args[1] is activity_repo
        assert target_handler.await_args.args[2] is settings
        if "@alice" in message_text:
            assert target_handler.await_args.kwargs["target_username"] == "@alice"
        else:
            assert target_handler.await_args.kwargs["target_username"] is None
    elif handler_attr == "_send_gacha_info":
        assert target_handler.await_args.args[1] is settings
        assert target_handler.await_args.args[2] is not None
        assert target_handler.await_args.args[3] is _CHAT_SETTINGS
    else:
        assert target_handler.await_args.args[1] is settings
    if banner is not None:
        assert target_handler.await_args.kwargs["banner"] == banner
