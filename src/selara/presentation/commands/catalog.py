from dataclasses import dataclass

from selara.application.use_cases.economy.catalog import RECIPES
from selara.presentation.commands.aliases import EXACT_ALIASES
from selara.presentation.commands.normalizer import normalize_text_command

TextCommandKey = str

SOCIAL_TRIGGER_TO_COMMAND_KEY: dict[str, TextCommandKey] = {
    "шлепнуть": "social_slap",
    "шлёпнуть": "social_slap",
    "шлепни": "social_slap",
    "шлёпни": "social_slap",
    "сжечь": "social_burn",
    "сожги": "social_burn",
    "убить": "social_kill",
    "убей": "social_kill",
    "трахнуть": "social_fuck",
    "трахни": "social_fuck",
    "соблазнить": "social_seduce",
    "соблазни": "social_seduce",
    "засосать": "social_makeout",
    "засоси": "social_makeout",
    "провести ночь с": "social_night",
    "проведи ночь с": "social_night",
    "сесть на": "social_siton",
    "сядь на": "social_siton",
    "нагнуть": "social_bend",
    "нагни": "social_bend",
    "поставить на колени": "social_kneel",
    "поставь на колени": "social_kneel",
    "раздеть": "social_undress",
    "раздень": "social_undress",
    "выебать": "social_ravage",
    "выеби": "social_ravage",
    "ударить": "social_hit",
    "ударь": "social_hit",
    "вмазать": "social_whack",
    "вмажь": "social_whack",
    "въебать": "social_crack",
    "въеби": "social_crack",
    "отпиздить": "social_beatup",
    "отпизди": "social_beatup",
    "отмудохать": "social_maul",
    "отмудохай": "social_maul",
    "запинать": "social_stomp",
    "запинай": "social_stomp",
    "оттаскать": "social_manhandle",
    "оттаскай": "social_manhandle",
    "скрутить": "social_restrain",
    "скрути": "social_restrain",
    "швырнуть": "social_throw",
    "швырни": "social_throw",
    "приложить": "social_clobber",
    "приложи": "social_clobber",
    "припечатать": "social_stamp",
    "припечатай": "social_stamp",
    "впечатать": "social_stamp",
    "впечатай": "social_stamp",
    "протащить": "social_manhandle",
    "протащи": "social_manhandle",
    "настучать по голове": "social_headknock",
    "настучи по голове": "social_headknock",
    "прижать к стене": "social_wallpin",
    "прижми к стене": "social_wallpin",
    "схватить за шкирку": "social_scruff",
    "схвати за шкирку": "social_scruff",
    "выкинуть в окно": "social_windowthrow",
    "выкинь в окно": "social_windowthrow",
    "спустить с лестницы": "social_stairdump",
    "спусти с лестницы": "social_stairdump",
    "отправить в нокаут": "social_knockout",
    "отправь в нокаут": "social_knockout",
    "дать подзатыльник": "social_headknock",
    "дай подзатыльник": "social_headknock",
    "дать леща": "social_faceslap",
    "дай леща": "social_faceslap",
    "навалять": "social_wallop",
    "наваляй": "social_wallop",
    "вломить": "social_smash",
    "вломи": "social_smash",
    "размазать": "social_smear",
    "размажь": "social_smear",
    "разъебать": "social_wreck",
    "разъеби": "social_wreck",
    "разнести": "social_wreck",
    "разнеси": "social_wreck",
    "унизить": "social_humiliate",
    "унизь": "social_humiliate",
    "засмеять": "social_ridicule",
    "засмей": "social_ridicule",
    "захуесосить": "social_flame",
    "забуллить": "social_bully",
    "задоминировать": "social_dominate",
    "задоминируй": "social_dominate",
    "застроить": "social_bossaround",
    "застрой": "social_bossaround",
    "осадить": "social_shutdown",
    "осади": "social_shutdown",
    "заткнуть": "social_shutup",
    "заткни": "social_shutup",
    "послать нахуй": "social_fuckoff",
    "пошли нахуй": "social_fuckoff",
    "выгнать": "social_evict",
    "выгони": "social_evict",
    "помурлыкать": "social_purr",
    "помурлыкай": "social_purr",
    "потереться": "social_nuzzle",
    "потрись": "social_nuzzle",
    "поняшиться": "social_cutesy",
    "похныкать в плечо": "social_sobshoulder",
    "похныкай в плечо": "social_sobshoulder",
    "свернуться рядом": "social_curlup",
    "свернись рядом": "social_curlup",
    "засопеть": "social_snuffle",
    "засопи": "social_snuffle",
    "поурчать": "social_rumble",
    "поурчи": "social_rumble",
    "уткнуться": "social_nestle",
    "уткнись": "social_nestle",
    "подлезть": "social_sneakclose",
    "подлезь": "social_sneakclose",
    "приласкать": "social_caress",
    "приласкай": "social_caress",
    "залипнуть на": "social_stareat",
    "залипни на": "social_stareat",
    "обнять": "social_hug",
    "обними": "social_hug",
    "поцеловать": "social_kiss",
    "поцелуй": "social_kiss",
    "пожать руку": "social_handshake",
    "пожми руку": "social_handshake",
    "пожать лапу": "social_handshake",
    "пожми лапу": "social_handshake",
    "дать пять": "social_highfive",
    "дай пять": "social_highfive",
    "хайфайв": "social_highfive",
    "погладить": "social_pat",
    "погладь": "social_pat",
    "куснуть": "social_bite",
    "кусни": "social_bite",
    "кусь": "social_bite",
    "пнуть": "social_kick",
    "пни": "social_kick",
    "ущипнуть": "social_pinch",
    "ущипни": "social_pinch",
    "прижать": "social_squeeze",
    "прижми": "social_squeeze",
    "потискать": "social_squeeze",
    "потискай": "social_squeeze",
    "наступить": "social_step",
    "наступи": "social_step",
    "пощекотать": "social_tickle",
    "пощекочи": "social_tickle",
    "ткнуть": "social_poke",
    "ткни": "social_poke",
    "оттолкнуть": "social_push",
    "оттолкни": "social_push",
    "утешить": "social_comfort",
    "утешь": "social_comfort",
    "успокоить": "social_calm",
    "успокой": "social_calm",
    "защитить": "social_protect",
    "защити": "social_protect",
    "поднять на руки": "social_carry",
    "подними на руки": "social_carry",
    "взять на руки": "social_carry",
    "возьми на руки": "social_carry",
    "утащить": "social_drag",
    "утащи": "social_drag",
    "выпроводить": "social_shoo",
    "выпроводи": "social_shoo",
    "вышвернуть": "social_hurlout",
    "вышверни": "social_hurlout",
    "вышвырнуть": "social_hurlout",
    "вышвырни": "social_hurlout",
    "выставить за дверь": "social_shoo",
    "выстави за дверь": "social_shoo",
    "подмигнуть": "social_wink",
    "подмигни": "social_wink",
    "потанцевать": "social_dance",
    "потанцуй": "social_dance",
    "поклониться": "social_bow",
    "поклонись": "social_bow",
    "подбодрить": "social_cheer",
    "подбодри": "social_cheer",
    "угостить": "social_treat",
    "угости": "social_treat",
    "похвалить": "social_praise",
    "похвали": "social_praise",
    "поздравить": "social_congrats",
    "поздравь": "social_congrats",
    "укрыть": "social_wrap",
    "укрой": "social_wrap",
    "наругать": "social_scold",
    "наругай": "social_scold",
    "дать кулак": "social_fistbump",
    "дай кулак": "social_fistbump",
    "кулачок": "social_fistbump",
    "взять": "social_take",
    "возьми": "social_take",
    "поиметь": "social_have",
    "поимей": "social_have",
    "насадить": "social_impale",
    "насади": "social_impale",
    "зажать": "social_trap",
    "зажми": "social_trap",
    "завалить": "social_floor",
    "завали": "social_floor",
    "разложить": "social_spread",
    "разложи": "social_spread",
    "пустить по кругу": "social_gang",
    "пусти по кругу": "social_gang",
    "оттрахать": "social_banghard",
    "оттрахай": "social_banghard",
    "засадить": "social_shovein",
    "засади": "social_shovein",
    "отсосать": "social_suck",
    "отсоси": "social_suck",
    "минет": "social_suck",
    "сделать минет": "social_suck",
    "сделай минет": "social_suck",
}

SOCIAL_COMMAND_KEY_TO_ACTION: dict[TextCommandKey, str] = {
    "social_slap": "slap",
    "social_burn": "burn",
    "social_kill": "kill",
    "social_fuck": "fuck",
    "social_seduce": "seduce",
    "social_makeout": "makeout",
    "social_night": "night",
    "social_siton": "siton",
    "social_bend": "bend",
    "social_kneel": "kneel",
    "social_undress": "undress",
    "social_ravage": "ravage",
    "social_hit": "hit",
    "social_whack": "whack",
    "social_crack": "crack",
    "social_beatup": "beatup",
    "social_maul": "maul",
    "social_stomp": "stomp",
    "social_manhandle": "manhandle",
    "social_restrain": "restrain",
    "social_throw": "throw",
    "social_clobber": "clobber",
    "social_stamp": "stamp",
    "social_headknock": "headknock",
    "social_wallpin": "wallpin",
    "social_scruff": "scruff",
    "social_windowthrow": "windowthrow",
    "social_stairdump": "stairdump",
    "social_knockout": "knockout",
    "social_faceslap": "faceslap",
    "social_wallop": "wallop",
    "social_smash": "smash",
    "social_smear": "smear",
    "social_wreck": "wreck",
    "social_humiliate": "humiliate",
    "social_ridicule": "ridicule",
    "social_flame": "flame",
    "social_bully": "bully",
    "social_dominate": "dominate",
    "social_bossaround": "bossaround",
    "social_shutdown": "shutdown",
    "social_shutup": "shutup",
    "social_fuckoff": "fuckoff",
    "social_evict": "evict",
    "social_purr": "purr",
    "social_nuzzle": "nuzzle",
    "social_cutesy": "cutesy",
    "social_sobshoulder": "sobshoulder",
    "social_curlup": "curlup",
    "social_snuffle": "snuffle",
    "social_rumble": "rumble",
    "social_nestle": "nestle",
    "social_sneakclose": "sneakclose",
    "social_caress": "caress",
    "social_stareat": "stareat",
    "social_hug": "hug",
    "social_kiss": "kiss",
    "social_handshake": "handshake",
    "social_highfive": "highfive",
    "social_pat": "pat",
    "social_bite": "bite",
    "social_kick": "kick",
    "social_pinch": "pinch",
    "social_squeeze": "squeeze",
    "social_step": "step",
    "social_tickle": "tickle",
    "social_poke": "poke",
    "social_push": "push",
    "social_comfort": "comfort",
    "social_calm": "calm",
    "social_protect": "protect",
    "social_carry": "carry",
    "social_drag": "drag",
    "social_shoo": "shoo",
    "social_hurlout": "hurlout",
    "social_wink": "wink",
    "social_dance": "dance",
    "social_bow": "bow",
    "social_cheer": "cheer",
    "social_treat": "treat",
    "social_praise": "praise",
    "social_congrats": "congrats",
    "social_wrap": "wrap",
    "social_scold": "scold",
    "social_fistbump": "fistbump",
    "social_take": "take",
    "social_have": "have",
    "social_impale": "impale",
    "social_trap": "trap",
    "social_floor": "floor",
    "social_spread": "spread",
    "social_gang": "gang",
    "social_banghard": "banghard",
    "social_shovein": "shovein",
    "social_suck": "suck",
}

PREFIX_TRIGGER_TO_COMMAND_KEY: dict[str, TextCommandKey] = {
    "+антирейд": "antiraid_on",
    "-антирейд": "antiraid_off",
    "-чат": "chat_lock",
    "+чат": "chat_unlock",
    "нейминг": "naming",
    "объява": "announce",
    "игра": "game",
    "кто ты": "me",
    "актив": "active",
    "топ": "top",
    "баланс": "eco",
    "ферма": "farm",
    "магазин": "shop",
    "инвентарь": "inventory",
    "крафт": "craft",
    "лотерея": "lottery",
    "рынок": "market",
    "аукцион": "auction",
    "ставка": "bid",
    "рост": "growth",
    "перевод": "pay",
    "платеж": "pay",
    "когда был": "lastseen",
    "когда была": "lastseen",
    "отношения": "pair",
    "брак": "marry",
    "пара": "pair",
    "предложить встречаться": "pair",
    "жениться": "marry",
    "предложить брак": "marry",
    "роль": "role",
    "титул": "title",
    "усыновить": "adopt",
    "стать питомцем": "pet",
    "семья": "family",
    **SOCIAL_TRIGGER_TO_COMMAND_KEY,
}

EXACT_TRIGGER_TO_COMMAND_KEY: dict[str, TextCommandKey] = {
    normalize_text_command(trigger): command_key
    for trigger, command_key in EXACT_ALIASES.items()
}

BUILTIN_TRIGGER_TO_COMMAND_KEY: dict[str, TextCommandKey] = dict(EXACT_TRIGGER_TO_COMMAND_KEY)
BUILTIN_TRIGGER_TO_COMMAND_KEY.update(PREFIX_TRIGGER_TO_COMMAND_KEY)

COMMAND_KEY_DEFAULT_SOURCE_TRIGGER: dict[TextCommandKey, str] = {
    "start": "старт",
    "alive": "бот",
    "game": "игра",
    "role": "роль",
    "me": "кто я",
    "iris_perenos": "ирис перенос",
    "iriskto_perenos": "ирис кто перенос",
    "help": "помощь",
    "active": "актив",
    "announce_reg": "рег",
    "announce_unreg": "анрег",
    "rep": "репутация",
    "achievements": "достижения",
    "lastseen": "когда был",
    "inactive": "кто неактив",
    "eco": "баланс",
    "farm": "ферма",
    "shop": "магазин",
    "inventory": "инвентарь",
    "craft": "крафт",
    "tap": "тап",
    "daily": "дейлик",
    "gacha_pull": "гача генш",
    "gacha_profile": "моя гача генш",
    "gacha_info": "гача инфо",
    "gacha_skip": "гача скип генш",
    "lottery": "лотерея",
    "market": "рынок",
    "article": "моя статья",
    "auction": "аукцион",
    "bid": "ставка",
    "pay": "перевод",
    "zhmyh": "жмых",
    "quote": "цитировать",
    "growth": "рост",
    "growth_action": "дрочка",
    "relation": "отношения",
    "marriage": "мой брак",
    "marriages": "браки",
    "pair": "пара",
    "breakup": "расстаться",
    "marry": "жениться",
    "divorce": "развод",
    "love": "любовь",
    "care": "забота",
    "date": "свидание",
    "gift": "подарок",
    "support": "поддержка",
    "flirt": "флирт",
    "surprise": "сюрприз",
    "vow": "клятва",
    "title": "титул",
    "adopt": "усыновить",
    "pet": "стать питомцем",
    "family": "семья",
    "antiraid_on": "+антирейд",
    "antiraid_off": "-антирейд",
    "chat_lock": "-чат",
    "chat_unlock": "+чат",
    "shipperim": "шипперим",
    "naming": "нейминг",
    "announce": "объява",
    "top": "топ",
    "social_slap": "шлепнуть",
    "social_burn": "сжечь",
    "social_kill": "убить",
    "social_fuck": "трахнуть",
    "social_seduce": "соблазнить",
    "social_makeout": "засосать",
    "social_night": "провести ночь с",
    "social_siton": "сесть на",
    "social_bend": "нагнуть",
    "social_kneel": "поставить на колени",
    "social_undress": "раздеть",
    "social_ravage": "выебать",
    "social_hit": "ударить",
    "social_whack": "вмазать",
    "social_crack": "въебать",
    "social_beatup": "отпиздить",
    "social_maul": "отмудохать",
    "social_stomp": "запинать",
    "social_manhandle": "оттаскать",
    "social_restrain": "скрутить",
    "social_throw": "швырнуть",
    "social_clobber": "приложить",
    "social_stamp": "припечатать",
    "social_headknock": "настучать по голове",
    "social_wallpin": "прижать к стене",
    "social_scruff": "схватить за шкирку",
    "social_windowthrow": "выкинуть в окно",
    "social_stairdump": "спустить с лестницы",
    "social_knockout": "отправить в нокаут",
    "social_faceslap": "дать леща",
    "social_wallop": "навалять",
    "social_smash": "вломить",
    "social_smear": "размазать",
    "social_wreck": "разъебать",
    "social_humiliate": "унизить",
    "social_ridicule": "засмеять",
    "social_flame": "захуесосить",
    "social_bully": "забуллить",
    "social_dominate": "задоминировать",
    "social_bossaround": "застроить",
    "social_shutdown": "осадить",
    "social_shutup": "заткнуть",
    "social_fuckoff": "послать нахуй",
    "social_evict": "выгнать",
    "social_purr": "помурлыкать",
    "social_nuzzle": "потереться",
    "social_cutesy": "поняшиться",
    "social_sobshoulder": "похныкать в плечо",
    "social_curlup": "свернуться рядом",
    "social_snuffle": "засопеть",
    "social_rumble": "поурчать",
    "social_nestle": "уткнуться",
    "social_sneakclose": "подлезть",
    "social_caress": "приласкать",
    "social_stareat": "залипнуть на",
    "social_hug": "обнять",
    "social_kiss": "поцеловать",
    "social_handshake": "пожать руку",
    "social_highfive": "дать пять",
    "social_pat": "погладить",
    "social_bite": "куснуть",
    "social_kick": "пнуть",
    "social_pinch": "ущипнуть",
    "social_squeeze": "прижать",
    "social_step": "наступить",
    "social_tickle": "пощекотать",
    "social_poke": "ткнуть",
    "social_push": "оттолкнуть",
    "social_comfort": "утешить",
    "social_calm": "успокоить",
    "social_protect": "защитить",
    "social_carry": "поднять на руки",
    "social_drag": "утащить",
    "social_shoo": "выпроводить",
    "social_hurlout": "вышвернуть",
    "social_wink": "подмигнуть",
    "social_dance": "потанцевать",
    "social_bow": "поклониться",
    "social_cheer": "подбодрить",
    "social_treat": "угостить",
    "social_praise": "похвалить",
    "social_congrats": "поздравить",
    "social_wrap": "укрыть",
    "social_scold": "наругать",
    "social_fistbump": "дать кулак",
    "social_take": "взять",
    "social_have": "поиметь",
    "social_impale": "насадить",
    "social_trap": "зажать",
    "social_floor": "завалить",
    "social_spread": "разложить",
    "social_gang": "пустить по кругу",
    "social_banghard": "оттрахать",
    "social_shovein": "засадить",
    "social_suck": "отсосать",
}

COMMAND_KEYS_WITH_TAIL: set[TextCommandKey] = {
    "lastseen",
    "naming",
    "announce",
    "game",
    "me",
    "active",
    "top",
    "role",
    "eco",
    "farm",
    "shop",
    "inventory",
    "craft",
    "lottery",
    "market",
    "auction",
    "bid",
    "pay",
    "growth",
    "pair",
    "marry",
    "title",
    "adopt",
    "pet",
    "family",
    "antiraid_on",
    "social_slap",
    "social_burn",
    "social_kill",
    "social_fuck",
    "social_seduce",
    "social_makeout",
    "social_night",
    "social_siton",
    "social_bend",
    "social_kneel",
    "social_undress",
    "social_ravage",
    "social_hit",
    "social_whack",
    "social_crack",
    "social_beatup",
    "social_maul",
    "social_stomp",
    "social_manhandle",
    "social_restrain",
    "social_throw",
    "social_clobber",
    "social_stamp",
    "social_headknock",
    "social_wallpin",
    "social_scruff",
    "social_windowthrow",
    "social_stairdump",
    "social_knockout",
    "social_faceslap",
    "social_wallop",
    "social_smash",
    "social_smear",
    "social_wreck",
    "social_humiliate",
    "social_ridicule",
    "social_flame",
    "social_bully",
    "social_dominate",
    "social_bossaround",
    "social_shutdown",
    "social_shutup",
    "social_fuckoff",
    "social_evict",
    "social_purr",
    "social_nuzzle",
    "social_cutesy",
    "social_sobshoulder",
    "social_curlup",
    "social_snuffle",
    "social_rumble",
    "social_nestle",
    "social_sneakclose",
    "social_caress",
    "social_stareat",
    "social_hug",
    "social_kiss",
    "social_handshake",
    "social_highfive",
    "social_pat",
    "social_bite",
    "social_kick",
    "social_pinch",
    "social_squeeze",
    "social_step",
    "social_tickle",
    "social_poke",
    "social_push",
    "social_comfort",
    "social_calm",
    "social_protect",
    "social_carry",
    "social_drag",
    "social_shoo",
    "social_hurlout",
    "social_wink",
    "social_dance",
    "social_bow",
    "social_cheer",
    "social_treat",
    "social_praise",
    "social_congrats",
    "social_wrap",
    "social_scold",
    "social_fistbump",
    "social_take",
    "social_have",
    "social_impale",
    "social_trap",
    "social_floor",
    "social_spread",
    "social_gang",
    "social_banghard",
    "social_shovein",
    "social_suck",
}

_MODE_HINT_TOKENS = {"global", "local"}
_FARM_ACTION_TOKENS = {"plant", "plantall", "plant_all", "harvest", "harvestall", "harvest_all", "upfarm", "upsize", "sell"}
_LOTTERY_ACTION_TOKENS = {"free", "paid", "status", "view"}
_GROWTH_ACTION_TOKENS = {"do", "d", "act", "go", "дрочить", "подрочить"}
_TITLE_RESET_TOKENS = {"clear", "reset", "off"}
_TITLE_SET_TOKENS = {"buy", "set"}
_GAME_KIND_TAILS = {
    "spy",
    "шпион",
    "spygame",
    "mafia",
    "мафия",
    "dice",
    "кости",
    "кубик",
    "кубики",
    "number",
    "num",
    "число",
    "угадай",
    "угадай число",
    "quiz",
    "викторина",
    "вик",
    "bredovukha",
    "bred",
    "бредовуха",
    "whoami",
    "who_am_i",
    "ктоя",
    "кто я",
    "bunker",
    "бункер",
    "zlobcards",
    "злобные карты",
    "злобкарты",
}
_CRAFT_RECIPE_TAILS = {normalize_text_command(recipe_code) for recipe_code in RECIPES}


def _split_tail_tokens(tail_text: str) -> list[str]:
    return [token for token in normalize_text_command(tail_text).split(" ") if token]


def _strip_mode_hint(tokens: list[str]) -> list[str]:
    if tokens and tokens[0] in _MODE_HINT_TOKENS:
        return tokens[1:]
    return tokens


def _is_user_ref_token(token: str) -> bool:
    return bool(token) and (token.startswith("@") and len(token) > 1 or token.lstrip("-").isdigit())


def prefix_tail_is_valid(*, command_key: TextCommandKey, tail_text: str) -> bool:
    normalized_tail = normalize_text_command(tail_text)
    tokens = _split_tail_tokens(tail_text)
    if not tokens:
        return True

    if command_key == "active":
        return len(tokens) == 1 and tokens[0].isdigit()

    if command_key == "top":
        mode_aliases = {"карма", "karma", "актив", "activity", "гибрид", "mix", "hybrid"}
        period_aliases = {"час", "hour", "сутки", "день", "day", "неделя", "week", "месяц", "month"}
        rest = list(tokens)
        if rest and rest[0] in mode_aliases:
            rest = rest[1:]
        if rest and rest[0] in period_aliases:
            rest = rest[1:]
        return not rest or len(rest) == 1 and rest[0].isdigit()

    if command_key in {"announce", "naming"}:
        return True

    if command_key == "game":
        return normalized_tail in _GAME_KIND_TAILS

    if command_key == "me":
        return len(tokens) <= 1 and (len(tokens) == 0 or _is_user_ref_token(tokens[0]))

    if command_key == "eco":
        rest = _strip_mode_hint(tokens)
        return not rest or len(rest) == 1 and rest[0].isdigit()

    if command_key == "farm":
        rest = _strip_mode_hint(tokens)
        return not rest or rest[0] in _FARM_ACTION_TOKENS

    if command_key == "shop":
        rest = _strip_mode_hint(tokens)
        return not rest or rest[0] == "buy"

    if command_key == "inventory":
        rest = _strip_mode_hint(tokens)
        return not rest or rest[0] == "use"

    if command_key == "lottery":
        rest = _strip_mode_hint(tokens)
        return not rest or rest[0] in _LOTTERY_ACTION_TOKENS

    if command_key == "market":
        rest = _strip_mode_hint(tokens)
        if not rest:
            return True
        if rest[0] == "sell":
            return len(rest) == 4 and rest[2].isdigit() and rest[3].isdigit()
        if rest[0] == "buy":
            return len(rest) == 3 and rest[1].isdigit() and rest[2].isdigit()
        if rest[0] == "cancel":
            return len(rest) == 2 and rest[1].isdigit()
        return False

    if command_key == "auction":
        if tokens[0] == "cancel":
            return len(tokens) == 1
        if tokens[0] in {"start", "sell"}:
            if len(tokens) not in {4, 5}:
                return False
            if not tokens[2].isdigit() or not tokens[3].isdigit():
                return False
            return len(tokens) == 4 or tokens[4].isdigit()
        return False

    if command_key == "bid":
        return len(tokens) == 1 and tokens[0].isdigit()

    if command_key == "pay":
        rest = _strip_mode_hint(tokens)
        if len(rest) == 1:
            return rest[0].isdigit()
        return len(rest) == 2 and _is_user_ref_token(rest[0]) and rest[1].isdigit()

    if command_key == "craft":
        rest = _strip_mode_hint(tokens)
        return not rest or " ".join(rest) in _CRAFT_RECIPE_TAILS

    if command_key == "growth":
        rest = _strip_mode_hint(tokens)
        return not rest or len(rest) == 1 and rest[0] in _GROWTH_ACTION_TOKENS

    if command_key == "lastseen":
        return len(tokens) == 1 and _is_user_ref_token(tokens[0])

    if command_key in {"pair", "marry", "adopt", "pet", "family"}:
        return len(tokens) == 1 and _is_user_ref_token(tokens[0])

    if command_key == "title":
        action = tokens[0]
        if action in _TITLE_RESET_TOKENS:
            return len(tokens) == 1
        if action in _TITLE_SET_TOKENS:
            return len(tokens) >= 2
        return False

    if command_key == "antiraid_on":
        return len(tokens) <= 1 and (not tokens or tokens[0] in {"5", "10"})

    if command_key in {"antiraid_off", "chat_lock", "chat_unlock"}:
        return not tokens

    if command_key == "role":
        return False

    return True


@dataclass(frozen=True)
class BuiltinTextMatch:
    command_key: TextCommandKey
    matched_trigger_norm: str


def resolve_builtin_command_key(trigger_text: str) -> TextCommandKey | None:
    normalized = normalize_text_command(trigger_text)
    if not normalized:
        return None
    return BUILTIN_TRIGGER_TO_COMMAND_KEY.get(normalized)


def match_builtin_command(text: str) -> BuiltinTextMatch | None:
    normalized = normalize_text_command(text)
    if not normalized or normalized.startswith("/"):
        return None

    exact_key = EXACT_TRIGGER_TO_COMMAND_KEY.get(normalized)
    if exact_key is not None:
        return BuiltinTextMatch(command_key=exact_key, matched_trigger_norm=normalized)

    for trigger in sorted(PREFIX_TRIGGER_TO_COMMAND_KEY, key=len, reverse=True):
        if normalized == trigger or normalized.startswith(f"{trigger} "):
            command_key = PREFIX_TRIGGER_TO_COMMAND_KEY[trigger]
            if normalized != trigger:
                tail = normalized[len(trigger) :].strip()
                if not prefix_tail_is_valid(command_key=command_key, tail_text=tail):
                    continue
            return BuiltinTextMatch(
                command_key=command_key,
                matched_trigger_norm=trigger,
            )

    return None
