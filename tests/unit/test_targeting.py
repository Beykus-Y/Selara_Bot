from types import SimpleNamespace

import pytest

from selara.domain.entities import ChatPersonaAssignment, UserSnapshot
from selara.presentation.targeting import persona_lookup_matches, resolve_chat_target_user


def _message() -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100, type="group"),
        reply_to_message=None,
    )


class _FakeActivityRepo:
    def __init__(self, assignments: list[ChatPersonaAssignment]) -> None:
        self._assignments = assignments

    async def get_chat_display_name(self, *, chat_id: int, user_id: int) -> str | None:
        return None

    async def get_user_snapshot(self, *, user_id: int):
        return None

    async def find_chat_user_by_username(self, *, chat_id: int, username: str):
        return None

    async def find_chat_persona_owner(self, *, chat_id: int, persona_label: str):
        return None

    async def list_chat_persona_assignments(self, *, chat_id: int) -> list[ChatPersonaAssignment]:
        assert chat_id == -100
        return self._assignments


def test_persona_lookup_matches_supports_controlled_name_inflections() -> None:
    assert persona_lookup_matches("Навию", "Навия")
    assert persona_lookup_matches("Дурина", "Дурин")
    assert not persona_lookup_matches("дурака", "Дурин")


@pytest.mark.asyncio
async def test_resolve_chat_target_user_supports_persona_label() -> None:
    target = UserSnapshot(
        telegram_user_id=77,
        username="navia_main",
        first_name="Navia",
        last_name=None,
        is_bot=False,
        chat_display_name="Навия",
    )
    repo = _FakeActivityRepo(
        [
            ChatPersonaAssignment(
                chat_id=-100,
                user=target,
                persona_label="Навия",
                persona_label_norm="навия",
                granted_by_user_id=1,
            )
        ]
    )

    resolved = await resolve_chat_target_user(
        _message(),
        repo,
        explicit_target="Навию",
        prefer_reply=False,
    )

    assert resolved == target
