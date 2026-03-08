from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal


EconomyScopeType = Literal["global", "chat"]
EconomyMode = Literal["global", "local"]
LotteryTicketType = Literal["free", "paid"]
MarketListingStatus = Literal["open", "closed", "cancelled", "expired"]
AuctionStatus = Literal["open", "closed", "cancelled"]
ShopOfferCategory = Literal["seeds", "consumables", "upgrades"]


@dataclass(frozen=True)
class EconomyScope:
    scope_id: str
    scope_type: EconomyScopeType
    chat_id: int | None


@dataclass(frozen=True)
class EconomyAccount:
    id: int
    scope_id: str
    scope_type: EconomyScopeType
    chat_id: int | None
    user_id: int
    balance: int
    tap_streak: int
    last_tap_at: datetime | None
    daily_streak: int
    last_daily_claimed_at: datetime | None
    free_lottery_claimed_on: date | None
    paid_lottery_used_today: int
    paid_lottery_used_on: date | None
    sprinkler_level: int
    tap_glove_level: int
    storage_level: int
    growth_size_mm: int = 0
    growth_stress_pct: int = 0
    growth_actions: int = 0
    last_growth_at: datetime | None = None
    growth_boost_pct: int = 0
    growth_cooldown_discount_seconds: int = 0


@dataclass(frozen=True)
class FarmState:
    account_id: int
    farm_level: int
    size_tier: str
    negative_event_streak: int
    last_planted_crop_code: str | None = None


@dataclass(frozen=True)
class PlotState:
    id: int
    account_id: int
    plot_no: int
    crop_code: str | None
    planted_at: datetime | None
    ready_at: datetime | None
    yield_boost_pct: int
    shield_active: bool


@dataclass(frozen=True)
class InventoryItem:
    account_id: int
    item_code: str
    quantity: int


@dataclass(frozen=True)
class ShopOffer:
    offer_code: str
    title: str
    category: ShopOfferCategory
    item_code: str
    price: int
    quantity: int
    description: str


@dataclass(frozen=True)
class LotteryResult:
    accepted: bool
    reason: str | None
    ticket_type: LotteryTicketType | None
    coin_reward: int
    item_rewards: tuple[tuple[str, int], ...]
    used_paid_today: int


@dataclass(frozen=True)
class MarketListing:
    id: int
    scope_id: str
    scope_type: EconomyScopeType
    chat_id: int | None
    seller_user_id: int
    item_code: str
    qty_total: int
    qty_left: int
    unit_price: int
    fee_paid: int
    status: MarketListingStatus
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True)
class MarketTrade:
    id: int
    listing_id: int
    scope_id: str
    scope_type: EconomyScopeType
    chat_id: int | None
    seller_user_id: int
    buyer_user_id: int
    item_code: str
    quantity: int
    unit_price: int
    total_price: int
    created_at: datetime


@dataclass(frozen=True)
class ChatBoost:
    id: int
    chat_id: int
    scope_id: str
    scope_type: EconomyScopeType
    boost_code: str
    value_percent: int
    starts_at: datetime
    ends_at: datetime
    created_by_user_id: int
    created_at: datetime


@dataclass(frozen=True)
class ChatAuction:
    id: int
    chat_id: int
    scope_id: str
    scope_type: EconomyScopeType
    seller_user_id: int
    item_code: str
    quantity: int
    start_price: int
    current_bid: int
    highest_bid_user_id: int | None
    min_increment: int
    status: AuctionStatus
    message_id: int | None
    ends_at: datetime
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime | None


@dataclass(frozen=True)
class TransferResult:
    accepted: bool
    reason: str | None
    sender_balance: int | None
    receiver_balance: int | None
    tax_amount: int | None


@dataclass(frozen=True)
class GameRewardResult:
    game_kind: str
    rewarded_users: tuple[tuple[int, int], ...]
    total_distributed: int
