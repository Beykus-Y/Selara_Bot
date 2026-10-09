"""GUX-12: group Gacha first-run coin payment context is never ambiguous."""
from __future__ import annotations

import importlib

import pytest

ui = importlib.import_module("selara.presentation.handlers.text_commands")


@pytest.mark.parametrize(
    "mode,chat_id,phrase,should_not_contain",
    [
        ("local", -100123, "монеты <b>этой группы</b>", "общий баланс"),
        ("global", -100123, "<b>общий баланс монет</b>", "этой группы"),
        ("global", None, "личный чат", "локальная экономика"),
    ],
)
def test_gacha_info_explains_actual_coin_wallet(
    mode: str, chat_id: int | None, phrase: str, should_not_contain: str,
) -> None:
    line = ui._gacha_coin_payment_context(economy_mode=mode, chat_id=chat_id)
    assert phrase in line
    assert should_not_contain not in line


def test_gacha_economy_resolution_uses_group_settings_not_global_fallback() -> None:
    from types import SimpleNamespace

    for mode in ("global", "local"):
        settings = SimpleNamespace(economy_mode=mode)
        assert ui._gacha_economy_mode(chat_type="supergroup", chat_settings=settings) == mode
        assert ui._gacha_economy_chat_id(chat_type="supergroup", chat_id=-1001) == -1001
        assert ui._gacha_economy_mode(chat_type="private", chat_settings=settings) == "global"
        assert ui._gacha_economy_chat_id(chat_type="private", chat_id=123) is None
