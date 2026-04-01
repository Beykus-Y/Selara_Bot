from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.domain.entities import ChatRoleDefinition
from selara.presentation.auth import get_actor_role_definition, has_command_access


def _role(*, role_code: str, title_ru: str, rank: int) -> ChatRoleDefinition:
    return ChatRoleDefinition(
        chat_id=-100,
        role_code=role_code,
        title_ru=title_ru,
        rank=rank,
        permissions=(),
        is_system=True,
        template_key=role_code,
    )


class _FakeActivityRepo:
    def __init__(self, actor_role_code: str) -> None:
        self._roles = {
            "participant": _role(role_code="participant", title_ru="Участник", rank=0),
            "junior_admin": _role(role_code="junior_admin", title_ru="Мл. админ", rank=10),
            "senior_admin": _role(role_code="senior_admin", title_ru="Ст. админ", rank=20),
        }
        self._actor_role_code = actor_role_code

    async def get_effective_role_definition(self, *, chat_id: int, user_id: int) -> ChatRoleDefinition:
        _ = chat_id, user_id
        return self._roles[self._actor_role_code]

    async def get_command_access_rule(self, *, chat_id: int, command_key: str):
        _ = chat_id, command_key
        return None

    async def get_chat_role_definition(self, *, chat_id: int, role_code: str) -> ChatRoleDefinition | None:
        _ = chat_id
        return self._roles.get(role_code)


class _BootstrapRepo:
    def __init__(self) -> None:
        self.bootstrapped_user_id: int | None = None

    async def bootstrap_chat_owner_role(self, *, chat, user):
        _ = chat
        self.bootstrapped_user_id = user.telegram_user_id
        return "owner", True

    async def get_effective_role_definition(self, *, chat_id: int, user_id: int):
        _ = chat_id, user_id
        return None


async def test_inactive_command_requires_junior_admin_by_default() -> None:
    allowed, actor_role_code, required_role_code, bootstrapped = await has_command_access(
        _FakeActivityRepo("participant"),
        chat_id=-100,
        chat_type="group",
        chat_title="Test",
        user_id=1,
        username="user",
        first_name="User",
        last_name=None,
        is_bot=False,
        command_key="inactive",
        bootstrap_if_missing_owner=False,
    )

    assert allowed is False
    assert actor_role_code == "participant"
    assert required_role_code == "junior_admin"
    assert bootstrapped is False


async def test_inactive_command_allows_junior_admin() -> None:
    allowed, actor_role_code, required_role_code, bootstrapped = await has_command_access(
        _FakeActivityRepo("junior_admin"),
        chat_id=-100,
        chat_type="group",
        chat_title="Test",
        user_id=1,
        username="user",
        first_name="User",
        last_name=None,
        is_bot=False,
        command_key="inactive",
        bootstrap_if_missing_owner=False,
    )

    assert allowed is True
    assert actor_role_code == "junior_admin"
    assert required_role_code == "junior_admin"
    assert bootstrapped is False


@pytest.mark.parametrize("command_key", ["rest_grant", "rest_list", "rest_revoke"])
async def test_rest_commands_require_junior_admin_by_default(command_key: str) -> None:
    allowed, actor_role_code, required_role_code, bootstrapped = await has_command_access(
        _FakeActivityRepo("participant"),
        chat_id=-100,
        chat_type="group",
        chat_title="Test",
        user_id=1,
        username="user",
        first_name="User",
        last_name=None,
        is_bot=False,
        command_key=command_key,
        bootstrap_if_missing_owner=False,
    )

    assert allowed is False
    assert actor_role_code == "participant"
    assert required_role_code == "junior_admin"
    assert bootstrapped is False


@pytest.mark.parametrize("command_key", ["rest_grant", "rest_list", "rest_revoke"])
async def test_rest_commands_allow_junior_admin(command_key: str) -> None:
    allowed, actor_role_code, required_role_code, bootstrapped = await has_command_access(
        _FakeActivityRepo("junior_admin"),
        chat_id=-100,
        chat_type="group",
        chat_title="Test",
        user_id=1,
        username="user",
        first_name="User",
        last_name=None,
        is_bot=False,
        command_key=command_key,
        bootstrap_if_missing_owner=False,
    )

    assert allowed is True
    assert actor_role_code == "junior_admin"
    assert required_role_code == "junior_admin"
    assert bootstrapped is False


@pytest.mark.parametrize("command_key", ["antiraid_on", "antiraid_off", "chat_lock", "chat_unlock"])
async def test_chat_gate_commands_require_senior_admin_by_default(command_key: str) -> None:
    allowed, actor_role_code, required_role_code, bootstrapped = await has_command_access(
        _FakeActivityRepo("junior_admin"),
        chat_id=-100,
        chat_type="group",
        chat_title="Test",
        user_id=1,
        username="user",
        first_name="User",
        last_name=None,
        is_bot=False,
        command_key=command_key,
        bootstrap_if_missing_owner=False,
    )

    assert allowed is False
    assert actor_role_code == "junior_admin"
    assert required_role_code == "senior_admin"
    assert bootstrapped is False


@pytest.mark.parametrize("command_key", ["antiraid_on", "antiraid_off", "chat_lock", "chat_unlock"])
async def test_chat_gate_commands_allow_senior_admin_by_default(command_key: str) -> None:
    allowed, actor_role_code, required_role_code, bootstrapped = await has_command_access(
        _FakeActivityRepo("senior_admin"),
        chat_id=-100,
        chat_type="group",
        chat_title="Test",
        user_id=1,
        username="user",
        first_name="User",
        last_name=None,
        is_bot=False,
        command_key=command_key,
        bootstrap_if_missing_owner=False,
    )

    assert allowed is True
    assert actor_role_code == "senior_admin"
    assert required_role_code == "senior_admin"
    assert bootstrapped is False


async def test_get_actor_role_definition_bootstraps_actual_chat_creator_when_bot_is_available() -> None:
    repo = _BootstrapRepo()
    bot = SimpleNamespace(
        get_chat_administrators=AsyncMock(
            return_value=[
                SimpleNamespace(
                    status="creator",
                    user=SimpleNamespace(
                        id=77,
                        username="creator",
                        first_name="Chat",
                        last_name="Owner",
                        is_bot=False,
                    ),
                )
            ]
        )
    )

    definition, bootstrapped = await get_actor_role_definition(
        repo,
        chat_id=-100,
        chat_type="group",
        chat_title="Test",
        user_id=1,
        username="actor",
        first_name="Actor",
        last_name=None,
        is_bot=False,
        bootstrap_if_missing_owner=True,
        bot=bot,
    )

    assert definition is None
    assert bootstrapped is True
    assert repo.bootstrapped_user_id == 77
