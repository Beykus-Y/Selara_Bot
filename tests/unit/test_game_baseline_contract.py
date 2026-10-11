from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from selara.presentation.game_state import (
    GAME_DEFINITIONS,
    GAME_LAUNCHABLE_KINDS,
    GameKind,
    GamePhase,
)

_BASELINE = Path(__file__).resolve().parents[2] / "docs" / "GAME_UX_BASELINE.md"
_ROW = re.compile(r"^\| (\w+) \| [^|]+\| (\d+) \| (yes|no) \|", re.MULTILINE)


def _baseline_text() -> str:
    return _BASELINE.read_text(encoding="utf-8")


def _documented_games() -> dict[str, tuple[int, bool]]:
    # Rows of the section 1 table: | kind | title | min players | secret roles | pinned by |
    return {
        kind: (int(min_players), secret_roles == "yes")
        for kind, min_players, secret_roles in _ROW.findall(_baseline_text())
    }


def _documented_phases() -> tuple[str, ...]:
    # Section 2 lists phases in backticks, after its heading line.
    section = _baseline_text().split("## 2.", 1)[1].split("## 3.", 1)[0]
    body = section.split("\n", 1)[1]
    return tuple(re.findall(r"`(\w+)`", body))


def test_documented_games_match_launchable_kinds() -> None:
    documented = _documented_games()
    assert len(documented) == 8
    assert set(documented) == set(GAME_LAUNCHABLE_KINDS)
    assert len(GAME_LAUNCHABLE_KINDS) == len(set(GAME_LAUNCHABLE_KINDS)) == 8


def test_documented_player_floor_and_secret_roles_match_runtime() -> None:
    for kind, (min_players, secret_roles) in _documented_games().items():
        definition = GAME_DEFINITIONS[kind]
        assert definition.min_players == min_players, kind
        assert definition.secret_roles is secret_roles, kind


def test_guessing_number_game_stays_removed() -> None:
    assert "number" not in GAME_LAUNCHABLE_KINDS
    assert all("number" not in kind for kind in get_args(GameKind))


def test_game_kind_literal_matches_launchable_kinds() -> None:
    assert set(get_args(GameKind)) == set(GAME_LAUNCHABLE_KINDS)


def test_documented_phases_match_phase_literal() -> None:
    assert _documented_phases() == get_args(GamePhase)
