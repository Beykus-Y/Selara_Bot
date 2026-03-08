from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date

from selara.domain.economy_entities import ShopOffer


@dataclass(frozen=True)
class CropDefinition:
    code: str
    title: str
    grow_seconds: int
    seed_cost: int
    min_yield: int
    max_yield: int
    sell_price: int


@dataclass(frozen=True)
class SizeTierDefinition:
    title: str
    price: int
    yield_mult: float
    time_mult: float


@dataclass(frozen=True)
class RecipeDefinition:
    code: str
    title: str
    ingredients: tuple[tuple[str, int], ...]
    result_item_code: str
    result_quantity: int
    description: str


FARM_LEVEL_PLOTS: dict[int, int] = {
    1: 2,
    2: 3,
    3: 4,
    4: 5,
    5: 6,
}

FARM_LEVEL_UPGRADE_COST: dict[int, int] = {
    2: 800,
    3: 2600,
    4: 7000,
    5: 16000,
}

SIZE_TIERS: dict[str, SizeTierDefinition] = {
    "small": SizeTierDefinition(title="Малый", price=0, yield_mult=1.0, time_mult=1.0),
    "medium": SizeTierDefinition(title="Средний", price=3000, yield_mult=1.55, time_mult=1.12),
    "large": SizeTierDefinition(title="Большой", price=9000, yield_mult=2.25, time_mult=1.28),
}

CROPS: dict[str, CropDefinition] = {
    "radish": CropDefinition(code="radish", title="Редис", grow_seconds=30 * 60, seed_cost=20, min_yield=3, max_yield=5, sell_price=9),
    "potato": CropDefinition(code="potato", title="Картофель", grow_seconds=60 * 60, seed_cost=40, min_yield=4, max_yield=7, sell_price=12),
    "wheat": CropDefinition(code="wheat", title="Пшеница", grow_seconds=2 * 60 * 60, seed_cost=85, min_yield=6, max_yield=10, sell_price=17),
    "corn": CropDefinition(code="corn", title="Кукуруза", grow_seconds=4 * 60 * 60, seed_cost=180, min_yield=8, max_yield=13, sell_price=29),
    "tomato": CropDefinition(code="tomato", title="Томат", grow_seconds=8 * 60 * 60, seed_cost=420, min_yield=10, max_yield=16, sell_price=52),
    "pumpkin": CropDefinition(code="pumpkin", title="Тыква", grow_seconds=12 * 60 * 60, seed_cost=700, min_yield=13, max_yield=20, sell_price=70),
    "blueberry": CropDefinition(code="blueberry", title="Черника", grow_seconds=16 * 60 * 60, seed_cost=1100, min_yield=16, max_yield=24, sell_price=90),
    "dragonfruit": CropDefinition(code="dragonfruit", title="Драгонфрут", grow_seconds=24 * 60 * 60, seed_cost=1850, min_yield=20, max_yield=30, sell_price=125),
}

CONSUMABLE_CATALOG: dict[str, tuple[str, int, str]] = {
    "fertilizer_fast": ("Удобрение: ускорение", 220, "Ускоряет рост выбранной грядки на 15%"),
    "fertilizer_rich": ("Удобрение: урожай", 260, "+25% к урожаю выбранной грядки"),
    "pesticide": ("Пестицид", 300, "Защищает от 1 негативного события"),
    "lottery_ticket": ("Лотерейный билет", 150, "Доп. попытка лотереи"),
    "market_fee_coupon": ("Купон комиссии", 180, "Один лот на рынке без комиссии 2%"),
    "crop_insurance": ("Страховка урожая", 320, "Сохраняет урожай при негативном событии"),
    "mystery_pack": ("Таинственный набор", 500, "Случайная награда (монеты или расходник)"),
    "energy_drink": ("Энергетик", 140, "Снижает стресс на 25% для механики роста"),
    "growth_gel": ("Гель роста", 240, "+40% к следующему приросту в механике роста"),
    "cooling_pack": ("Охлаждающий пакет", 190, "Снижает следующий кулдаун механики роста на 20 минут"),
    "stimulant_shot": ("Стимулятор", 320, "+70% к следующему приросту, но стресс сразу +10%"),
    "pizza": ("Пицца", 0, "Крафтовая еда: восстанавливает готовность механики роста"),
    "veggie_salad": ("Овощной салат", 0, "Крафтовая еда: снижает стресс на 15%"),
    "corn_chips": ("Кукурузные чипсы", 0, "Крафтовая еда: даёт +20% буста следующему росту"),
}

RECIPES: dict[str, RecipeDefinition] = {
    "pizza": RecipeDefinition(
        code="pizza",
        title="Пицца",
        ingredients=(("crop:wheat", 3), ("crop:tomato", 2)),
        result_item_code="item:pizza",
        result_quantity=1,
        description="Сбрасывает кулдаун роста после /use pizza.",
    ),
    "salad": RecipeDefinition(
        code="salad",
        title="Овощной салат",
        ingredients=(("crop:tomato", 2), ("crop:radish", 2)),
        result_item_code="item:veggie_salad",
        result_quantity=1,
        description="Мягко снижает стресс роста.",
    ),
    "chips": RecipeDefinition(
        code="chips",
        title="Кукурузные чипсы",
        ingredients=(("crop:corn", 3), ("crop:potato", 2)),
        result_item_code="item:corn_chips",
        result_quantity=1,
        description="Даёт небольшой буст следующему росту.",
    ),
}

UPGRADE_MAX_LEVEL: dict[str, int] = {
    "sprinkler": 3,
    "tap_glove": 3,
    "storage_rack": 4,
}

UPGRADE_TITLE: dict[str, str] = {
    "sprinkler": "Спринклер",
    "tap_glove": "Перчатка тапа",
    "storage_rack": "Складской стеллаж",
}

UPGRADE_NEXT_COST: dict[str, dict[int, int]] = {
    "sprinkler": {1: 1200, 2: 2600, 3: 5400},
    "tap_glove": {1: 1000, 2: 2400, 3: 5000},
    "storage_rack": {1: 900, 2: 1800, 3: 3600, 4: 6200},
}

INVENTORY_BASE_SLOTS = 20
INVENTORY_STORAGE_PER_LEVEL = 20

NEGATIVE_EVENT_REASON = "negative_event"
POSITIVE_EVENT_REASON = "positive_event"
_UPGRADE_OFFER_RE = re.compile(r"^upgrade_(sprinkler|tap_glove|storage_rack)_(\d+)$")


def inventory_stack_limit(storage_level: int) -> int:
    return INVENTORY_BASE_SLOTS + max(0, storage_level) * INVENTORY_STORAGE_PER_LEVEL


def get_crop(code: str) -> CropDefinition | None:
    return CROPS.get(code)


def get_size_tier(code: str) -> SizeTierDefinition | None:
    return SIZE_TIERS.get(code)


def get_plot_slots(farm_level: int) -> int:
    return FARM_LEVEL_PLOTS.get(farm_level, FARM_LEVEL_PLOTS[1])


def localize_scope(scope_id: str) -> str:
    if scope_id == "global":
        return "глобальный"
    if scope_id.startswith("chat:"):
        chat_tail = scope_id.removeprefix("chat:")
        return f"локальный (чат {chat_tail})"
    return scope_id


def localize_size_tier(size_tier: str) -> str:
    return {
        "small": "малый",
        "medium": "средний",
        "large": "большой",
    }.get(size_tier, size_tier)


def localize_crop_code(crop_code: str | None) -> str:
    if crop_code is None:
        return "—"
    crop = CROPS.get(crop_code)
    return crop.title if crop is not None else crop_code


def localize_item_code(item_code: str) -> str:
    normalized = item_code.strip().lower()

    if normalized.startswith("crop:"):
        crop = CROPS.get(normalized.removeprefix("crop:"))
        return f"Урожай: {crop.title}" if crop is not None else normalized

    if normalized.startswith("seed:"):
        crop = CROPS.get(normalized.removeprefix("seed:"))
        return f"Семена: {crop.title}" if crop is not None else normalized

    if normalized.startswith("item:"):
        short = normalized.removeprefix("item:")
        if short in CONSUMABLE_CATALOG:
            return CONSUMABLE_CATALOG[short][0]
        if short == "permanent_token":
            return "Постоянный токен"
        return normalized

    if normalized.startswith("upgrade:"):
        parts = normalized.split(":")
        if len(parts) == 3 and parts[1] in UPGRADE_TITLE and parts[2].isdigit():
            return f"{UPGRADE_TITLE[parts[1]]} ур.{int(parts[2])}"
        return normalized

    return normalized


def localize_offer_code(offer_code: str) -> str:
    normalized = offer_code.strip().lower()
    if normalized.startswith("seed_"):
        crop = CROPS.get(normalized.removeprefix("seed_"))
        return f"Семена: {crop.title}" if crop is not None else normalized

    if normalized in CONSUMABLE_CATALOG:
        return CONSUMABLE_CATALOG[normalized][0]

    if normalized == "upgrade_token":
        return "Постоянный токен"

    match = _UPGRADE_OFFER_RE.fullmatch(normalized)
    if match:
        code = match.group(1)
        level = int(match.group(2))
        title = UPGRADE_TITLE.get(code, code)
        return f"{title} ур.{level}"

    return normalized


def normalize_crop_input(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "редис": "radish",
        "картофель": "potato",
        "картошка": "potato",
        "пшеница": "wheat",
        "кукуруза": "corn",
        "томат": "tomato",
        "помидор": "tomato",
        "тыква": "pumpkin",
        "черника": "blueberry",
        "драгонфрут": "dragonfruit",
    }
    return aliases.get(normalized, normalized)


def build_daily_shop_offers(
    *,
    scope_id: str,
    account_user_id: int,
    current_day: date,
    sprinkler_level: int,
    tap_glove_level: int,
    storage_level: int,
) -> list[ShopOffer]:
    seed = f"{scope_id}:{account_user_id}:{current_day.isoformat()}"
    rng = random.Random(seed)

    one_time_pool: list[ShopOffer] = []
    for crop in CROPS.values():
        one_time_pool.append(
            ShopOffer(
                offer_code=f"seed_{crop.code}",
                title=f"Семена: {crop.title}",
                category="seeds",
                item_code=f"seed:{crop.code}",
                price=crop.seed_cost,
                quantity=1,
                description=f"Посадка {crop.title.lower()}",
            )
        )

    for code, (title, price, description) in CONSUMABLE_CATALOG.items():
        if price <= 0:
            continue
        one_time_pool.append(
            ShopOffer(
                offer_code=code,
                title=title,
                category="consumables",
                item_code=f"item:{code}",
                price=price,
                quantity=1,
                description=description,
            )
        )

    selected_one_time = rng.sample(one_time_pool, k=min(4, len(one_time_pool)))

    upgrades: list[ShopOffer] = []
    current_levels = {
        "sprinkler": sprinkler_level,
        "tap_glove": tap_glove_level,
        "storage_rack": storage_level,
    }
    for code in ["sprinkler", "tap_glove", "storage_rack"]:
        current_level = max(0, current_levels[code])
        max_level = UPGRADE_MAX_LEVEL[code]
        if current_level >= max_level:
            continue
        next_level = current_level + 1
        price = UPGRADE_NEXT_COST[code][next_level]
        upgrades.append(
            ShopOffer(
                offer_code=f"upgrade_{code}_{next_level}",
                title=f"{UPGRADE_TITLE[code]} ур.{next_level}",
                category="upgrades",
                item_code=f"upgrade:{code}:{next_level}",
                price=price,
                quantity=1,
                description=f"Улучшение {UPGRADE_TITLE[code]} до уровня {next_level}",
            )
        )

    if not upgrades:
        upgrades.append(
            ShopOffer(
                offer_code="upgrade_token",
                title="Постоянный токен",
                category="upgrades",
                item_code="item:permanent_token",
                price=1800,
                quantity=1,
                description="Трофейный токен для будущих обновлений",
            )
        )

    selected_upgrades = upgrades if len(upgrades) <= 2 else rng.sample(upgrades, k=2)
    offers = selected_one_time + selected_upgrades

    # Guarantee deterministic order by offer code for stable UI rendering.
    offers.sort(key=lambda item: item.offer_code)
    return offers
