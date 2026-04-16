import pytest

from selara.presentation.handlers.text_commands import (
    _build_social_action_replica_line,
    _extract_social_action,
    _extract_social_action_request,
    _extract_social_action_target_request,
)


def test_extract_social_action_basic_commands() -> None:
    assert _extract_social_action("шлепнуть") == "slap"
    assert _extract_social_action("сожги") == "burn"
    assert _extract_social_action("обнять") == "hug"
    assert _extract_social_action("поцеловать!") == "kiss"


def test_extract_social_action_supports_imperative_aliases() -> None:
    assert _extract_social_action("ударь") == "hit"
    assert _extract_social_action("отпизди") == "beatup"
    assert _extract_social_action("запинай") == "stomp"
    assert _extract_social_action("наваляй") == "wallop"
    assert _extract_social_action("вломи") == "smash"
    assert _extract_social_action("помурлыкай") == "purr"
    assert _extract_social_action("трахни") == "fuck"
    assert _extract_social_action("раздень") == "undress"
    assert _extract_social_action("выеби") == "ravage"
    assert _extract_social_action("соблазни") == "seduce"
    assert _extract_social_action("засоси") == "makeout"
    assert _extract_social_action("убей") == "kill"


def test_extract_social_action_supports_new_single_word_actions() -> None:
    assert _extract_social_action("погладь") == "pat"
    assert _extract_social_action("кусни") == "bite"
    assert _extract_social_action("пни") == "kick"
    assert _extract_social_action("ущипни") == "pinch"
    assert _extract_social_action("потискай") == "squeeze"
    assert _extract_social_action("наступи") == "step"
    assert _extract_social_action("пощекочи") == "tickle"
    assert _extract_social_action("ткни") == "poke"
    assert _extract_social_action("оттолкни") == "push"
    assert _extract_social_action("утешь") == "comfort"
    assert _extract_social_action("успокой") == "calm"
    assert _extract_social_action("защити") == "protect"
    assert _extract_social_action("утащи") == "drag"
    assert _extract_social_action("выпроводи") == "shoo"
    assert _extract_social_action("вышверни") == "hurlout"
    assert _extract_social_action("вышвырни") == "hurlout"
    assert _extract_social_action("помурлыкай") == "purr"
    assert _extract_social_action("подмигни") == "wink"
    assert _extract_social_action("потанцуй") == "dance"
    assert _extract_social_action("поклонись") == "bow"
    assert _extract_social_action("подбодри") == "cheer"
    assert _extract_social_action("угости") == "treat"
    assert _extract_social_action("похвали") == "praise"
    assert _extract_social_action("поздравь") == "congrats"
    assert _extract_social_action("укрой") == "wrap"
    assert _extract_social_action("наругай") == "scold"
    assert _extract_social_action("нагни") == "bend"
    assert _extract_social_action("отсоси") == "suck"
    assert _extract_social_action("минет") == "suck"


def test_extract_social_action_supports_new_multiword_actions() -> None:
    assert _extract_social_action("пожми руку") == "handshake"
    assert _extract_social_action("дай пять!") == "highfive"
    assert _extract_social_action("дай кулак") == "fistbump"
    assert _extract_social_action("проведи ночь с") == "night"
    assert _extract_social_action("сядь на") == "siton"
    assert _extract_social_action("поставь на колени") == "kneel"
    assert _extract_social_action("настучи по голове") == "headknock"
    assert _extract_social_action("подними на руки") == "carry"
    assert _extract_social_action("возьми на руки") == "carry"
    assert _extract_social_action("выстави за дверь") == "shoo"


def test_extract_social_action_supports_expanded_action_set() -> None:
    alias_map = {
        "вмажь": "whack",
        "въеби": "crack",
        "отмудохать": "maul",
        "оттаскай": "manhandle",
        "скрути": "restrain",
        "швырни": "throw",
        "приложи": "clobber",
        "припечатай": "stamp",
        "впечатай": "stamp",
        "протащи": "manhandle",
        "прижми к стене": "wallpin",
        "схвати за шкирку": "scruff",
        "выкинь в окно": "windowthrow",
        "спусти с лестницы": "stairdump",
        "отправь в нокаут": "knockout",
        "дай подзатыльник": "headknock",
        "дай леща": "faceslap",
        "размажь": "smear",
        "разъеби": "wreck",
        "разнеси": "wreck",
        "унизь": "humiliate",
        "засмей": "ridicule",
        "захуесосить": "flame",
        "забуллить": "bully",
        "задоминируй": "dominate",
        "застрой": "bossaround",
        "осади": "shutdown",
        "заткни": "shutup",
        "пошли нахуй": "fuckoff",
        "выгони": "evict",
        "потрись": "nuzzle",
        "поняшиться": "cutesy",
        "похныкай в плечо": "sobshoulder",
        "свернись рядом": "curlup",
        "засопи": "snuffle",
        "поурчи": "rumble",
        "уткнись": "nestle",
        "подлезь": "sneakclose",
        "приласкай": "caress",
        "залипни на": "stareat",
        "возьми": "take",
        "поимей": "have",
        "насади": "impale",
        "зажми": "trap",
        "завали": "floor",
        "разложи": "spread",
        "пусти по кругу": "gang",
        "оттрахай": "banghard",
        "засади": "shovein",
    }
    for trigger, expected in alias_map.items():
        assert _extract_social_action(trigger) == expected


def test_extract_social_action_ignores_unknown_or_slash() -> None:
    assert _extract_social_action("привет") is None
    assert _extract_social_action("/шлепнуть") is None


def test_extract_social_action_request_supports_multiline_replica() -> None:
    action_key, replica = _extract_social_action_request("Обнять\nБедолага ты наша")

    assert action_key == "hug"
    assert replica == "Бедолага ты наша"


def test_extract_social_action_request_supports_inline_replica_with_punctuation() -> None:
    action_key, replica = _extract_social_action_request("обнять! бедолага ты наша")

    assert action_key == "hug"
    assert replica == "бедолага ты наша"


def test_extract_social_action_target_request_supports_all_targets() -> None:
    action_key, mass_target, username_arg, replica = _extract_social_action_target_request("Обнять всех")

    assert action_key == "hug"
    assert mass_target is True
    assert username_arg is None
    assert replica is None


def test_extract_social_action_target_request_supports_all_targets_with_replica() -> None:
    action_key, mass_target, username_arg, replica = _extract_social_action_target_request("поцеловать всем\nвы лучшие")

    assert action_key == "kiss"
    assert mass_target is True
    assert username_arg is None
    assert replica == "вы лучшие"


def test_extract_social_action_target_request_supports_persona_target_with_replica() -> None:
    action_key, mass_target, target_arg, replica = _extract_social_action_target_request("Обнять Ху Тао\nслушаю")

    assert action_key == "hug"
    assert mass_target is False
    assert target_arg == "Ху Тао"
    assert replica == "слушаю"


def test_build_social_action_replica_line_uses_alternative_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "selara.presentation.handlers.text_commands.random.choice",
        lambda seq: seq[1],
    )

    assert _build_social_action_replica_line("привет") == "🗣 И добавил(а): «привет»"
