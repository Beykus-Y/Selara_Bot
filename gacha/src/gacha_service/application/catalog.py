from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from gacha_service.domain.models import CardRarity, GachaCard


CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "banners"


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
    if not CONFIG_DIR.exists():
        raise RuntimeError(f"Каталог конфигов не найден: {CONFIG_DIR}")

    banners: dict[str, BannerConfig] = {}
    for path in sorted(CONFIG_DIR.glob("*.json")):
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
