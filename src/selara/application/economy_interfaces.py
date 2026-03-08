from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from selara.domain.economy_entities import ChatAuction, EconomyAccount, EconomyScope, FarmState, InventoryItem, MarketListing, PlotState


class EconomyRepository(Protocol):
    async def set_private_chat_context(self, *, user_id: int, chat_id: int) -> None: ...

    async def get_private_chat_context(self, *, user_id: int) -> int | None: ...

    async def resolve_scope(
        self,
        *,
        mode: str,
        chat_id: int | None,
        user_id: int,
    ) -> tuple[EconomyScope | None, str | None]: ...

    async def get_or_create_account(
        self,
        *,
        scope: EconomyScope,
        user_id: int,
    ) -> tuple[EconomyAccount, FarmState]: ...

    async def get_account(
        self,
        *,
        scope: EconomyScope,
        user_id: int,
    ) -> EconomyAccount | None: ...

    async def list_plots(self, *, account_id: int) -> list[PlotState]: ...

    async def get_plot(self, *, account_id: int, plot_no: int) -> PlotState | None: ...

    async def upsert_plot(
        self,
        *,
        account_id: int,
        plot_no: int,
        crop_code: str | None,
        planted_at: datetime | None,
        ready_at: datetime | None,
        yield_boost_pct: int,
        shield_active: bool,
    ) -> PlotState: ...

    async def list_inventory(self, *, account_id: int) -> list[InventoryItem]: ...

    async def get_inventory_item(self, *, account_id: int, item_code: str) -> InventoryItem | None: ...

    async def add_inventory_item(self, *, account_id: int, item_code: str, delta: int) -> InventoryItem: ...

    async def add_balance(self, *, account_id: int, delta: int) -> int: ...

    async def update_tap_state(
        self,
        *,
        account_id: int,
        tap_streak: int,
        last_tap_at: datetime,
    ) -> None: ...

    async def update_daily_state(
        self,
        *,
        account_id: int,
        daily_streak: int,
        last_daily_claimed_at: datetime,
    ) -> None: ...

    async def mark_free_lottery_claimed(
        self,
        *,
        account_id: int,
        claimed_on: date,
    ) -> None: ...

    async def increment_paid_lottery_used(
        self,
        *,
        account_id: int,
        used_on: date,
    ) -> int: ...

    async def set_paid_lottery_used(
        self,
        *,
        account_id: int,
        used_on: date,
        used_count: int,
    ) -> None: ...

    async def update_farm_level(self, *, account_id: int, farm_level: int) -> None: ...

    async def update_farm_size_tier(self, *, account_id: int, size_tier: str) -> None: ...

    async def get_farm_state(self, *, account_id: int) -> FarmState | None: ...

    async def set_negative_event_streak(self, *, account_id: int, value: int) -> None: ...

    async def set_upgrade_level(self, *, account_id: int, upgrade_code: str, new_level: int) -> None: ...
    async def update_growth_state(
        self,
        *,
        account_id: int,
        growth_size_mm: int,
        growth_stress_pct: int,
        growth_actions: int,
        last_growth_at: datetime | None,
        growth_boost_pct: int,
        growth_cooldown_discount_seconds: int,
    ) -> None: ...

    async def create_market_listing(
        self,
        *,
        scope: EconomyScope,
        chat_id: int | None,
        seller_user_id: int,
        item_code: str,
        qty_total: int,
        unit_price: int,
        fee_paid: int,
        expires_at: datetime,
    ) -> MarketListing: ...

    async def list_market_open(self, *, scope: EconomyScope, limit: int = 20) -> list[MarketListing]: ...

    async def get_market_listing(self, *, listing_id: int) -> MarketListing | None: ...

    async def update_market_listing_qty_and_status(
        self,
        *,
        listing_id: int,
        qty_left: int,
        status: str,
    ) -> None: ...

    async def count_open_market_listings_for_seller(self, *, scope: EconomyScope, seller_user_id: int) -> int: ...

    async def touch_transfer_daily(
        self,
        *,
        account_id: int,
        limit_date: date,
        sent_delta: int,
        count_delta: int,
    ) -> tuple[int, int]: ...

    async def get_transfer_daily(self, *, account_id: int, limit_date: date) -> tuple[int, int]: ...

    async def add_ledger(
        self,
        *,
        account_id: int,
        direction: str,
        amount: int,
        reason: str,
        meta_json: str,
    ) -> None: ...

    async def create_chat_auction(
        self,
        *,
        chat_id: int,
        scope: EconomyScope,
        seller_user_id: int,
        item_code: str,
        quantity: int,
        start_price: int,
        min_increment: int,
        ends_at: datetime,
        message_id: int | None,
    ) -> ChatAuction: ...

    async def get_chat_auction(self, *, auction_id: int) -> ChatAuction | None: ...

    async def get_active_chat_auction(self, *, chat_id: int) -> ChatAuction | None: ...

    async def update_chat_auction_bid(
        self,
        *,
        auction_id: int,
        current_bid: int,
        highest_bid_user_id: int | None,
        message_id: int | None = None,
    ) -> ChatAuction | None: ...

    async def close_chat_auction(
        self,
        *,
        auction_id: int,
        status: str,
        closed_at: datetime,
        message_id: int | None = None,
    ) -> ChatAuction | None: ...
