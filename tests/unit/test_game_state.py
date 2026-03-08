import importlib

import pytest
import selara.presentation.game_state as game_state_module

from selara.presentation.game_state import (
    BUNKER_CARD_FIELDS,
    MAFIA_CIVILIAN_ROLES,
    MAFIA_MAFIA_ROLES,
    MAFIA_NEUTRAL_ROLES,
    MAFIA_ROLE_COMMISSIONER,
    MAFIA_ROLE_CIVILIAN,
    MAFIA_ROLE_MAFIA,
    MAFIA_ROLE_MANIAC,
    GameStore,
)

game_router_module = importlib.import_module("selara.presentation.handlers.game.router")


async def _create_started_mafia_game(store: GameStore):
    game, error = await store.create_lobby(
        kind="mafia",
        chat_id=100,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    for user_id in [2, 3, 4]:
        joined_game, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined_game is not None
        assert status == "joined"

    started_game, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    assert started_game is not None
    assert started_game.phase == "night"
    return started_game


async def _prepare_day_vote_phase(store: GameStore):
    game = await _create_started_mafia_game(store)

    game_after_night, _, error = await store.mafia_resolve_night(game_id=game.game_id)
    assert error is None
    assert game_after_night is not None
    assert game_after_night.phase == "day_discussion"

    day_vote_game, error = await store.mafia_open_day_vote(game_id=game.game_id)
    assert error is None
    assert day_vote_game is not None
    assert day_vote_game.phase == "day_vote"
    return day_vote_game


async def _create_started_bredovukha_game(store: GameStore):
    game, error = await store.create_lobby(
        kind="bredovukha",
        chat_id=500,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    for user_id in [2, 3]:
        joined_game, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined_game is not None
        assert status == "joined"

    tuned_game, tune_error = await store.set_bred_rounds(game_id=game.game_id, rounds=3)
    assert tune_error is None
    assert tuned_game is not None

    started_game, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    assert started_game is not None
    assert started_game.phase == "category_pick"
    assert started_game.bred_category_options
    assert started_game.bred_current_selector_user_id is not None
    return started_game


async def _open_bred_private_answers(store: GameStore, game_id: str):
    game = await store.get_game(game_id)
    assert game is not None
    assert game.phase == "category_pick"
    assert game.bred_current_selector_user_id is not None
    assert game.bred_category_options

    opened_game, category, error = await store.bred_choose_category(
        game_id=game_id,
        actor_user_id=game.bred_current_selector_user_id,
        option_index=0,
    )
    assert error is None
    assert opened_game is not None
    assert category is not None
    assert opened_game.phase == "private_answers"
    assert opened_game.bred_question_prompt
    assert opened_game.bred_correct_answer
    return opened_game


async def _create_started_whoami_game(store: GameStore):
    game, error = await store.create_lobby(
        kind="whoami",
        chat_id=550,
        chat_title="whoami-chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    for user_id in [2, 3]:
        joined_game, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined_game is not None
        assert status == "joined"

    started_game, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    assert started_game is not None
    assert started_game.kind == "whoami"
    assert started_game.status == "started"
    assert started_game.phase == "whoami_ask"

    started_game.roles = {
        1: "Лампа",
        2: "Чайник",
        3: "Ложка",
    }
    started_game.whoami_turn_order = [1, 2, 3]
    started_game.whoami_current_actor_index = 0
    started_game.whoami_current_actor_user_id = 1
    started_game.whoami_solved_user_ids.clear()
    started_game.whoami_finish_order.clear()
    return started_game


@pytest.mark.asyncio
async def test_mafia_night_ready_after_all_role_actions() -> None:
    store = GameStore()
    game = await _create_started_mafia_game(store)

    current_game, ready, error = await store.mafia_is_night_ready(game_id=game.game_id)
    assert error is None
    assert current_game is not None
    assert ready is False

    for _ in range(4):
        current_game, ready, error = await store.mafia_is_night_ready(game_id=game.game_id)
        assert error is None
        assert current_game is not None
        if ready:
            break

        for actor_user_id in sorted(current_game.alive_player_ids):
            targets = store._mafia_night_action_targets(current_game, actor_user_id=actor_user_id)
            if not targets:
                continue
            _, action_error = await store.mafia_register_night_action(
                game_id=game.game_id,
                actor_user_id=actor_user_id,
                target_user_id=targets[0],
            )
            assert action_error is None

    _, ready, error = await store.mafia_is_night_ready(game_id=game.game_id)
    assert error is None
    assert ready is True


@pytest.mark.asyncio
async def test_list_active_games_filters_finished_and_chat_ids() -> None:
    store = GameStore()
    first, first_error = await store.create_lobby(
        kind="dice",
        chat_id=101,
        chat_title="one",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    second, second_error = await store.create_lobby(
        kind="quiz",
        chat_id=202,
        chat_title="two",
        owner_user_id=2,
        owner_label="u2",
        reveal_eliminated_role=True,
    )
    assert first_error is None and first is not None
    assert second_error is None and second is not None

    await store.finish(game_id=first.game_id, winner_text="done")

    all_active = await store.list_active_games()
    assert [game.game_id for game in all_active] == [second.game_id]

    filtered = await store.list_active_games(chat_ids={101})
    assert filtered == []


@pytest.mark.asyncio
async def test_latest_role_game_ignores_finished_secret_role_games() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="whoami",
        chat_id=303,
        chat_title="whoami",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    joined_game, status = await store.join(game_id=game.game_id, user_id=2, user_label="u2")
    assert joined_game is not None
    assert status == "joined"
    joined_game, status = await store.join(game_id=game.game_id, user_id=3, user_label="u3")
    assert joined_game is not None
    assert status == "joined"

    started_game, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    assert started_game is not None
    assert started_game.roles.get(1)

    await store.finish(game_id=game.game_id, winner_text="done")

    latest_game, role = await store.get_latest_role_game_for_user(user_id=1)
    assert latest_game is None
    assert role is None


@pytest.mark.asyncio
async def test_mafia_role_assignment_uses_extended_role_pool() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="mafia",
        chat_id=1001,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    for user_id in range(2, 10):
        joined_game, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined_game is not None
        assert status == "joined"

    started_game, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    assert started_game is not None
    assert started_game.phase == "night"

    allowed_roles = MAFIA_CIVILIAN_ROLES | MAFIA_MAFIA_ROLES | MAFIA_NEUTRAL_ROLES
    assert set(started_game.roles.values()).issubset(allowed_roles)
    assert any(role in MAFIA_MAFIA_ROLES for role in started_game.roles.values())


@pytest.mark.asyncio
async def test_mafia_commissioner_and_maniac_reports() -> None:
    store = GameStore()
    game = await _create_started_mafia_game(store)

    # Stabilize roles for deterministic night reports.
    game.roles = {
        1: MAFIA_ROLE_COMMISSIONER,
        2: MAFIA_ROLE_MAFIA,
        3: MAFIA_ROLE_MANIAC,
        4: MAFIA_ROLE_CIVILIAN,
    }
    game.alive_player_ids = {1, 2, 3, 4}
    game.last_night_killers = {2}

    updated, error = await store.mafia_register_night_action(game_id=game.game_id, actor_user_id=1, target_user_id=2)
    assert error is None
    assert updated is not None
    updated, error = await store.mafia_register_night_action(game_id=game.game_id, actor_user_id=2, target_user_id=4)
    assert error is None
    assert updated is not None
    updated, error = await store.mafia_register_night_action(game_id=game.game_id, actor_user_id=3, target_user_id=2)
    assert error is None
    assert updated is not None

    game_after, resolution, error = await store.mafia_resolve_night(game_id=game.game_id)
    assert error is None
    assert game_after is not None
    assert resolution is not None
    assert resolution.private_reports
    # Комиссар получает минимум один отчёт.
    assert any(user_id == 1 for user_id, _ in resolution.private_reports)


@pytest.mark.asyncio
async def test_day_vote_unique_candidate_opens_execution_confirm() -> None:
    store = GameStore()
    game = await _prepare_day_vote_phase(store)

    alive = sorted(game.alive_player_ids)
    candidate = alive[-1]
    fallback_target = alive[0]

    for voter in alive:
        target = candidate if voter != candidate else fallback_target
        _, _, error = await store.mafia_register_day_vote(
            game_id=game.game_id,
            voter_user_id=voter,
            target_user_id=target,
        )
        assert error is None

    _, voted_count, alive_count = await store.mafia_get_vote_snapshot(game_id=game.game_id)
    assert voted_count == alive_count

    resolved_game, resolution, error = await store.mafia_resolve_day_vote(game_id=game.game_id)
    assert error is None
    assert resolved_game is not None
    assert resolution is not None
    assert resolution.opened_execution_confirm is True
    assert resolution.candidate_user_id == candidate
    assert resolved_game.phase == "day_execution_confirm"


@pytest.mark.asyncio
async def test_execution_confirm_passes_when_yes_more_than_no() -> None:
    store = GameStore()
    game = await _prepare_day_vote_phase(store)

    alive = sorted(game.alive_player_ids)
    candidate = alive[-1]
    fallback_target = alive[0]

    for voter in alive:
        target = candidate if voter != candidate else fallback_target
        _, _, error = await store.mafia_register_day_vote(
            game_id=game.game_id,
            voter_user_id=voter,
            target_user_id=target,
        )
        assert error is None

    game, resolution, error = await store.mafia_resolve_day_vote(game_id=game.game_id)
    assert error is None
    assert game is not None
    assert resolution is not None
    assert game.phase == "day_execution_confirm"

    voters = sorted(game.alive_player_ids)
    assert len(voters) >= 3

    _, _, error = await store.mafia_register_execution_confirm_vote(game_id=game.game_id, voter_user_id=voters[0], approve=True)
    assert error is None
    _, _, error = await store.mafia_register_execution_confirm_vote(game_id=game.game_id, voter_user_id=voters[1], approve=True)
    assert error is None
    _, _, error = await store.mafia_register_execution_confirm_vote(game_id=game.game_id, voter_user_id=voters[2], approve=False)
    assert error is None

    resolved_game, confirm, error = await store.mafia_resolve_execution_confirm(game_id=game.game_id)
    assert error is None
    assert resolved_game is not None
    assert confirm is not None
    assert confirm.passed is True
    assert confirm.executed_user_id == candidate
    assert candidate not in resolved_game.alive_player_ids


@pytest.mark.asyncio
async def test_execution_confirm_fails_when_yes_not_more_than_no() -> None:
    store = GameStore()
    game = await _prepare_day_vote_phase(store)

    alive = sorted(game.alive_player_ids)
    candidate = alive[-1]
    fallback_target = alive[0]

    for voter in alive:
        target = candidate if voter != candidate else fallback_target
        _, _, error = await store.mafia_register_day_vote(
            game_id=game.game_id,
            voter_user_id=voter,
            target_user_id=target,
        )
        assert error is None

    game, _, error = await store.mafia_resolve_day_vote(game_id=game.game_id)
    assert error is None
    assert game is not None
    assert game.phase == "day_execution_confirm"

    voters = sorted(game.alive_player_ids)
    assert len(voters) >= 2

    _, _, error = await store.mafia_register_execution_confirm_vote(game_id=game.game_id, voter_user_id=voters[0], approve=True)
    assert error is None
    _, _, error = await store.mafia_register_execution_confirm_vote(game_id=game.game_id, voter_user_id=voters[1], approve=False)
    assert error is None

    resolved_game, confirm, error = await store.mafia_resolve_execution_confirm(game_id=game.game_id)
    assert error is None
    assert resolved_game is not None
    assert confirm is not None
    assert confirm.passed is False
    assert confirm.executed_user_id is None
    assert candidate in resolved_game.alive_player_ids


@pytest.mark.asyncio
async def test_lobby_mafia_reveal_setting_can_be_toggled() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="mafia",
        chat_id=101,
        chat_title="chat",
        owner_user_id=10,
        owner_label="owner",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None
    assert game.reveal_eliminated_role is True

    updated_game, error = await store.set_mafia_reveal_eliminated_role(
        game_id=game.game_id,
        reveal_eliminated_role=False,
    )
    assert error is None
    assert updated_game is not None
    assert updated_game.reveal_eliminated_role is False


@pytest.mark.asyncio
async def test_number_game_finishes_after_correct_guess() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="number",
        chat_id=201,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    joined_game, status = await store.join(game_id=game.game_id, user_id=2, user_label="u2")
    assert joined_game is not None
    assert status == "joined"

    started_game, error = await store.start(game_id=game.game_id)
    assert error is None
    assert started_game is not None
    assert started_game.kind == "number"
    assert started_game.status == "started"
    assert started_game.number_secret is not None

    secret = started_game.number_secret
    wrong_guess = secret - 1 if secret > 1 else secret + 1
    game_after_wrong, wrong_result, error = await store.number_register_guess(
        game_id=started_game.game_id,
        user_id=1,
        guess=wrong_guess,
    )
    assert error is None
    assert game_after_wrong is not None
    assert wrong_result is not None
    assert wrong_result.direction in {"up", "down"}
    assert game_after_wrong.status == "started"

    finished_game, correct_result, error = await store.number_register_guess(
        game_id=started_game.game_id,
        user_id=2,
        guess=secret,
    )
    assert error is None
    assert finished_game is not None
    assert correct_result is not None
    assert correct_result.direction == "correct"
    assert correct_result.winner_user_id == 2
    assert finished_game.status == "finished"


@pytest.mark.asyncio
async def test_quiz_rounds_and_winner_resolution() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="quiz",
        chat_id=202,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    for user_id in [2, 3]:
        joined_game, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined_game is not None
        assert status == "joined"

    started_game, error = await store.start(game_id=game.game_id)
    assert error is None
    assert started_game is not None
    assert started_game.kind == "quiz"
    assert started_game.status == "started"
    assert started_game.quiz_questions

    leader = 1
    follower_ids = [2, 3]
    active_game = started_game

    while active_game.status == "started":
        question_index = active_game.quiz_current_question_index
        assert question_index is not None
        question = active_game.quiz_questions[question_index]

        game_after_leader_answer, _, error = await store.quiz_submit_answer(
            game_id=active_game.game_id,
            user_id=leader,
            option_index=question.answer_index,
        )
        assert error is None
        assert game_after_leader_answer is not None

        for follower_id in follower_ids:
            wrong_option = 0 if question.answer_index != 0 else 1
            game_after_follower_answer, _, error = await store.quiz_submit_answer(
                game_id=active_game.game_id,
                user_id=follower_id,
                option_index=wrong_option,
            )
            assert error is None
            assert game_after_follower_answer is not None

        resolved_game, resolution, error = await store.quiz_resolve_round(
            game_id=active_game.game_id,
            force=False,
        )
        assert error is None
        assert resolved_game is not None
        assert resolution is not None

        active_game = resolved_game

    assert active_game.status == "finished"
    assert active_game.winner_text is not None
    assert "u1" in active_game.winner_text
    assert active_game.quiz_scores[leader] > active_game.quiz_scores[2]


@pytest.mark.asyncio
async def test_spy_vote_finishes_game_on_majority() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="spy",
        chat_id=303,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    for user_id in [2, 3, 4]:
        joined_game, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined_game is not None
        assert status == "joined"

    started, error = await store.start(game_id=game.game_id)
    assert error is None
    assert started is not None
    assert started.kind == "spy"
    assert started.status == "started"

    target = 2
    g1, resolution, _, err1 = await store.spy_register_vote(game_id=game.game_id, voter_user_id=1, target_user_id=target)
    assert err1 is None
    assert g1 is not None
    assert resolution is None

    g2, resolution, _, err2 = await store.spy_register_vote(game_id=game.game_id, voter_user_id=3, target_user_id=target)
    assert err2 is None
    assert g2 is not None
    assert resolution is None

    g3, resolution, _, err3 = await store.spy_register_vote(game_id=game.game_id, voter_user_id=4, target_user_id=target)
    assert err3 is None
    assert g3 is not None
    assert resolution is not None
    assert g3.status == "finished"
    assert resolution.candidate_user_id == target
    assert resolution.winner_text is not None


@pytest.mark.asyncio
async def test_dice_game_waits_for_all_rolls() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="dice",
        chat_id=304,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    joined_game, status = await store.join(game_id=game.game_id, user_id=2, user_label="u2")
    assert joined_game is not None
    assert status == "joined"

    started_game, error = await store.start(game_id=game.game_id)
    assert error is None
    assert started_game is not None
    assert started_game.status == "started"
    assert started_game.phase == "freeplay"

    g1, roll1, e1 = await store.dice_register_roll(game_id=game.game_id, user_id=1)
    assert e1 is None
    assert g1 is not None
    assert roll1 is not None
    assert roll1.finished is False
    assert g1.status == "started"

    g2, roll2, e2 = await store.dice_register_roll(game_id=game.game_id, user_id=2)
    assert e2 is None
    assert g2 is not None
    assert roll2 is not None
    assert roll2.finished is True
    assert g2.status == "finished"


@pytest.mark.asyncio
async def test_set_player_label_updates_active_game() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="spy",
        chat_id=305,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    updated = await store.set_player_label(chat_id=305, user_id=1, user_label="НовыйНик")
    assert updated is not None
    assert updated.players[1] == "НовыйНик"


@pytest.mark.asyncio
async def test_migrate_chat_id_moves_active_game_mapping() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="spy",
        chat_id=401,
        chat_title="Old chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    migrated_count = await store.migrate_chat_id(old_chat_id=401, new_chat_id=1401, new_chat_title="New chat")
    assert migrated_count == 1

    old_active = await store.get_active_game_for_chat(chat_id=401)
    assert old_active is None

    new_active = await store.get_active_game_for_chat(chat_id=1401)
    assert new_active is not None
    assert new_active.game_id == game.game_id
    assert new_active.chat_id == 1401
    assert new_active.chat_title == "New chat"


@pytest.mark.asyncio
async def test_bredovukha_opens_vote_after_all_private_answers() -> None:
    store = GameStore()
    started_game = await _create_started_bredovukha_game(store)
    game = await _open_bred_private_answers(store, started_game.game_id)

    game1, result1, error1 = await store.bred_submit_lie(game_id=game.game_id, user_id=1, lie_text="Вариант альфа")
    assert error1 is None
    assert game1 is not None
    assert result1 is not None
    assert result1.vote_opened is False
    assert game1.phase == "private_answers"

    game2, result2, error2 = await store.bred_submit_lie(game_id=game.game_id, user_id=2, lie_text="Вариант бета")
    assert error2 is None
    assert game2 is not None
    assert result2 is not None
    assert result2.vote_opened is False
    assert game2.phase == "private_answers"

    game3, result3, error3 = await store.bred_submit_lie(game_id=game.game_id, user_id=3, lie_text="Вариант гамма")
    assert error3 is None
    assert game3 is not None
    assert result3 is not None
    assert result3.vote_opened is True
    assert game3.phase == "public_vote"
    assert len(game3.bred_options) == 4
    assert game3.bred_correct_answer in game3.bred_options


@pytest.mark.asyncio
async def test_bredovukha_scoring_and_winner_resolution() -> None:
    store = GameStore()
    started_game = await _create_started_bredovukha_game(store)
    game = started_game

    for round_no in range(1, 4):
        game = await _open_bred_private_answers(store, game.game_id)

        for user_id, lie_text in {
            1: f"Вариант альфа {round_no}",
            2: f"Вариант бета {round_no}",
            3: f"Вариант гамма {round_no}",
        }.items():
            game, result, error = await store.bred_submit_lie(game_id=game.game_id, user_id=user_id, lie_text=lie_text)
            assert error is None
            assert game is not None
            assert result is not None

        assert game.phase == "public_vote"
        correct_option_index = next(index for index, owner in enumerate(game.bred_option_owner_user_ids) if owner is None)
        option_by_user = {owner: index for index, owner in enumerate(game.bred_option_owner_user_ids) if owner is not None}
        assert 1 in option_by_user

        _, _, e1 = await store.bred_register_vote(game_id=game.game_id, voter_user_id=1, option_index=correct_option_index)
        assert e1 is None
        _, _, e2 = await store.bred_register_vote(game_id=game.game_id, voter_user_id=2, option_index=option_by_user[1])
        assert e2 is None
        _, _, e3 = await store.bred_register_vote(game_id=game.game_id, voter_user_id=3, option_index=option_by_user[1])
        assert e3 is None

        game, resolution, error = await store.bred_resolve_round(game_id=game.game_id, force=False)
        assert error is None
        assert game is not None
        assert resolution is not None
        assert resolution.round_no == round_no

    assert game.status == "finished"
    assert game.bred_scores[1] == 12
    assert game.bred_scores[2] == 0
    assert game.bred_scores[3] == 0
    assert game.winner_text is not None
    assert "u1" in game.winner_text


@pytest.mark.asyncio
async def test_bredovukha_rejects_duplicate_or_true_answers() -> None:
    store = GameStore()
    started_game = await _create_started_bredovukha_game(store)
    game = await _open_bred_private_answers(store, started_game.game_id)
    assert game.bred_correct_answer is not None

    game1, result1, error1 = await store.bred_submit_lie(game_id=game.game_id, user_id=1, lie_text="Уникальный вариант")
    assert error1 is None
    assert game1 is not None
    assert result1 is not None

    game2, result2, error2 = await store.bred_submit_lie(game_id=game.game_id, user_id=2, lie_text="Уникальный вариант")
    assert game2 is not None
    assert result2 is None
    assert error2 is not None

    game3, result3, error3 = await store.bred_submit_lie(
        game_id=game.game_id,
        user_id=2,
        lie_text=game.bred_correct_answer,
    )
    assert game3 is not None
    assert result3 is None
    assert error3 is not None


@pytest.mark.asyncio
async def test_bredovukha_accepts_single_digit_lie() -> None:
    store = GameStore()
    started_game = await _create_started_bredovukha_game(store)
    game = await _open_bred_private_answers(store, started_game.game_id)

    game1, result1, error1 = await store.bred_submit_lie(game_id=game.game_id, user_id=1, lie_text="7")
    assert error1 is None
    assert game1 is not None
    assert result1 is not None
    assert game1.bred_lies.get(1) == "7"


@pytest.mark.asyncio
async def test_bredovukha_round_settings_not_less_than_players() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="bredovukha",
        chat_id=600,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    for user_id in [2, 3]:
        joined, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined is not None
        assert status == "joined"

    updated, error = await store.set_bred_rounds(game_id=game.game_id, rounds=2)
    assert updated is not None
    assert error is not None

    updated, error = await store.set_bred_rounds(game_id=game.game_id, rounds=4)
    assert error is None
    assert updated is not None
    assert updated.bred_rounds == 4


@pytest.mark.asyncio
async def test_bredovukha_selector_order_includes_all_players() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="bredovukha",
        chat_id=601,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    for user_id in [2, 3]:
        joined, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined is not None
        assert status == "joined"

    updated, error = await store.set_bred_rounds(game_id=game.game_id, rounds=5)
    assert error is None
    assert updated is not None

    started, error = await store.start(game_id=game.game_id)
    assert error is None
    assert started is not None
    assert started.phase == "category_pick"
    assert started.bred_rounds == 5
    assert len(started.bred_selector_user_ids_by_round) == 5
    assert set(started.bred_selector_user_ids_by_round) == set(started.players.keys())
    assert started.bred_current_selector_user_id == started.bred_selector_user_ids_by_round[0]


async def _create_started_bunker_game(store: GameStore, *, players_count: int = 6):
    game, error = await store.create_lobby(
        kind="bunker",
        chat_id=701,
        chat_title="bunker-chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    for user_id in range(2, players_count + 1):
        joined, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined is not None
        assert status == "joined"

    started, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    assert started is not None
    assert started.kind == "bunker"
    assert started.status == "started"
    assert started.phase == "bunker_reveal"
    return started


async def _open_bunker_vote(store: GameStore, game_id: str):
    guard = 0
    while True:
        game = await store.get_game(game_id)
        assert game is not None
        if game.phase == "bunker_vote":
            return game
        assert game.phase == "bunker_reveal"
        assert game.bunker_current_actor_user_id is not None
        actor_user_id = game.bunker_current_actor_user_id
        revealed = game.bunker_revealed_fields.get(actor_user_id, set())
        field_key = next((field for field in BUNKER_CARD_FIELDS if field not in revealed), None)
        assert field_key is not None
        updated_game, result, error = await store.bunker_register_reveal(
            game_id=game_id,
            actor_user_id=actor_user_id,
            field_key=field_key,
        )
        assert error is None
        assert updated_game is not None
        assert result is not None
        guard += 1
        assert guard <= 20


@pytest.mark.asyncio
async def test_bunker_start_requires_min_players() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="bunker",
        chat_id=702,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    for user_id in [2, 3, 4, 5]:
        joined, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined is not None
        assert status == "joined"

    started, start_error = await store.start(game_id=game.game_id)
    assert started is not None
    assert start_error is not None
    assert "минимум" in start_error.lower()


@pytest.mark.asyncio
async def test_bunker_default_seats_and_manual_lobby_tuning() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="bunker",
        chat_id=703,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None
    assert game.bunker_seats == 2

    for user_id in [2, 3, 4, 5, 6, 7, 8, 9, 10]:
        joined, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined is not None
        assert status == "joined"

    updated = await store.get_game(game.game_id)
    assert updated is not None
    assert updated.bunker_seats == 4

    tuned, tune_error = await store.set_bunker_seats(game_id=game.game_id, seats=3)
    assert tune_error is None
    assert tuned is not None
    assert tuned.bunker_seats == 3
    assert tuned.bunker_seats_tuned is True

    joined, status = await store.join(game_id=game.game_id, user_id=11, user_label="u11")
    assert joined is not None
    assert status == "joined"
    assert joined.bunker_seats == 3


@pytest.mark.asyncio
async def test_bunker_reveal_round_opens_vote() -> None:
    store = GameStore()
    game = await _create_started_bunker_game(store, players_count=6)

    assert game.bunker_current_actor_user_id is not None
    assert game.bunker_round_reveal_user_ids
    participants_count = len(game.bunker_round_reveal_user_ids)
    assert participants_count == len(game.alive_player_ids)

    updated = await _open_bunker_vote(store, game.game_id)
    assert updated.phase == "bunker_vote"
    assert updated.bunker_current_actor_user_id is None


@pytest.mark.asyncio
async def test_bunker_vote_tie_keeps_all_alive() -> None:
    store = GameStore()
    game = await _create_started_bunker_game(store, players_count=6)
    game = await _open_bunker_vote(store, game.game_id)
    alive = sorted(game.alive_player_ids)

    _, _, self_vote_error = await store.bunker_register_vote(
        game_id=game.game_id,
        voter_user_id=alive[0],
        target_user_id=alive[0],
    )
    assert self_vote_error is not None

    for idx, voter_user_id in enumerate(alive):
        target_user_id = alive[(idx + 1) % len(alive)]
        _, _, error = await store.bunker_register_vote(
            game_id=game.game_id,
            voter_user_id=voter_user_id,
            target_user_id=target_user_id,
        )
        assert error is None

    resolved_game, resolution, error = await store.bunker_resolve_vote(game_id=game.game_id, force=False)
    assert error is None
    assert resolved_game is not None
    assert resolution is not None
    assert resolution.tie is True
    assert resolution.eliminated_user_id is None
    assert len(resolved_game.alive_player_ids) == 6
    assert resolved_game.status == "started"
    assert resolved_game.phase in {"bunker_reveal", "bunker_vote"}


@pytest.mark.asyncio
async def test_bunker_elimination_reveals_card_and_can_finish() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="bunker",
        chat_id=704,
        chat_title="chat",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None
    for user_id in [2, 3, 4, 5, 6]:
        joined, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined is not None
        assert status == "joined"

    tuned, tune_error = await store.set_bunker_seats(game_id=game.game_id, seats=5)
    assert tune_error is None
    assert tuned is not None
    assert tuned.bunker_seats == 5

    started, start_error = await store.start(game_id=game.game_id)
    assert start_error is None
    assert started is not None
    started = await _open_bunker_vote(store, started.game_id)

    alive = sorted(started.alive_player_ids)
    candidate = alive[-1]
    for voter_user_id in alive:
        if voter_user_id == candidate:
            target_user_id = alive[0]
        else:
            target_user_id = candidate
        _, _, vote_error = await store.bunker_register_vote(
            game_id=started.game_id,
            voter_user_id=voter_user_id,
            target_user_id=target_user_id,
        )
        assert vote_error is None

    resolved, resolution, error = await store.bunker_resolve_vote(game_id=started.game_id, force=False)
    assert error is None
    assert resolved is not None
    assert resolution is not None
    assert resolution.tie is False
    assert resolution.eliminated_user_id == candidate
    assert resolution.eliminated_card is not None
    assert resolved.status == "finished"
    assert resolved.winner_text is not None
    assert len(resolved.alive_player_ids) == 5


@pytest.mark.asyncio
async def test_bunker_field_uniqueness_and_overflow_fallback(monkeypatch) -> None:
    store = GameStore()
    started = await _create_started_bunker_game(store, players_count=6)

    for field_key in BUNKER_CARD_FIELDS:
        values = [getattr(card, field_key) for card in started.bunker_cards.values()]
        assert len(values) == len(set(values))

    tiny_data = {
        "catastrophes": ("Катастрофа А",),
        "bunker_conditions": ("Условие А",),
        "professions": ("Профессия А", "Профессия Б"),
        "ages": ("20 лет", "21 год"),
        "genders": ("Мужчина", "Женщина"),
        "health_conditions": ("Здоров", "Астма"),
        "skills": ("Навык А", "Навык Б"),
        "hobbies": ("Хобби А", "Хобби Б"),
        "phobias": ("Фобия А", "Фобия Б"),
        "traits": ("Особенность А", "Особенность Б"),
        "items": ("Предмет А", "Предмет Б"),
    }
    monkeypatch.setattr(game_state_module, "BUNKER_DATA", tiny_data)

    overflow_store = GameStore()
    overflow_started = await _create_started_bunker_game(overflow_store, players_count=6)
    assert overflow_started.bunker_pool_overflow_fields
    assert len(overflow_started.bunker_pool_overflow_fields) >= 1

    has_duplicates = False
    for field_key in overflow_started.bunker_pool_overflow_fields:
        values = [getattr(card, field_key) for card in overflow_started.bunker_cards.values()]
        if len(set(values)) < len(values):
            has_duplicates = True
            break
    assert has_duplicates is True


@pytest.mark.asyncio
async def test_whoami_correct_guess_marks_player_solved_and_continues_game() -> None:
    store = GameStore()
    started = await _create_started_whoami_game(store)

    game, resolution, error = await store.whoami_guess_identity(
        game_id=started.game_id,
        actor_user_id=1,
        guess_text="лампа",
    )

    assert error is None
    assert game is not None
    assert resolution is not None
    assert resolution.guessed_correctly is True
    assert resolution.finished is False
    assert resolution.actual_identity is None
    assert game.status == "started"
    assert game.phase == "whoami_ask"
    assert game.whoami_solved_user_ids == {1}
    assert game.whoami_finish_order == [1]
    assert game.whoami_current_actor_user_id == 2
    assert game.winner_text is None


@pytest.mark.asyncio
async def test_whoami_solved_player_cannot_ask_or_guess_but_can_answer() -> None:
    store = GameStore()
    started = await _create_started_whoami_game(store)

    solved_game, solved_resolution, solved_error = await store.whoami_guess_identity(
        game_id=started.game_id,
        actor_user_id=1,
        guess_text="лампа",
    )
    assert solved_error is None
    assert solved_game is not None
    assert solved_resolution is not None
    assert solved_game.whoami_current_actor_user_id == 2

    solved_game.whoami_current_actor_user_id = 1
    solved_game.whoami_current_actor_index = 0

    ask_game, ask_result, ask_error = await store.whoami_submit_question(
        game_id=started.game_id,
        actor_user_id=1,
        question_text="Я предмет?",
    )
    assert ask_game is not None
    assert ask_result is None
    assert ask_error == "Вы уже разгадали карточку и больше не задаёте вопросы"

    guess_game, guess_result, guess_error = await store.whoami_guess_identity(
        game_id=started.game_id,
        actor_user_id=1,
        guess_text="лампа",
    )
    assert guess_game is not None
    assert guess_result is None
    assert guess_error == "Вы уже разгадали карточку и больше не ходите"

    solved_game.whoami_current_actor_user_id = 2
    solved_game.whoami_current_actor_index = 1

    game, question_result, error = await store.whoami_submit_question(
        game_id=started.game_id,
        actor_user_id=2,
        question_text="Я кухонный предмет?",
    )
    assert error is None
    assert game is not None
    assert question_result is not None
    assert game.phase == "whoami_answer"

    answered_game, answer_resolution, answer_error = await store.whoami_answer_question(
        game_id=started.game_id,
        responder_user_id=1,
        answer_code="no",
    )
    assert answer_error is None
    assert answered_game is not None
    assert answer_resolution is not None
    assert answer_resolution.responder_user_id == 1
    assert answer_resolution.answer_label == "Нет"
    assert answered_game.phase == "whoami_ask"
    assert answered_game.whoami_current_actor_user_id == 3


@pytest.mark.asyncio
async def test_whoami_finishes_only_after_all_players_solve_and_preserves_finish_order() -> None:
    store = GameStore()
    started = await _create_started_whoami_game(store)

    game, resolution1, error1 = await store.whoami_guess_identity(
        game_id=started.game_id,
        actor_user_id=1,
        guess_text="лампа",
    )
    assert error1 is None
    assert game is not None
    assert resolution1 is not None
    assert resolution1.finished is False
    assert game.whoami_current_actor_user_id == 2

    game, resolution2, error2 = await store.whoami_guess_identity(
        game_id=started.game_id,
        actor_user_id=2,
        guess_text="чайник",
    )
    assert error2 is None
    assert game is not None
    assert resolution2 is not None
    assert resolution2.finished is False
    assert game.whoami_current_actor_user_id == 3

    game, resolution3, error3 = await store.whoami_guess_identity(
        game_id=started.game_id,
        actor_user_id=3,
        guess_text="ложка",
    )

    assert error3 is None
    assert game is not None
    assert resolution3 is not None
    assert resolution3.guessed_correctly is True
    assert resolution3.finished is True
    assert resolution3.actual_identity == "Ложка"
    assert game.status == "finished"
    assert game.phase == "finished"
    assert game.whoami_current_actor_user_id is None
    assert game.whoami_solved_user_ids == {1, 2, 3}
    assert game.whoami_finish_order == [1, 2, 3]
    assert game.winner_text == "Все карточки разгаданы. Порядок финиша: u1, u2, u3."
    assert game_router_module._winner_ids_for_whoami(game) == {1, 2, 3}


@pytest.mark.asyncio
async def test_whoami_create_lobby_accepts_explicit_category() -> None:
    store = GameStore()

    game, error = await store.create_lobby(
        kind="whoami",
        chat_id=3030,
        chat_title="whoami",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
        whoami_category="18+ и пикантное",
        actions_18_enabled=True,
    )

    assert error is None
    assert game is not None
    assert game.whoami_category == "18+ и пикантное"


@pytest.mark.asyncio
async def test_whoami_create_lobby_rejects_explicit_category_when_18_disabled() -> None:
    store = GameStore()

    game, error = await store.create_lobby(
        kind="whoami",
        chat_id=3031,
        chat_title="whoami",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
        whoami_category="18+ и пикантное",
        actions_18_enabled=False,
    )

    assert game is None
    assert error == "18+ темы для игры «Кто я» отключены в этом чате"


@pytest.mark.asyncio
async def test_whoami_set_category_updates_lobby_without_cycle() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="whoami",
        chat_id=4040,
        chat_title="whoami",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    updated, update_error = await store.set_whoami_category(
        game_id=game.game_id,
        category="Honkai: Star Rail",
    )

    assert update_error is None
    assert updated is not None
    assert updated.whoami_category == "Honkai: Star Rail"


@pytest.mark.asyncio
async def test_whoami_set_category_rejects_explicit_when_18_disabled() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="whoami",
        chat_id=4041,
        chat_title="whoami",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    updated, update_error = await store.set_whoami_category(
        game_id=game.game_id,
        category="18+ и пикантное",
        actions_18_enabled=False,
    )

    assert updated is not None
    assert update_error == "18+ темы для игры «Кто я» отключены в этом чате"
    assert updated.whoami_category is None


@pytest.mark.asyncio
async def test_whoami_cycle_category_skips_explicit_when_18_disabled() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="whoami",
        chat_id=4042,
        chat_title="whoami",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    updated, update_error = await store.cycle_whoami_category(
        game_id=game.game_id,
        actions_18_enabled=False,
    )

    assert update_error is None
    assert updated is not None
    assert updated.whoami_category in game_state_module.allowed_whoami_categories(actions_18_enabled=False)
    assert updated.whoami_category != "18+ и пикантное"


@pytest.mark.asyncio
async def test_whoami_start_random_category_excludes_explicit_when_18_disabled() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="whoami",
        chat_id=4043,
        chat_title="whoami",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    for user_id in [2, 3]:
        joined, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined is not None
        assert status == "joined"

    started, start_error = await store.start(game_id=game.game_id, actions_18_enabled=False)

    assert start_error is None
    assert started is not None
    assert started.whoami_category in game_state_module.allowed_whoami_categories(actions_18_enabled=False)
    assert started.whoami_category != "18+ и пикантное"


@pytest.mark.asyncio
async def test_whoami_start_rejects_explicit_category_when_18_disabled_after_lobby_created() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="whoami",
        chat_id=4044,
        chat_title="whoami",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
        whoami_category="18+ и пикантное",
        actions_18_enabled=True,
    )
    assert error is None
    assert game is not None

    for user_id in [2, 3]:
        joined, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined is not None
        assert status == "joined"

    started, start_error = await store.start(game_id=game.game_id, actions_18_enabled=False)

    assert started is not None
    assert start_error == "18+ темы для игры «Кто я» отключены в этом чате"
    assert started.status == "lobby"
    assert started.phase == "lobby"


@pytest.mark.asyncio
async def test_spy_create_lobby_accepts_category() -> None:
    store = GameStore()

    game, error = await store.create_lobby(
        kind="spy",
        chat_id=5050,
        chat_title="spy",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
        spy_category="Транспорт и логистика",
    )

    assert error is None
    assert game is not None
    assert game.spy_category == "Транспорт и логистика"


@pytest.mark.asyncio
async def test_spy_set_category_updates_lobby_without_cycle() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="spy",
        chat_id=5051,
        chat_title="spy",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    updated, update_error = await store.set_spy_category(
        game_id=game.game_id,
        category="Отдых и туризм",
    )

    assert update_error is None
    assert updated is not None
    assert updated.spy_category == "Отдых и туризм"


@pytest.mark.asyncio
async def test_spy_cycle_category_moves_to_named_theme() -> None:
    store = GameStore()
    game, error = await store.create_lobby(
        kind="spy",
        chat_id=5052,
        chat_title="spy",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
    )
    assert error is None
    assert game is not None

    updated, update_error = await store.cycle_spy_category(game_id=game.game_id)

    assert update_error is None
    assert updated is not None
    assert updated.spy_category in game_state_module.SPY_CATEGORIES


@pytest.mark.asyncio
async def test_spy_start_uses_selected_category_pool() -> None:
    store = GameStore()
    category = "Транспорт и логистика"
    game, error = await store.create_lobby(
        kind="spy",
        chat_id=5053,
        chat_title="spy",
        owner_user_id=1,
        owner_label="u1",
        reveal_eliminated_role=True,
        spy_category=category,
    )
    assert error is None
    assert game is not None

    for user_id in [2, 3]:
        joined, status = await store.join(game_id=game.game_id, user_id=user_id, user_label=f"u{user_id}")
        assert joined is not None
        assert status == "joined"

    started, start_error = await store.start(game_id=game.game_id)

    assert start_error is None
    assert started is not None
    assert started.spy_category == category
    assert started.spy_location in game_state_module.SPY_LOCATIONS_BY_CATEGORY[category]


def test_whoami_cards_include_genshin_hsr_and_explicit_categories() -> None:
    assert "18+ и пикантное" in game_state_module.WHOAMI_CATEGORIES
    assert "Genshin Impact" in game_state_module.WHOAMI_CATEGORIES
    assert "Honkai: Star Rail" in game_state_module.WHOAMI_CATEGORIES
    assert len(game_state_module.WHOAMI_CARDS_BY_CATEGORY["18+ и пикантное"]) >= 20
    assert len(game_state_module.WHOAMI_CARDS_BY_CATEGORY["Genshin Impact"]) >= 20
    assert len(game_state_module.WHOAMI_CARDS_BY_CATEGORY["Honkai: Star Rail"]) >= 20


def test_spy_locations_are_grouped_by_category() -> None:
    assert "Транспорт и логистика" in game_state_module.SPY_CATEGORIES
    assert "Отдых и туризм" in game_state_module.SPY_CATEGORIES
    assert len(game_state_module.SPY_LOCATIONS_BY_CATEGORY["Транспорт и логистика"]) >= 10
    assert len(game_state_module.SPY_LOCATIONS_BY_CATEGORY["Отдых и туризм"]) >= 10
    assert "Аэропорт" in game_state_module.SPY_LOCATIONS_BY_CATEGORY["Транспорт и логистика"]
