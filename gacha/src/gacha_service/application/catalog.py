from __future__ import annotations

import json
from functools import lru_cache

from pydantic import BaseModel, Field

from gacha_service.config import settings
from gacha_service.domain.models import CardRarity, GachaCard


class CardConfig(BaseModel):
    code: str
    name: str
    rarity: CardRarity
    points: int = Field(ge=0)
    primogems: int = Field(ge=0)
    adventure_xp: int = Field(ge=0)
    image_url: str
    weight: int = Field(default=1, ge=1)


class BannerConfig(BaseModel):
    code: str
    title: str
    cooldown_seconds: int = Field(gt=0)
    cards: list[CardConfig]


def _load_banner_file(path: Path) -> BannerConfig:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return BannerConfig.model_validate(payload)


@lru_cache(maxsize=1)
def _load_all_banners() -> dict[str, BannerConfig]:
    config_dir = settings.banners_dir
    if not config_dir.exists():
        raise RuntimeError(f"Каталог конфигов не найден: {config_dir}")

    banners: dict[str, BannerConfig] = {}
    for path in sorted(config_dir.glob("*.json")):
        config = _load_banner_file(path)
        banners[config.code] = config
    return banners


def get_banner_config(banner: str) -> BannerConfig:
    config = _load_all_banners().get(banner)
    if config is None:
        raise ValueError(f"Баннер '{banner}' не поддерживается")
    return config


def get_cards_for_banner(banner: str) -> tuple[GachaCard, ...]:
    config = get_banner_config(banner)
    return tuple(
        GachaCard(
            code=card.code,
            banner=config.code,
            name=card.name,
            rarity=card.rarity,
            points=card.points,
            primogems=card.primogems,
            adventure_xp=card.adventure_xp,
            image_url=card.image_url,
            weight=card.weight,
        )
        for card in config.cards
    )


def get_card_for_banner(banner: str, code: str) -> GachaCard:
    normalized_code = (code or "").strip().lower()
    if not normalized_code:
        raise ValueError("Код карты не указан.")

    for card in get_cards_for_banner(banner):
        if card.code.lower() == normalized_code:
            return card
    raise ValueError(f"Карта '{code}' не найдена в баннере '{banner}'.")
