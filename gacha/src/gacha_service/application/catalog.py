from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

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
    region_code: str | None = Field(default=None, min_length=1)
    element_code: str | None = Field(default=None, min_length=1)
    weight: float = Field(default=1, gt=0)


class BannerConfig(BaseModel):
    code: str
    title: str
    cooldown_seconds: int = Field(gt=0)
    cards: list[CardConfig]

    @model_validator(mode="after")
    def validate_banner_specific_card_fields(self) -> "BannerConfig":
        if self.code != "genshin":
            return self

        missing_fields: list[str] = []
        for card in self.cards:
            missing: list[str] = []
            if not card.region_code:
                missing.append("region_code")
            if not card.element_code:
                missing.append("element_code")
            if missing:
                missing_fields.append(f"{card.code}: {', '.join(missing)}")
        if missing_fields:
            details = "; ".join(missing_fields)
            raise ValueError(f"Для баннера '{self.code}' у карт обязательны region_code и element_code: {details}")
        return self


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
            region_code=card.region_code,
            element_code=card.element_code,
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
