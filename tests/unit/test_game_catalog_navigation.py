"""Handler-level regression tests for the paginated game catalog callbacks
(Stage 1 of docs/GAMES_UX_MODERNIZATION_TODO.md): list -> detail -> rules,
all editing the same message, ownership restricted to whoever ran /game."""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

game_router = importlib.import_module("selara.presentation.handlers.game.router")


class FakeBot:
    def __init__(self) -> None:
        self.edits: list[dict] = []

    async def edit_message_text(self, *, chat_id, message_id, text, parse_mode, reply_markup):
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )


class FakeQuery:
    def __init__(self, *, user_id: int = 7, data: str) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id, username="tester", first_name="T", last_name=None, is_bot=False)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=-100500), message_id=555)
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


def _callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_list_callback_edits_message_to_requested_page() -> None:
    bot = FakeBot()
    query = FakeQuery(user_id=7, data="game:list:1:u7")
    await game_router.game_list_callback(query, bot=bot)

    assert len(bot.edits) == 1
    edit = bot.edits[0]
    assert edit["chat_id"] == -100500
    assert edit["message_id"] == 555
    for kind in game_router._catalog_kinds_for_page(1):
        assert f"game:detail:{kind}:1:u7" in _callbacks(edit["reply_markup"])
    assert query.answers == [(None, False)]


@pytest.mark.asyncio
async def test_list_callback_rejects_a_non_requester_tap() -> None:
    bot = FakeBot()
    query = FakeQuery(user_id=999, data="game:list:1:u7")
    await game_router.game_list_callback(query, bot=bot)

    assert bot.edits == []
    assert query.answers == [("Каталог игр доступен только тому, кто вызвал /game.", True)]


@pytest.mark.asyncio
async def test_list_callback_noop_page_indicator_just_acks() -> None:
    bot = FakeBot()
    query = FakeQuery(user_id=7, data="game:list:noop:u7")
    await game_router.game_list_callback(query, bot=bot)

    assert bot.edits == []
    assert query.answers == [(None, False)]


@pytest.mark.asyncio
async def test_detail_callback_edits_message_with_detail_card() -> None:
    bot = FakeBot()
    query = FakeQuery(user_id=7, data="game:detail:spy:0:u7")
    await game_router.game_detail_callback(query, bot=bot)

    assert len(bot.edits) == 1
    edit = bot.edits[0]
    assert "Шпион" in edit["text"] or game_router.GAME_DEFINITIONS["spy"].title in edit["text"]
    callbacks = _callbacks(edit["reply_markup"])
    assert "game:new:spy:u7" in callbacks
    assert "game:rules:spy:0:u7" in callbacks
    assert "game:list:0:u7" in callbacks


@pytest.mark.asyncio
async def test_detail_callback_rejects_a_non_requester_tap() -> None:
    bot = FakeBot()
    query = FakeQuery(user_id=999, data="game:detail:spy:0:u7")
    await game_router.game_detail_callback(query, bot=bot)

    assert bot.edits == []
    assert query.answers == [("Каталог игр доступен только тому, кто вызвал /game.", True)]


@pytest.mark.asyncio
async def test_rules_callback_edits_message_with_rules_text_and_back_button() -> None:
    bot = FakeBot()
    query = FakeQuery(user_id=7, data="game:rules:mafia:0:u7")
    await game_router.game_rules_callback(query, bot=bot)

    assert len(bot.edits) == 1
    edit = bot.edits[0]
    assert "как играть" in edit["text"].lower()
    callbacks = _callbacks(edit["reply_markup"])
    assert "game:detail:mafia:0:u7" in callbacks


@pytest.mark.asyncio
async def test_rules_callback_rejects_a_non_requester_tap() -> None:
    bot = FakeBot()
    query = FakeQuery(user_id=999, data="game:rules:mafia:0:u7")
    await game_router.game_rules_callback(query, bot=bot)

    assert bot.edits == []
    assert query.answers == [("Каталог игр доступен только тому, кто вызвал /game.", True)]
