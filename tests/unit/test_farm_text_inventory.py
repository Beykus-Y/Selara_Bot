from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from selara.application.use_cases.economy.catalog import localize_item_code
from selara.domain.economy_entities import EconomyAccount, FarmState, InventoryItem, PlotState
from selara.presentation.handlers.economy import _build_inventory_keyboard, _farm_text


def test_farm_text_includes_inventory_sections_and_hides_zero_qty() -> None:
    now = datetime.now(timezone.utc)
    dashboard = SimpleNamespace(
        account=EconomyAccount(
            id=1,
            scope_id="chat:-100",
            scope_type="chat",
            chat_id=-100,
            user_id=10,
            balance=1234,
            tap_streak=1,
            last_tap_at=None,
            daily_streak=1,
            last_daily_claimed_at=None,
            free_lottery_claimed_on=None,
            paid_lottery_used_today=0,
            paid_lottery_used_on=None,
            sprinkler_level=0,
            tap_glove_level=0,
            storage_level=0,
        ),
        farm=FarmState(account_id=1, farm_level=1, size_tier="small", negative_event_streak=0),
        plots=(
            PlotState(
                id=1,
                account_id=1,
                plot_no=1,
                crop_code="radish",
                planted_at=now,
                ready_at=now + timedelta(minutes=10),
                yield_boost_pct=0,
                shield_active=False,
            ),
        ),
        inventory=(
            InventoryItem(account_id=1, item_code="crop:radish", quantity=7),
            InventoryItem(account_id=1, item_code="seed:wheat", quantity=3),
            InventoryItem(account_id=1, item_code="item:energy_drink", quantity=2),
            InventoryItem(account_id=1, item_code="item:pesticide", quantity=0),
        ),
    )

    text = _farm_text(dashboard)
    assert "<b>Ферма</b>" in text
    assert "Грядки:" in text
    assert "Остатки на складе:" in text
    assert "Быстро:" not in text
    assert "⏳" in text
    assert localize_item_code("crop:radish") in text
    assert localize_item_code("seed:wheat") in text
    assert localize_item_code("item:energy_drink") in text
    assert localize_item_code("item:pesticide") not in text


def test_farm_text_marks_ready_plots_in_human_readable_form() -> None:
    now = datetime.now(timezone.utc)
    dashboard = SimpleNamespace(
        account=EconomyAccount(
            id=1,
            scope_id="chat:-100",
            scope_type="chat",
            chat_id=-100,
            user_id=10,
            balance=620,
            tap_streak=1,
            last_tap_at=None,
            daily_streak=1,
            last_daily_claimed_at=None,
            free_lottery_claimed_on=None,
            paid_lottery_used_today=0,
            paid_lottery_used_on=None,
            sprinkler_level=0,
            tap_glove_level=0,
            storage_level=0,
        ),
        farm=FarmState(account_id=1, farm_level=1, size_tier="small", negative_event_streak=0),
        plots=(
            PlotState(
                id=1,
                account_id=1,
                plot_no=1,
                crop_code="radish",
                planted_at=now - timedelta(minutes=40),
                ready_at=now - timedelta(minutes=5),
                yield_boost_pct=0,
                shield_active=False,
            ),
        ),
        inventory=(),
    )

    text = _farm_text(dashboard)
    assert "Редис ✅ готово к сбору" in text


def test_inventory_keyboard_paginates_items_and_keeps_page_in_callbacks() -> None:
    dashboard = SimpleNamespace(
        inventory=tuple(
            InventoryItem(account_id=1, item_code=f"item:test_{idx:02d}", quantity=1)
            for idx in range(1, 13)
        )
    )

    page0 = _build_inventory_keyboard("global", dashboard, owner_user_id=None, page=0)
    page1 = _build_inventory_keyboard("global", dashboard, owner_user_id=None, page=1)

    callbacks_page0 = [button.callback_data for row in page0.inline_keyboard for button in row if button.callback_data]
    callbacks_page1 = [button.callback_data for row in page1.inline_keyboard for button in row if button.callback_data]

    assert "inv:u:g:test_01:0:0" in callbacks_page0
    assert "inv:u:g:test_05:0:0" in callbacks_page0
    assert "inv:u:g:test_06:0:1" in callbacks_page1
    assert "inv:u:g:test_10:0:1" in callbacks_page1
    assert "inv:ov:g:2" in callbacks_page0
    assert "inv:ov:g:1" in callbacks_page0
    assert "inv:ov:g:0" in callbacks_page0
