from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.filters import CommandObject

from selara.domain.entities import ChatPersonaAssignment, UserSnapshot
from selara.presentation.handlers import economy as economy_module


def _message() -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100, type="group", title="Economy Chat"),
        from_user=SimpleNamespace(id=10, username="actor", first_name="Actor", last_name=None, is_bot=False),
        reply_to_message=None,
        message_id=500,
        answer=AsyncMock(return_value=SimpleNamespace(message_id=501)),
    )


@pytest.mark.asyncio
async def test_pay_command_supports_persona_target(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message()
    target = UserSnapshot(
        telegram_user_id=21,
        username="hutao_main",
        first_name="Hu",
        last_name="Tao",
        is_bot=False,
        chat_display_name="Ху Тао",
    )
    activity_repo = SimpleNamespace(
        find_chat_persona_owner=AsyncMock(return_value=None),
        list_chat_persona_assignments=AsyncMock(
            return_value=[
                ChatPersonaAssignment(
                    chat_id=-100,
                    user=target,
                    persona_label="Ху Тао",
                    persona_label_norm="ху тао",
                    granted_by_user_id=1,
                )
            ]
        ),
    )
    transfer_coins = AsyncMock(
        return_value=SimpleNamespace(
            accepted=True,
            amount=100,
            tax_amount=5,
            sender_balance=900,
        )
    )

    monkeypatch.setattr(economy_module, "transfer_coins", transfer_coins)
    monkeypatch.setattr(economy_module, "log_chat_action", AsyncMock())

    await economy_module.pay_command(
        message,
        CommandObject(prefix="/", command="pay", mention=None, args="Ху Тао 100"),
        bot=SimpleNamespace(),
        economy_repo=SimpleNamespace(),
        activity_repo=activity_repo,
        chat_settings=SimpleNamespace(
            economy_enabled=True,
            economy_mode="global",
            economy_transfer_daily_limit=5000,
            economy_transfer_tax_percent=5,
            cleanup_economy_commands=False,
        ),
    )

    transfer_coins.assert_awaited_once()
    assert transfer_coins.await_args.kwargs["receiver_user_id"] == 21
    assert transfer_coins.await_args.kwargs["amount"] == 100
