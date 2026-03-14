import pytest

from selara.presentation.commands.resolver import TextCommandResolutionError, resolve_text_command


def test_resolver_maps_who_am_i() -> None:
    intent = resolve_text_command("Кто я", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "me"


def test_resolver_maps_active_default() -> None:
    intent = resolve_text_command("актив", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "active"
    assert intent.args["limit"] == 10


def test_resolver_maps_top_with_limit() -> None:
    intent = resolve_text_command("топ 15", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "top"
    assert intent.args["mode"] == "activity"
    assert intent.args["period"] == "all"
    assert intent.args["limit"] == 15


def test_resolver_maps_top_karma_mode() -> None:
    intent = resolve_text_command("топ карма 20", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "top"
    assert intent.args["mode"] == "karma"
    assert intent.args["limit"] == 20
    assert intent.args["period"] == "all"


def test_resolver_maps_top_week_period_default_activity_mode() -> None:
    intent = resolve_text_command("топ неделя", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "top"
    assert intent.args["mode"] == "activity"
    assert intent.args["period"] == "week"
    assert intent.args["limit"] == 10


def test_resolver_maps_top_month_period_with_limit() -> None:
    intent = resolve_text_command("топ месяц 12", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "top"
    assert intent.args["mode"] == "activity"
    assert intent.args["period"] == "month"
    assert intent.args["limit"] == 12


def test_resolver_period_top_forces_activity_mode_even_with_karma_token() -> None:
    intent = resolve_text_command("топ карма неделя 8", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "top"
    assert intent.args["mode"] == "activity"
    assert intent.args["period"] == "week"
    assert intent.args["limit"] == 8


def test_resolver_maps_last_seen_alias() -> None:
    intent = resolve_text_command("когда была", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "lastseen"


def test_resolver_maps_inactive_alias() -> None:
    intent = resolve_text_command("кто неактив", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "inactive"


def test_resolver_maps_rep_alias() -> None:
    intent = resolve_text_command("репутация", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "rep"


def test_resolver_maps_alive_alias() -> None:
    intent = resolve_text_command("бот", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "alive"


def test_resolver_maps_start_alias() -> None:
    intent = resolve_text_command("старт", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "start"


def test_resolver_maps_game_alias() -> None:
    intent = resolve_text_command("игра", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "game"


def test_resolver_maps_game_alias_with_args() -> None:
    intent = resolve_text_command("игра мафия", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "game"
    assert intent.args["raw_args"] == "мафия"


def test_resolver_maps_relationship_proposal_phrases_with_args() -> None:
    pair_intent = resolve_text_command("предложить встречаться @alice", top_default=10, top_max=50)
    marry_intent = resolve_text_command("предложить брак @alice", top_default=10, top_max=50)

    assert pair_intent is not None
    assert pair_intent.name == "pair"
    assert pair_intent.args["raw_args"] == "@alice"

    assert marry_intent is not None
    assert marry_intent.name == "marry"
    assert marry_intent.args["raw_args"] == "@alice"


def test_resolver_maps_announce_subscribe_aliases() -> None:
    intent_reg = resolve_text_command("рег", top_default=10, top_max=50)
    assert intent_reg is not None
    assert intent_reg.name == "announce_reg"

    intent_unreg = resolve_text_command("анрег", top_default=10, top_max=50)
    assert intent_unreg is not None
    assert intent_unreg.name == "announce_unreg"


def test_resolver_maps_economy_aliases() -> None:
    alias_map = {
        "роль": "role",
        "баланс": "eco",
        "ферма": "farm",
        "магазин": "shop",
        "инвентарь": "inventory",
        "тап": "tap",
        "дейлик": "daily",
        "лотерея": "lottery",
        "рынок": "market",
        "жмых": "zhmyh",
        "шипперим": "shipperim",
        "рост": "growth",
        "профиль": "growth",
        "дрочка": "growth_action",
        "подрочить": "growth_action",
        "мои отношения": "relation",
        "мой брак": "marriage",
        "браки": "marriages",
        "пара": "pair",
        "расстаться": "breakup",
        "забота": "care",
        "свидание": "date",
        "подарок": "gift",
        "поддержка": "support",
        "флирт": "flirt",
        "сюрприз": "surprise",
        "клятва": "vow",
    }
    for alias, expected in alias_map.items():
        intent = resolve_text_command(alias, top_default=10, top_max=50)
        assert intent is not None
        assert intent.name == expected


def test_resolver_maps_gacha_pull_commands() -> None:
    genshin_intent = resolve_text_command("гача генш", top_default=10, top_max=50)
    hsr_intent = resolve_text_command("гача хср", top_default=10, top_max=50)

    assert genshin_intent is not None
    assert genshin_intent.name == "gacha_pull"
    assert genshin_intent.args["banner"] == "genshin"

    assert hsr_intent is not None
    assert hsr_intent.name == "gacha_pull"
    assert hsr_intent.args["banner"] == "hsr"


def test_resolver_maps_gacha_profile_commands() -> None:
    intent = resolve_text_command("моя гача геншин", top_default=10, top_max=50)

    assert intent is not None
    assert intent.name == "gacha_profile"
    assert intent.args["banner"] == "genshin"


def test_resolver_maps_gacha_skip_commands() -> None:
    self_intent = resolve_text_command("гача скип генш", top_default=10, top_max=50)
    other_intent = resolve_text_command("гача скип хср @alice", top_default=10, top_max=50)

    assert self_intent is not None
    assert self_intent.name == "gacha_skip"
    assert self_intent.args["banner"] == "genshin"
    assert self_intent.args["target_username"] is None

    assert other_intent is not None
    assert other_intent.name == "gacha_skip"
    assert other_intent.args["banner"] == "hsr"
    assert other_intent.args["target_username"] == "@alice"


@pytest.mark.parametrize("text", ["гача", "моя гача", "гача завтра", "моя гача завтра"])
def test_resolver_raises_for_incomplete_or_unknown_gacha_commands(text: str) -> None:
    with pytest.raises(TextCommandResolutionError, match="Формат: гача"):
        resolve_text_command(text, top_default=10, top_max=50)


def test_resolver_raises_for_invalid_gacha_skip_command() -> None:
    with pytest.raises(TextCommandResolutionError, match="Формат: гача скип"):
        resolve_text_command("гача скип генш alice", top_default=10, top_max=50)


def test_resolver_rejects_non_command_phrases() -> None:
    assert resolve_text_command("активность", top_default=10, top_max=50) is None
    assert resolve_text_command("кто я такой", top_default=10, top_max=50) is None
    assert resolve_text_command("актив вернулся что ли", top_default=10, top_max=50) is None
    assert resolve_text_command("рынок сегодня шумный", top_default=10, top_max=50) is None


def test_resolver_raises_for_out_of_range_limit() -> None:
    with pytest.raises(TextCommandResolutionError):
        resolve_text_command("актив 99", top_default=10, top_max=50)


def test_resolver_ignores_invalid_active_tail_that_is_not_command_argument() -> None:
    assert resolve_text_command("актив abc", top_default=10, top_max=50) is None


def test_resolver_maps_market_structured_args() -> None:
    intent = resolve_text_command("рынок buy 15 2", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "market"
    assert intent.args["raw_args"] == "buy 15 2"
