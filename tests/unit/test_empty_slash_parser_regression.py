"""Regression for a bare slash in command access and group middleware (#235)."""
from __future__ import annotations

import pytest

from selara.presentation.commands.access import resolve_command_key_input


@pytest.mark.parametrize("text", ["", " ", "\t", "/", "/ ", "/   ", "  /  ", "/\t", "/ \t ", " / \n "])
def test_invalid_empty_slash_command_returns_none(text: str) -> None:
    assert resolve_command_key_input(text) is None


@pytest.mark.parametrize(("raw", "key"), [
    ("/start", "start"),
    ("/start@selarabot", "start"),
    ("/start@selarabot payload", "start"),
    ("/pet", "pet"),
    ("/pet@selarabot", "pet"),
    ("/pet @friend", "family_pet"),
    ("/pet@selarabot @friend", "family_pet"),
    ("/game", "game"),
])
def test_normal_slash_command_semantics_unchanged(raw: str, key: str) -> None:
    assert resolve_command_key_input(raw) == key
