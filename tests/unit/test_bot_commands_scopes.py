from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara import main as main_module

PRESENTATION_DIR = Path(__file__).resolve().parents[2] / "src" / "selara" / "presentation"
COMMAND_FILTER_RE = re.compile(r"Command\(\s*['\"]([a-z0-9_]+)['\"]")
COMMAND_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")

PRIVATE_ONLY_COMMANDS = {"start", "ai", "memory", "ai_reset", "forget_all", "autocfg", "role", "terms"}


def _registered_command_names() -> set[str]:
    names: set[str] = set()
    for path in PRESENTATION_DIR.rglob("*.py"):
        names.update(COMMAND_FILTER_RE.findall(path.read_text(encoding="utf-8")))
    return names


def _names(commands) -> list[str]:
    return [command.command for command in commands]


@pytest.mark.parametrize("summary_enabled", [True, False])
@pytest.mark.parametrize("scope_name", ["private", "group"])
def test_menu_entries_follow_telegram_limits(scope_name: str, summary_enabled: bool) -> None:
    commands = (
        main_module.build_bot_commands_private()
        if scope_name == "private"
        else main_module.build_bot_commands_group(summary_enabled=summary_enabled)
    )
    names = _names(commands)

    assert 0 < len(names) <= 100
    assert len(names) == len(set(names)), "duplicate command in menu"
    for command in commands:
        assert COMMAND_NAME_RE.match(command.command), command.command
        assert 0 < len(command.description) <= 256, command.command


@pytest.mark.parametrize("summary_enabled", [True, False])
def test_every_menu_command_has_a_registered_handler(summary_enabled: bool) -> None:
    registered = _registered_command_names()
    menu = main_module.build_bot_commands_private() + main_module.build_bot_commands_group(
        summary_enabled=summary_enabled
    )

    missing = {command.command for command in menu} - registered
    assert not missing, f"menu lists commands without a handler: {sorted(missing)}"


def test_summary_is_listed_in_groups_only_when_llm_enabled() -> None:
    assert "summary" not in _names(main_module.build_bot_commands_group(summary_enabled=False))
    assert "summary" in _names(main_module.build_bot_commands_group(summary_enabled=True))


def test_private_only_commands_are_not_offered_in_groups() -> None:
    group = set(_names(main_module.build_bot_commands_group(summary_enabled=True)))
    private = set(_names(main_module.build_bot_commands_private()))

    assert not (PRIVATE_ONLY_COMMANDS & group)
    assert PRIVATE_ONLY_COMMANDS <= private


def test_group_menu_keeps_relationship_and_game_entries() -> None:
    group = set(_names(main_module.build_bot_commands_group(summary_enabled=False)))

    assert {"help", "game", "top", "active", "relation", "pair", "marry", "vow"} <= group


@pytest.mark.asyncio
async def test_sync_sets_private_and_group_scopes() -> None:
    bot = SimpleNamespace(set_my_commands=AsyncMock())

    await main_module.sync_bot_commands(bot, summary_enabled=True)

    assert bot.set_my_commands.await_count == 2
    scopes = [call.kwargs["scope"].type for call in bot.set_my_commands.await_args_list]
    assert scopes == ["all_private_chats", "all_group_chats"]

    private_call, group_call = bot.set_my_commands.await_args_list
    assert _names(private_call.args[0]) == _names(main_module.build_bot_commands_private())
    assert _names(group_call.args[0]) == _names(
        main_module.build_bot_commands_group(summary_enabled=True)
    )


@pytest.mark.asyncio
async def test_sync_failure_in_one_scope_does_not_stop_the_other_or_raise() -> None:
    bot = SimpleNamespace(set_my_commands=AsyncMock(side_effect=[RuntimeError("boom"), None]))

    await main_module.sync_bot_commands(bot, summary_enabled=False)

    assert bot.set_my_commands.await_count == 2
