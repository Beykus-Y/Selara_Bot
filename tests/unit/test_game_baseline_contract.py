from __future__ import annotations

from typing import get_args

from selara.presentation.game_state import (
    GAME_DEFINITIONS,
    GAME_LAUNCHABLE_KINDS,
    GameKind,
    GamePhase,
)

# Mirrors the table in docs/GAME_UX_BASELINE.md. Change both together.
_EXPECTED_LAUNCHABLE = {
    "zlobcards": (3, False),
    "spy": (3, True),
    "whoami": (3, True),
    "mafia": (4, True),
    "dice": (2, False),
    "quiz": (2, False),
    "bredovukha": (3, False),
    "bunker": (6, True),
}

_EXPECTED_PHASES = (
    "lobby",
    "freeplay",
    "whoami_ask",
    "whoami_answer",
    "category_pick",
    "private_answers",
    "public_vote",
    "bunker_reveal",
    "bunker_vote",
    "night",
    "day_discussion",
    "day_vote",
    "day_execution_confirm",
    "finished",
)


def test_launchable_kinds_are_exactly_the_eight_documented_games() -> None:
    assert len(GAME_LAUNCHABLE_KINDS) == len(set(GAME_LAUNCHABLE_KINDS)) == 8
    assert set(GAME_LAUNCHABLE_KINDS) == set(_EXPECTED_LAUNCHABLE)


def test_guessing_number_game_stays_removed() -> None:
    assert "number" not in GAME_LAUNCHABLE_KINDS
    assert all("number" not in kind for kind in get_args(GameKind))


def test_each_launchable_game_matches_documented_player_floor_and_secret_roles() -> None:
    for kind in GAME_LAUNCHABLE_KINDS:
        definition = GAME_DEFINITIONS[kind]
        min_players, secret_roles = _EXPECTED_LAUNCHABLE[kind]
        assert definition.min_players == min_players, kind
        assert definition.secret_roles is secret_roles, kind


def test_game_kind_literal_matches_launchable_kinds() -> None:
    assert set(get_args(GameKind)) == set(GAME_LAUNCHABLE_KINDS)


def test_phase_literal_matches_documented_phases() -> None:
    assert get_args(GamePhase) == _EXPECTED_PHASES
