from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from selara.domain.economy_entities import ChatAuction, EconomyAccount, EconomyScope, FarmState, InventoryItem, MarketListing, PlotState, ShopOffer


@dataclass(frozen=True)
class EconomyDashboard:
    scope: EconomyScope
    account: EconomyAccount
    farm: FarmState
    plots: tuple[PlotState, ...]
    inventory: tuple[InventoryItem, ...]


@dataclass(frozen=True)
class TapResult:
    accepted: bool
    reason: str | None
    reward: int
    proc_x4: bool
    new_balance: int | None
    next_available_at: datetime | None
    tap_streak: int


@dataclass(frozen=True)
class DailyResult:
    accepted: bool
    reason: str | None
    reward: int
    streak: int
    new_balance: int | None
    granted_lottery_ticket: bool
    next_available_at: datetime | None


@dataclass(frozen=True)
class PlantResult:
    accepted: bool
    reason: str | None
    crop_code: str | None
    plot_no: int | None
    ready_at: datetime | None
    new_balance: int | None


@dataclass(frozen=True)
class HarvestResult:
    accepted: bool
    reason: str | None
    crop_code: str | None
    amount: int
    event: str | None


@dataclass(frozen=True)
class HarvestAllResult:
    accepted: bool
    reason: str | None
    harvested_plots: tuple[int, ...]
    total_amount: int
    crop_totals: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class PlantAllResult:
    accepted: bool
    reason: str | None
    crop_code: str | None
    planted_plots: tuple[int, ...]
    new_balance: int | None


@dataclass(frozen=True)
class BuyShopResult:
    accepted: bool
    reason: str | None
    offer: ShopOffer | None
    new_balance: int | None


@dataclass(frozen=True)
class UseItemResult:
    accepted: bool
    reason: str | None
    item_code: str | None
    details: str | None


@dataclass(frozen=True)
class LotteryResultView:
    accepted: bool
    reason: str | None
    ticket_type: str | None
    coin_reward: int
    item_rewards: tuple[tuple[str, int], ...]
    new_balance: int | None
    used_paid_today: int


@dataclass(frozen=True)
class MarketCreateResult:
    accepted: bool
    reason: str | None
    listing: MarketListing | None


@dataclass(frozen=True)
class MarketBuyResult:
    accepted: bool
    reason: str | None
    listing_id: int | None
    quantity: int
    total_cost: int
    buyer_balance: int | None


@dataclass(frozen=True)
class MarketCancelResult:
    accepted: bool
    reason: str | None
    listing_id: int | None


@dataclass(frozen=True)
class CraftResult:
    accepted: bool
    reason: str | None
    recipe_code: str | None
    crafted_item_code: str | None
    crafted_quantity: int


@dataclass(frozen=True)
class AuctionStartResult:
    accepted: bool
    reason: str | None
    auction: ChatAuction | None


@dataclass(frozen=True)
class AuctionBidResult:
    accepted: bool
    reason: str | None
    auction: ChatAuction | None


@dataclass(frozen=True)
class AuctionFinalizeResult:
    accepted: bool
    reason: str | None
    auction: ChatAuction | None
    winner_user_id: int | None


@dataclass(frozen=True)
class TransferCoinsResult:
    accepted: bool
    reason: str | None
    amount: int
    tax_amount: int
    sender_balance: int | None
    receiver_balance: int | None


@dataclass(frozen=True)
class GrowthProfileResult:
    accepted: bool
    reason: str | None
    size_mm: int
    stress_pct: int
    actions: int
    balance: int | None
    next_available_at: datetime | None
    cooldown_seconds: int


@dataclass(frozen=True)
class GrowthActionResult:
    accepted: bool
    reason: str | None
    size_delta_mm: int
    new_size_mm: int
    stress_delta_pct: int
    new_stress_pct: int
    reward: int
    new_balance: int | None
    cooldown_seconds: int
    next_available_at: datetime | None
    fumble: bool
