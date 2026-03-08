from types import SimpleNamespace

import pytest

from selara.domain.entities import UserSnapshot
from selara.presentation.handlers.stats import _resolve_last_seen_command_target


class _FakeActivityRepo:
    async def find_chat_user_by_username(self, *, chat_id: int, username: str):
        if chat_id == -100 and username.lower() == "@known":
            return UserSnapshot(
                telegram_user_id=700,
                username="known",
                first_name="Known",
                last_name="User",
                is_bot=False,
            )
        return None

    async def get_user_snapshot(self, *, user_id: int):
        if user_id == 900:
            return UserSnapshot(
                telegram_user_id=900,
                username="id900",
                first_name="Id",
                last_name="Nine",
                is_bot=False,
            )
        return None


def _message(*, chat_type: str = "group", chat_id: int = -100, from_user_id: int = 111):
    from_user = SimpleNamespace(
        id=from_user_id,
        username="self",
        first_name="Self",
        last_name=None,
    )
    return SimpleNamespace(
        chat=SimpleNamespace(type=chat_type, id=chat_id),
        from_user=from_user,
        reply_to_message=None,
    )


@pytest.mark.asyncio
async def test_lastseen_target_defaults_to_self_without_args() -> None:
    message = _message()
    user_id, label, error = await _resolve_last_seen_command_target(
        message,
        command=SimpleNamespace(args=None),
        activity_repo=_FakeActivityRepo(),
    )
    assert error is None
    assert user_id == 111
    assert label == "@self"


@pytest.mark.asyncio
async def test_lastseen_target_resolves_username() -> None:
    message = _message()
    user_id, label, error = await _resolve_last_seen_command_target(
        message,
        command=SimpleNamespace(args="@known"),
        activity_repo=_FakeActivityRepo(),
    )
    assert error is None
    assert user_id == 700
    assert label == "Known User"


@pytest.mark.asyncio
async def test_lastseen_target_rejects_unknown_username() -> None:
    message = _message()
    user_id, label, error = await _resolve_last_seen_command_target(
        message,
        command=SimpleNamespace(args="@missing"),
        activity_repo=_FakeActivityRepo(),
    )
    assert user_id is None
    assert label is None
    assert error is not None


@pytest.mark.asyncio
async def test_lastseen_target_resolves_numeric_id() -> None:
    message = _message()
    user_id, label, error = await _resolve_last_seen_command_target(
        message,
        command=SimpleNamespace(args="900"),
        activity_repo=_FakeActivityRepo(),
    )
    assert error is None
    assert user_id == 900
    assert label == "@id900"
