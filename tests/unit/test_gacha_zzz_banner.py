from selara.application.gacha_banners import (
    GACHA_BANNER_CODES,
    get_gacha_banner_profile,
    gacha_banner_aliases,
)
from selara.application.use_cases import gacha as gacha_use_case
from selara.presentation.commands.resolver import resolve_text_command
from selara.presentation.handlers import text_commands


def test_zzz_banner_is_registered_for_bot_and_miniapp() -> None:
    assert GACHA_BANNER_CODES == ("genshin", "hsr", "zzz")


def test_zzz_aliases_resolve_to_zzz_banner() -> None:
    aliases = gacha_banner_aliases()

    assert aliases["ззз"] == "zzz"
    assert aliases["зенлес"] == "zzz"
    assert aliases["генш"] == "genshin"
    assert aliases["хср"] == "hsr"


def test_zzz_text_commands_route_to_zzz_banner() -> None:
    pull = resolve_text_command("гача ззз", top_default=10, top_max=50)
    assert pull is not None
    assert pull.name == "gacha_pull"
    assert pull.args["banner"] == "zzz"

    profile = resolve_text_command("моя гача зенлес", top_default=10, top_max=50)
    assert profile is not None
    assert profile.name == "gacha_profile"
    assert profile.args["banner"] == "zzz"

    skip = resolve_text_command("гача скип ззз", top_default=10, top_max=50)
    assert skip is not None
    assert skip.name == "gacha_skip"
    assert skip.args["banner"] == "zzz"


def test_zzz_profile_wording_on_bot_side() -> None:
    profile = get_gacha_banner_profile("zzz")

    assert profile is not None
    assert profile.free_pull_command == "гача ззз"
    assert profile.collection_command == "моя гача ззз"
    assert profile.currency_label == "Полихромы"
    assert profile.rank_label == "Уровень Интернота"
    assert profile.uses_custom_emojis is False
    assert text_commands._gacha_currency_button_label("zzz").startswith("+")
    assert "полихромов за" in text_commands._gacha_currency_button_label("zzz")
    assert text_commands._gacha_rank_label("zzz") == "Уровень Интернота"
    assert text_commands._gacha_info_currency_label("zzz") == "Полихромы"
    assert gacha_use_case._gacha_currency_label("zzz") == "полихромов"


def test_zzz_info_heading_has_no_custom_emoji() -> None:
    heading = text_commands._gacha_info_banner_heading_html(banner="zzz", use_custom_emojis=True)

    assert heading == "⚡ <b>Zenless Zone Zero</b>"


def test_existing_banners_keep_their_wording() -> None:
    assert "нефрита за" in text_commands._gacha_currency_button_label("hsr")
    assert "примогемов за" in text_commands._gacha_currency_button_label("genshin")
    assert gacha_use_case._gacha_currency_label("hsr") == "звездного нефрита"
    assert gacha_use_case._gacha_currency_label("genshin") == "примогемов"
    assert text_commands._gacha_info_banner_heading_html(banner="hsr") == "🌌 <b>Honkai: Star Rail</b>"


def test_unknown_banner_falls_back_to_generic_labels() -> None:
    assert get_gacha_banner_profile("unknown") is None
    assert text_commands._gacha_info_banner_heading_html(banner="unknown") == "🎴 <b>unknown</b>"
    assert text_commands._gacha_currency_label("unknown") == "Примогемы"
