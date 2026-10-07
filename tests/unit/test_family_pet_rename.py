from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from aiogram.filters import CommandObject
from alembic.migration import MigrationContext
from alembic.operations import Operations

from selara.presentation.commands.access import KNOWN_COMMAND_KEYS, resolve_command_key_input
from selara.presentation.commands.catalog import COMMAND_KEY_DEFAULT_SOURCE_TRIGGER, match_builtin_command
from selara.presentation.handlers import chat_assistant
from selara.presentation.middlewares.chat_write_lock import is_write_locked_command


@pytest.mark.parametrize("raw", ["/bepet", "/bepet @user", "/bepet@selara_bot", "/pet @user", "/pet@selara_bot @user"])
def test_slash_commands_resolve_to_family_pet_key(raw: str) -> None:
    assert resolve_command_key_input(raw) == "family_pet"


@pytest.mark.parametrize("raw", ["/pet", "/pet@selara_bot", "/pets", "/pet_new кот Мурка", "/pet_shop"])
def test_bare_pet_commands_belong_to_ai_pets(raw: str) -> None:
    assert resolve_command_key_input(raw) == "pet"


@pytest.mark.parametrize("raw", ["стать питомцем", "стать питомцем @user"])
def test_text_trigger_resolves_to_family_pet_key(raw: str) -> None:
    assert resolve_command_key_input(raw) == "family_pet"
    match = match_builtin_command(raw)
    assert match is not None and match.command_key == "family_pet"


def test_pet_key_now_belongs_to_ai_pets() -> None:
    assert "family_pet" in KNOWN_COMMAND_KEYS
    assert "pet" in KNOWN_COMMAND_KEYS
    assert COMMAND_KEY_DEFAULT_SOURCE_TRIGGER["family_pet"] == "стать питомцем"
    assert "pet" not in COMMAND_KEY_DEFAULT_SOURCE_TRIGGER


@pytest.mark.parametrize("key", ["family_pet", "bepet", "pet"])
def test_family_pet_commands_are_write_locked(key: str) -> None:
    assert is_write_locked_command(key)


def _message() -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100, type="group", title="Chat"),
        from_user=SimpleNamespace(id=10, username="actor", first_name="Actor", last_name=None, is_bot=False),
        reply_to_message=None,
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_legacy_pet_command_hints_and_runs_old_action(monkeypatch: pytest.MonkeyPatch) -> None:
    send_request = AsyncMock()
    monkeypatch.setattr(chat_assistant, "_send_family_request", send_request)
    message = _message()
    settings = SimpleNamespace(family_tree_enabled=True)

    await chat_assistant.legacy_pet_command(message, CommandObject(command="pet", args="@user"), SimpleNamespace(), settings)

    assert "/bepet" in message.answer.await_args.args[0]
    send_request.assert_awaited_once()
    assert send_request.await_args.kwargs["relation_type"] == "pet"
    assert send_request.await_args.kwargs["raw_args"] == "@user"


def _load_migration():
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0078_family_pet_command_key.py"
    spec = importlib.util.spec_from_file_location("family_pet_command_key_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_migration_moves_rules_and_aliases_and_can_be_downgraded() -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE chat_command_access_rules (chat_id BIGINT, command_key VARCHAR(64), "
            "min_role_code VARCHAR(64), PRIMARY KEY (chat_id, command_key))"
        )
        connection.exec_driver_sql(
            "CREATE TABLE chat_text_aliases (id INTEGER PRIMARY KEY, chat_id BIGINT, command_key VARCHAR(64), "
            "alias_text_norm VARCHAR(128), source_trigger_norm VARCHAR(128))"
        )
        connection.exec_driver_sql(
            "INSERT INTO chat_command_access_rules VALUES "
            "(1, 'pet', 'admin'), (1, 'adopt', 'admin'), "
            # Collision: chat 2 already has a rule under the new key, it must win.
            "(2, 'pet', 'owner'), (2, 'family_pet', 'participant')"
        )
        connection.exec_driver_sql(
            "INSERT INTO chat_text_aliases VALUES "
            "(1, 1, 'pet', 'в питомцы', 'стать питомцем'), (2, 1, 'adopt', 'в дети', 'усыновить')"
        )
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        rules = connection.exec_driver_sql(
            "SELECT chat_id, command_key, min_role_code FROM chat_command_access_rules ORDER BY chat_id, command_key"
        ).all()
        assert rules == [(1, "adopt", "admin"), (1, "family_pet", "admin"), (2, "family_pet", "participant")]
        aliases = connection.exec_driver_sql("SELECT id, command_key FROM chat_text_aliases ORDER BY id").all()
        assert aliases == [(1, "family_pet"), (2, "adopt")]

        with Operations.context(context):
            migration.downgrade()

        rules = connection.exec_driver_sql(
            "SELECT chat_id, command_key, min_role_code FROM chat_command_access_rules ORDER BY chat_id, command_key"
        ).all()
        assert rules == [(1, "adopt", "admin"), (1, "pet", "admin"), (2, "pet", "participant")]
        aliases = connection.exec_driver_sql("SELECT id, command_key FROM chat_text_aliases ORDER BY id").all()
        assert aliases == [(1, "pet"), (2, "adopt")]
    engine.dispose()
