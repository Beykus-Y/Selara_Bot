from __future__ import annotations

from dataclasses import dataclass

from gacha_service.domain.models import RARITY_LABELS, RARITY_SUMMARY_LABELS, CardRarity


@dataclass(frozen=True, slots=True)
class BannerProfile:
    """Per-banner wording and presentation rules.

    Every banner-specific label lives here, so adding a banner means adding one entry
    to `_PROFILES` instead of another `if banner == ...` branch in the service or API.
    """

    xp_label: str
    rank_label: str
    currency_label: str
    progression_reward_label: str
    duplicate_stage_prefix: str | None
    shows_origin: bool
    profile_heading: str | None
    profile_rank_label: str
    profile_currency_label: str
    rarity_labels: dict[CardRarity, str]
    rarity_summary_labels: dict[CardRarity, str]


_GENSHIN = BannerProfile(
    xp_label="Опыт приключений",
    rank_label="Ранг приключений",
    currency_label="Примогемы",
    progression_reward_label="созвездие",
    duplicate_stage_prefix="С",
    shows_origin=True,
    profile_heading="💠 Геншин",
    profile_rank_label="Ранг",
    profile_currency_label="Примогемы",
    rarity_labels=dict(RARITY_LABELS),
    rarity_summary_labels=dict(RARITY_SUMMARY_LABELS),
)

_HSR = BannerProfile(
    xp_label="Опыт освоения",
    rank_label="Уровень освоения",
    currency_label="Звездный нефрит",
    progression_reward_label="эйдолон",
    duplicate_stage_prefix="E",
    shows_origin=False,
    profile_heading="🌌 Honkai: Star Rail",
    profile_rank_label="Освоение",
    profile_currency_label="Нефрит",
    rarity_labels=dict(RARITY_LABELS),
    rarity_summary_labels=dict(RARITY_SUMMARY_LABELS),
)

_ZZZ = BannerProfile(
    xp_label="Кредиты Интернота",
    rank_label="Уровень Интернота",
    currency_label="Полихромы",
    progression_reward_label="кинолента",
    duplicate_stage_prefix="М",
    shows_origin=False,
    profile_heading="⚡ Zenless Zone Zero",
    profile_rank_label="Уровень",
    profile_currency_label="Полихромы",
    rarity_labels={
        **RARITY_LABELS,
        CardRarity.legendary: "🟨 Агент S-ранга",
        CardRarity.epic: "🟪 Агент A-ранга",
    },
    rarity_summary_labels={
        **RARITY_SUMMARY_LABELS,
        CardRarity.legendary: "Агентов S-ранга",
        CardRarity.epic: "Агентов A-ранга",
    },
)

_PROFILES: dict[str, BannerProfile] = {
    "genshin": _GENSHIN,
    "hsr": _HSR,
    "zzz": _ZZZ,
}


def get_banner_profile(banner: str) -> BannerProfile:
    # Unknown banners fall back to the Genshin wording, as the banner code path always did.
    return _PROFILES.get(banner, _GENSHIN)
