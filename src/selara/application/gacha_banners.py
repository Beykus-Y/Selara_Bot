from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GachaBannerProfile:
    """Bot-side wording for one gacha banner.

    Add a banner here instead of another ``if banner == ...`` branch in the handlers.
    The gacha service has its own profile table with the same banner codes.
    """

    code: str
    aliases: tuple[str, ...]
    label: str
    heading_label: str
    heading_icon: str
    currency_label: str
    currency_genitive: str
    currency_short_genitive: str
    rank_label: str
    info_rank_label: str
    info_currency_label: str
    heading_custom_emoji_key: str | None = None
    uses_custom_emojis: bool = False

    @property
    def free_pull_command(self) -> str:
        return f"гача {self.aliases[0]}"

    @property
    def collection_command(self) -> str:
        return f"моя гача {self.aliases[0]}"


_GENSHIN = GachaBannerProfile(
    code="genshin",
    aliases=("генш", "геншин"),
    label="Геншин",
    heading_label="Геншин",
    heading_icon="💠",
    heading_custom_emoji_key="primogem",
    currency_label="Примогемы",
    currency_genitive="примогемов",
    currency_short_genitive="примогемов",
    rank_label="Ранг приключений",
    info_rank_label="Ранг",
    info_currency_label="Примогемы",
    uses_custom_emojis=True,
)

_HSR = GachaBannerProfile(
    code="hsr",
    aliases=("хср",),
    label="HSR",
    heading_label="Honkai: Star Rail",
    heading_icon="🌌",
    currency_label="Звездный нефрит",
    currency_genitive="звездного нефрита",
    currency_short_genitive="нефрита",
    rank_label="Уровень освоения",
    info_rank_label="Освоение",
    info_currency_label="Нефрит",
)

_ZZZ = GachaBannerProfile(
    code="zzz",
    aliases=("ззз", "зенлес"),
    label="ZZZ",
    heading_label="Zenless Zone Zero",
    heading_icon="⚡",
    currency_label="Полихромы",
    currency_genitive="полихромов",
    currency_short_genitive="полихромов",
    rank_label="Уровень Интернота",
    info_rank_label="Уровень",
    info_currency_label="Полихромы",
)

GACHA_BANNER_PROFILES: tuple[GachaBannerProfile, ...] = (_GENSHIN, _HSR, _ZZZ)
GACHA_BANNER_CODES: tuple[str, ...] = tuple(profile.code for profile in GACHA_BANNER_PROFILES)
_PROFILES_BY_CODE: dict[str, GachaBannerProfile] = {profile.code: profile for profile in GACHA_BANNER_PROFILES}


def get_gacha_banner_profile(banner: str | None) -> GachaBannerProfile | None:
    return _PROFILES_BY_CODE.get((banner or "").strip().lower())


def gacha_banner_aliases() -> dict[str, str]:
    """Maps every text alias ("генш", "ззз", ...) to its banner code."""
    return {alias: profile.code for profile in GACHA_BANNER_PROFILES for alias in profile.aliases}
