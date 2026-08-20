"""Regression test for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 3: fixing
just the Bredovukha DM button's URL (adding ?start=game_{id}) would have been
a no-op without this -- _show_role_for_user (the /start deep-link handler)
had no branch for "bredovukha" at all and fell through to "В этой игре нет
секретных ролей", which is technically true but useless: the button's whole
point is to open the private lie-submission prompt, not a message about
secret roles."""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from selara.presentation.game_state import GameStore

game_router = importlib.import_module("selara.presentation.handlers.game.router")


class FakeMessage:
    def __init__(self, *, user_id: int) -> None:
        self.from_user = SimpleNamespace(id=user_id, username="u", first_name="U", last_name=None, is_bot=False)
        self.answers: list[tuple[str, dict]] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append((text, kwargs))


async def _bredovukha_in_private_answers(store: GameStore):
    game, error = await store.create_lobby(
        kind="bredovukha", chat_id=-100, chat_title="chat",
        owner_user_id=1, owner_label="owner", reveal_eliminated_role=True,
    )
    assert error is None
    for uid in (2, 3):
        await store.join(game_id=game.game_id, user_id=uid, user_label=f"u{uid}")
    started, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    assert started.phase == "category_pick"

    picked, _, pick_error = await store.bred_force_pick_category(game_id=started.game_id)
    assert pick_error is None
    assert picked.phase == "private_answers"
    return picked


@pytest.mark.asyncio
async def test_deep_link_shows_the_private_answer_prompt_not_a_no_roles_message(monkeypatch) -> None:
    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    game = await _bredovukha_in_private_answers(store)

    message = FakeMessage(user_id=2)
    await game_router._show_role_for_user(message, game_id=game.game_id)

    assert message.answers, "expected a reply"
    text = message.answers[-1][0]
    assert "нет секретных ролей" not in text
    assert "Факт с пропуском" in text


@pytest.mark.asyncio
async def test_deep_link_rejects_a_non_participant(monkeypatch) -> None:
    store = GameStore()
    monkeypatch.setattr(game_router, "GAME_STORE", store)
    game = await _bredovukha_in_private_answers(store)

    message = FakeMessage(user_id=999)
    await game_router._show_role_for_user(message, game_id=game.game_id)

    assert message.answers[-1][0] == "Вы не участвуете в этой игре."
