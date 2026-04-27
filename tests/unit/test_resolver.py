import pytest

from selara.presentation.commands.resolver import TextCommandResolutionError, resolve_text_command


def test_resolver_maps_who_am_i() -> None:
    intent = resolve_text_command("Кто я", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "me"


def test_resolver_maps_who_are_you_to_profile_lookup() -> None:
    intent = resolve_text_command("Кто ты", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "me"


def test_resolver_maps_who_are_you_with_username_args() -> None:
    intent = resolve_text_command("кто ты @alice", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "me"
    assert intent.args["raw_args"] == "@alice"


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


def test_resolver_maps_top_week_period_less_than_filter() -> None:
    intent = resolve_text_command("топ неделя <100", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "top"
    assert intent.args["mode"] == "activity"
    assert intent.args["period"] == "week"
    assert intent.args["limit"] == 50
    assert intent.args["activity_less_than"] == 100


def test_resolver_maps_top_period_less_than_filter_with_limit() -> None:
    intent = resolve_text_command("топ месяц <100 30", top_default=10, top_max=50)
    assert intent is not None
    assert intent.name == "top"
    assert intent.args["mode"] == "activity"
    assert intent.args["period"] == "month"
    assert intent.args["limit"] == 30
    assert intent.args["activity_less_than"] == 100


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


def test_resolver_maps_relationship_shortcuts_to_actions() -> None:
    pair_intent = resolve_text_command("отношения", top_default=10, top_max=50)
    marry_intent = resolve_text_command("брак", top_default=10, top_max=50)

    assert pair_intent is not None
    assert pair_intent.name == "pair"

    assert marry_intent is not None
    assert marry_intent.name == "marry"


def test_resolver_maps_relationship_shortcuts_with_user_refs() -> None:
    pair_intent = resolve_text_command("отношения @alice", top_default=10, top_max=50)
    marry_intent = resolve_text_command("брак @alice", top_default=10, top_max=50)

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


def test_resolver_maps_new_social_action_aliases_with_targets() -> None:
    beatup_intent = resolve_text_command("отпиздить @alice", top_default=10, top_max=50)
    hurlout_intent = resolve_text_command("вышвернуть @alice", top_default=10, top_max=50)
    hurlout_alt_intent = resolve_text_command("вышвырнуть @alice", top_default=10, top_max=50)
    stomp_intent = resolve_text_command("запинать @alice", top_default=10, top_max=50)
    headknock_intent = resolve_text_command("настучать по голове @alice", top_default=10, top_max=50)
    wallop_intent = resolve_text_command("навалять @alice", top_default=10, top_max=50)
    smash_intent = resolve_text_command("вломить @alice", top_default=10, top_max=50)
    purr_intent = resolve_text_command("помурлыкать @alice", top_default=10, top_max=50)
    kneel_intent = resolve_text_command("поставить на колени @alice", top_default=10, top_max=50)
    undress_intent = resolve_text_command("раздеть @alice", top_default=10, top_max=50)
    ravage_intent = resolve_text_command("выебать @alice", top_default=10, top_max=50)

    assert beatup_intent is not None
    assert beatup_intent.name == "social_beatup"
    assert beatup_intent.args["raw_args"] == "@alice"

    assert hurlout_intent is not None
    assert hurlout_intent.name == "social_hurlout"
    assert hurlout_intent.args["raw_args"] == "@alice"

    assert hurlout_alt_intent is not None
    assert hurlout_alt_intent.name == "social_hurlout"
    assert hurlout_alt_intent.args["raw_args"] == "@alice"

    assert stomp_intent is not None
    assert stomp_intent.name == "social_stomp"
    assert stomp_intent.args["raw_args"] == "@alice"

    assert headknock_intent is not None
    assert headknock_intent.name == "social_headknock"
    assert headknock_intent.args["raw_args"] == "@alice"

    assert wallop_intent is not None
    assert wallop_intent.name == "social_wallop"
    assert wallop_intent.args["raw_args"] == "@alice"

    assert smash_intent is not None
    assert smash_intent.name == "social_smash"
    assert smash_intent.args["raw_args"] == "@alice"

    assert purr_intent is not None
    assert purr_intent.name == "social_purr"
    assert purr_intent.args["raw_args"] == "@alice"

    assert kneel_intent is not None
    assert kneel_intent.name == "social_kneel"
    assert kneel_intent.args["raw_args"] == "@alice"

    assert undress_intent is not None
    assert undress_intent.name == "social_undress"
    assert undress_intent.args["raw_args"] == "@alice"

    assert ravage_intent is not None
    assert ravage_intent.name == "social_ravage"
    assert ravage_intent.args["raw_args"] == "@alice"


def test_resolver_maps_expanded_social_action_aliases_with_targets() -> None:
    alias_map = {
        "вмазать @alice": "social_whack",
        "въебать @alice": "social_crack",
        "отмудохать @alice": "social_maul",
        "оттаскать @alice": "social_manhandle",
        "скрутить @alice": "social_restrain",
        "швырнуть @alice": "social_throw",
        "приложить @alice": "social_clobber",
        "припечатать @alice": "social_stamp",
        "прижать к стене @alice": "social_wallpin",
        "схватить за шкирку @alice": "social_scruff",
        "выкинуть в окно @alice": "social_windowthrow",
        "спустить с лестницы @alice": "social_stairdump",
        "отправить в нокаут @alice": "social_knockout",
        "дать леща @alice": "social_faceslap",
        "размазать @alice": "social_smear",
        "разъебать @alice": "social_wreck",
        "унизить @alice": "social_humiliate",
        "засмеять @alice": "social_ridicule",
        "захуесосить @alice": "social_flame",
        "забуллить @alice": "social_bully",
        "задоминировать @alice": "social_dominate",
        "застроить @alice": "social_bossaround",
        "осадить @alice": "social_shutdown",
        "заткнуть @alice": "social_shutup",
        "послать нахуй @alice": "social_fuckoff",
        "выгнать @alice": "social_evict",
        "потереться @alice": "social_nuzzle",
        "поняшиться @alice": "social_cutesy",
        "похныкать в плечо @alice": "social_sobshoulder",
        "свернуться рядом @alice": "social_curlup",
        "засопеть @alice": "social_snuffle",
        "поурчать @alice": "social_rumble",
        "уткнуться @alice": "social_nestle",
        "подлезть @alice": "social_sneakclose",
        "приласкать @alice": "social_caress",
        "залипнуть на @alice": "social_stareat",
        "взять @alice": "social_take",
        "поиметь @alice": "social_have",
        "насадить @alice": "social_impale",
        "зажать @alice": "social_trap",
        "завалить @alice": "social_floor",
        "разложить @alice": "social_spread",
        "пустить по кругу @alice": "social_gang",
        "оттрахать @alice": "social_banghard",
        "засадить @alice": "social_shovein",
    }
    for text, expected in alias_map.items():
        intent = resolve_text_command(text, top_default=10, top_max=50)
        assert intent is not None
        assert intent.name == expected
        assert intent.args["raw_args"] == "@alice"


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
        "цитировать": "quote",
        "шипперим": "shipperim",
        "рост": "growth",
        "профиль": "growth",
        "дрочка": "growth_action",
        "подрочить": "growth_action",
        "отношения": "pair",
        "мои отношения": "relation",
        "брак": "marry",
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


def test_resolver_maps_gacha_info_command() -> None:
    intent = resolve_text_command("гача инфо", top_default=10, top_max=50)

    assert intent is not None
    assert intent.name == "gacha_info"


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


@pytest.mark.parametrize("text", ["гача", "моя гача", "гача завтра", "моя гача завтра", "гача инфо генш"])
def test_resolver_raises_for_incomplete_or_unknown_gacha_commands(text: str) -> None:
    with pytest.raises(TextCommandResolutionError, match="Формат: гача"):
        resolve_text_command(text, top_default=10, top_max=50)


def test_resolver_raises_for_invalid_gacha_skip_command() -> None:
    with pytest.raises(TextCommandResolutionError, match="Формат: гача скип"):
        resolve_text_command("гача скип генш alice", top_default=10, top_max=50)


def test_resolver_rejects_non_command_phrases() -> None:
    assert resolve_text_command("активность", top_default=10, top_max=50) is None
    assert resolve_text_command("кто я такой", top_default=10, top_max=50) is None
    assert resolve_text_command("кто ты такой", top_default=10, top_max=50) is None
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


@pytest.mark.parametrize(
    ("text", "expected_name", "expected_args"),
    [
        ("+антирейд", "antiraid_on", {}),
        ("+антирейд 5", "antiraid_on", {"raw_args": "5"}),
        ("+антирейд 10", "antiraid_on", {"raw_args": "10"}),
        ("-антирейд", "antiraid_off", {}),
        ("-чат", "chat_lock", {}),
        ("+чат", "chat_unlock", {}),
    ],
)
def test_resolver_maps_chat_gate_commands(
    text: str,
    expected_name: str,
    expected_args: dict[str, str],
) -> None:
    intent = resolve_text_command(text, top_default=10, top_max=50)

    assert intent is not None
    assert intent.name == expected_name
    assert intent.args == expected_args


def test_resolver_rejects_invalid_antiraid_window() -> None:
    with pytest.raises(TextCommandResolutionError, match=r"Формат команды: \+антирейд \[5\|10\]"):
        resolve_text_command("+антирейд 7", top_default=10, top_max=50)
