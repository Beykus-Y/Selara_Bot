import importlib

from selara.presentation.game_state import GameKind, GroupGame
from selara.presentation.handlers.game.router import _build_game_rewards

game_router_module = importlib.import_module("selara.presentation.handlers.game.router")


def _base_game(kind: GameKind) -> GroupGame:
    return GroupGame(
        game_id="g1",
        kind=kind,
        chat_id=-100,
        chat_title="test",
        owner_user_id=1,
        players={1: "u1", 2: "u2", 3: "u3"},
        status="finished",
        winner_text="ok",
    )


def test_game_rewards_ranges_for_all_game_modes(monkeypatch) -> None:
    def fake_randint(low: int, high: int) -> int:
        if low >= 100:
            return high - 1
        return low + 1

    monkeypatch.setattr(game_router_module.random, "randint", fake_randint)

    # dice
    dice = _base_game("dice")
    dice.dice_scores = {1: 2, 2: 6, 3: 4}
    rewards = _build_game_rewards(dice)
    assert rewards[2] > 100
    assert 10 <= rewards[1] <= 20
    assert 10 <= rewards[3] <= 20

    # number (winner определяется override в обработчике игры)
    number = _base_game("number")
    rewards = _build_game_rewards(number, winner_user_ids_override={3})
    assert rewards[3] > 100
    assert 10 <= rewards[1] <= 20
    assert 10 <= rewards[2] <= 20

    # quiz
    quiz = _base_game("quiz")
    quiz.quiz_scores = {1: 5, 2: 7, 3: 4}
    rewards = _build_game_rewards(quiz)
    assert rewards[2] > 100
    assert 10 <= rewards[1] <= 20
    assert 10 <= rewards[3] <= 20

    # bredovukha
    bred = _base_game("bredovukha")
    bred.bred_scores = {1: 3, 2: 2, 3: 8}
    rewards = _build_game_rewards(bred)
    assert rewards[3] > 100
    assert 10 <= rewards[1] <= 20
    assert 10 <= rewards[2] <= 20

    # bunker (победители = выжившие)
    bunker = _base_game("bunker")
    bunker.alive_player_ids = {1, 3}
    rewards = _build_game_rewards(bunker)
    assert rewards[1] > 100
    assert rewards[3] > 100
    assert 10 <= rewards[2] <= 20

    # spy
    spy = _base_game("spy")
    spy.roles = {1: "Шпион", 2: "Мирный", 3: "Мирный"}
    spy.winner_text = "Победа шпиона"
    rewards = _build_game_rewards(spy)
    assert rewards[1] > 100
    assert 10 <= rewards[2] <= 20
    assert 10 <= rewards[3] <= 20

    # mafia
    mafia = _base_game("mafia")
    mafia.roles = {1: "Рядовая мафия", 2: "Мирный житель", 3: "Мирный житель"}
    mafia.winner_text = "Победа мирных"
    rewards = _build_game_rewards(mafia)
    assert rewards[2] > 100
    assert rewards[3] > 100
    assert 10 <= rewards[1] <= 20


def test_game_rewards_not_granted_for_stopped_game() -> None:
    game = _base_game("quiz")
    game.winner_text = "Игра завершена по решению ведущего."
    assert _build_game_rewards(game) == {}
