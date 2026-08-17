"""Verifies command_catalog.py entries against the real dispatch code, not
just internal self-consistency — the whole point of this catalog is to stop
docs from silently drifting from what the bot actually does (see
docs/WEB_UI_MODERNIZATION_TODO.md's slash-command single-source plan).
"""

from __future__ import annotations

import re
from pathlib import Path

from selara.presentation.commands.catalog import (
    EXACT_TRIGGER_TO_COMMAND_KEY,
    PREFIX_TRIGGER_TO_COMMAND_KEY,
)
from selara.presentation.commands.command_catalog import (
    COMMAND_CATALOG,
    GAME_RULES_RU,
    commands_for_category,
    get_command_spec,
)

HANDLERS_DIR = Path(__file__).resolve().parents[2] / "src/selara/presentation/handlers"
_AIOGRAM_COMMAND_RE = re.compile(r'@router\.message\(Command\("([a-zA-Z0-9_]+)"')


def _real_aiogram_commands(*filenames: str) -> set[str]:
    names: set[str] = set()
    for filename in filenames:
        source = (HANDLERS_DIR / filename).read_text(encoding="utf-8")
        names.update(_AIOGRAM_COMMAND_RE.findall(source))
    return names


def _real_source_text(filename: str) -> str:
    return (HANDLERS_DIR / filename).read_text(encoding="utf-8")


def _base_command_word(syntax_entry: str) -> str:
    # "/farm plant <культура> [грядка]" -> "farm"
    first_token = syntax_entry.split()[0]
    assert first_token.startswith("/"), f"expected a leading slash in {syntax_entry!r}"
    return first_token[1:]


def test_catalog_keys_are_unique() -> None:
    keys = [spec.key for spec in COMMAND_CATALOG]
    assert len(keys) == len(set(keys))


def test_get_command_spec_and_commands_for_category_round_trip() -> None:
    for spec in COMMAND_CATALOG:
        assert get_command_spec(spec.key) is spec
        assert spec in commands_for_category(spec.category)


def test_economy_syntax_matches_real_aiogram_command_registrations() -> None:
    real_commands = _real_aiogram_commands("economy.py")
    assert real_commands, "sanity check: economy.py should have at least one Command() registration"

    for spec in commands_for_category("economy"):
        for syntax_entry in spec.syntax:
            base = _base_command_word(syntax_entry)
            assert base in real_commands, (
                f"{spec.key}: '{syntax_entry}' claims /{base}, but no "
                f"@router.message(Command(\"{base}\")) found in economy.py"
            )


def test_economy_natural_triggers_exist_in_the_real_trigger_maps() -> None:
    all_real_triggers = set(EXACT_TRIGGER_TO_COMMAND_KEY) | set(PREFIX_TRIGGER_TO_COMMAND_KEY)
    for spec in commands_for_category("economy"):
        for trigger in spec.natural_triggers:
            assert trigger in all_real_triggers, (
                f"{spec.key}: natural trigger {trigger!r} not found in catalog.py's trigger maps"
            )


def test_games_syntax_matches_real_aiogram_command_registrations() -> None:
    real_commands = _real_aiogram_commands("game/router.py")
    assert real_commands, "sanity check: game/router.py should have at least one Command() registration"

    for spec in commands_for_category("games"):
        for syntax_entry in spec.syntax:
            base = _base_command_word(syntax_entry)
            assert base in real_commands, (
                f"{spec.key}: '{syntax_entry}' claims /{base}, but no "
                f"@router.message(Command(\"{base}\")) found in game/router.py"
            )


def test_games_natural_triggers_exist_in_the_real_trigger_maps() -> None:
    all_real_triggers = set(EXACT_TRIGGER_TO_COMMAND_KEY) | set(PREFIX_TRIGGER_TO_COMMAND_KEY)
    for spec in commands_for_category("games"):
        for trigger in spec.natural_triggers:
            assert trigger in all_real_triggers, (
                f"{spec.key}: natural trigger {trigger!r} not found in catalog.py's trigger maps"
            )


def test_relationships_syntax_matches_real_aiogram_command_registrations() -> None:
    real_commands = _real_aiogram_commands("relationships.py")
    assert real_commands, "sanity check: relationships.py should have at least one Command() registration"

    for spec in commands_for_category("relationships"):
        for syntax_entry in spec.syntax:
            base = _base_command_word(syntax_entry)
            assert base in real_commands, (
                f"{spec.key}: '{syntax_entry}' claims /{base}, but no "
                f"@router.message(Command(\"{base}\")) found in relationships.py"
            )


def test_relationships_natural_triggers_exist_in_the_real_trigger_maps() -> None:
    all_real_triggers = set(EXACT_TRIGGER_TO_COMMAND_KEY) | set(PREFIX_TRIGGER_TO_COMMAND_KEY)
    for spec in commands_for_category("relationships"):
        for trigger in spec.natural_triggers:
            assert trigger in all_real_triggers, (
                f"{spec.key}: natural trigger {trigger!r} not found in catalog.py's trigger maps"
            )


def test_family_syntax_matches_real_aiogram_command_registrations() -> None:
    real_commands = _real_aiogram_commands("chat_assistant.py")
    assert real_commands, "sanity check: chat_assistant.py should have at least one Command() registration"

    for spec in commands_for_category("family"):
        for syntax_entry in spec.syntax:
            base = _base_command_word(syntax_entry)
            assert base in real_commands, (
                f"{spec.key}: '{syntax_entry}' claims /{base}, but no "
                f"@router.message(Command(\"{base}\")) found in chat_assistant.py"
            )


def test_family_natural_triggers_exist_in_the_real_trigger_maps() -> None:
    all_real_triggers = set(EXACT_TRIGGER_TO_COMMAND_KEY) | set(PREFIX_TRIGGER_TO_COMMAND_KEY)
    for spec in commands_for_category("family"):
        for trigger in spec.natural_triggers:
            assert trigger in all_real_triggers, (
                f"{spec.key}: natural trigger {trigger!r} not found in catalog.py's trigger maps"
            )


def test_daily_article_is_dispatched_via_the_documented_regex_path() -> None:
    handler_source = (HANDLERS_DIR / "text_commands.py").read_text(encoding="utf-8")
    assert "_is_daily_article_command" in handler_source
    assert "/article" in handler_source
    assert '"моя статья"' in handler_source
    assert '"статья"' in handler_source

    spec = get_command_spec("misc_daily_article")
    assert spec.syntax == ("/article",)
    # No aiogram Command() registration exists for it — confirms the note
    # that it bypasses that mechanism, so a future refactor that adds one
    # doesn't leave this catalog entry's notes stale.
    assert "article" not in _real_aiogram_commands("economy.py", "text_commands.py")


def test_misc_public_utility_syntax_matches_real_aiogram_command_registrations() -> None:
    real_commands = _real_aiogram_commands("help.py", "settings.py", "stats.py", "moderation.py")
    for spec in ("misc_lastseen", "misc_public_service_commands"):
        for syntax_entry in get_command_spec(spec).syntax:
            base = _base_command_word(syntax_entry)
            assert base in real_commands, f"{spec}: '{syntax_entry}' claims /{base}, not found"


def test_gacha_natural_triggers_use_real_banner_aliases() -> None:
    from selara.presentation.commands.resolver import _GACHA_BANNER_ALIASES

    spec = get_command_spec("misc_gacha")
    assert spec.syntax == (), "gacha has no aiogram/regex slash form — must not claim one"

    real_aliases = set(_GACHA_BANNER_ALIASES)
    for trigger in spec.natural_triggers:
        words = trigger.split()
        # every trigger is "гача <banner>" / "моя гача <banner>" / "гача скип <banner>",
        # except the banner-less "гача инфо"
        if trigger == "гача инфо":
            continue
        banner_word = words[-1]
        assert banner_word in real_aliases, f"{trigger!r} uses unknown banner alias {banner_word!r}"


def test_gacha_notes_match_real_gating_code() -> None:
    resolver_source = (
        Path(__file__).resolve().parents[2] / "src/selara/presentation/commands/resolver.py"
    ).read_text(encoding="utf-8")
    text_commands_source = (HANDLERS_DIR / "text_commands.py").read_text(encoding="utf-8")

    assert "_parse_gacha_command" in resolver_source
    assert "GACHA_CURRENCY_PER_COIN_RATE" in text_commands_source
    assert "_require_channel_subscription" in text_commands_source
    assert "gacha_enabled" in text_commands_source

    from selara.application.use_cases.gacha import GACHA_CURRENCY_PER_COIN_RATE

    assert GACHA_CURRENCY_PER_COIN_RATE == 10


def test_admin_syntax_matches_real_aiogram_command_registrations() -> None:
    real_commands = _real_aiogram_commands("moderation.py", "aliases.py", "settings.py", "chat_assistant.py")
    assert real_commands, "sanity check: at least one Command() registration expected"

    for spec in commands_for_category("admin"):
        for syntax_entry in spec.syntax:
            if not syntax_entry.startswith("/"):
                # A natural-language form (e.g. "научить ..."), verified
                # separately against catalog.py's regex/trigger sources by
                # test_admin_smart_triggers_and_rp_natural_forms_are_real.
                continue
            base = _base_command_word(syntax_entry)
            assert base in real_commands, (
                f"{spec.key}: '{syntax_entry}' claims /{base}, but no matching "
                f"@router.message(Command(\"{base}\")) found in moderation.py/aliases.py/settings.py/chat_assistant.py"
            )


def test_admin_role_alias_settings_commands_have_no_invented_natural_triggers() -> None:
    # The original admin-only slice (roles/aliases/settings management)
    # checked directly against catalog.py's trigger maps and confirmed
    # slash-only — verify that stays true. Scoped to just those keys, not
    # the whole "admin" category: admin_smart_triggers/admin_custom_rp_actions
    # were added later with real, verified natural-language forms
    # (научить/добавить_действие) and are intentionally excluded here.
    slash_only_keys = {
        spec.key
        for spec in commands_for_category("admin")
        if spec.key not in ("admin_smart_triggers", "admin_custom_rp_actions")
    }
    for spec in commands_for_category("admin"):
        if spec.key not in slash_only_keys:
            continue
        assert spec.natural_triggers == ()
        assert spec.dispatch_kind == "slash"


def test_admin_smart_triggers_and_rp_natural_forms_are_real() -> None:
    source = _real_source_text("text_commands.py")
    settrigger = get_command_spec("admin_smart_triggers")
    assert settrigger.dispatch_kind == "both"
    assert "научить" in settrigger.natural_triggers
    assert "_SMART_TRIGGER_LEARN_PATTERN" in source
    assert r"научить" in source

    rp = get_command_spec("admin_custom_rp_actions")
    assert rp.dispatch_kind == "both"
    assert "добавить_действие" in rp.natural_triggers
    assert "_CUSTOM_RP_ADD_PATTERN" in source
    assert "добавить_действие" in source


def test_misc_public_utility_natural_triggers_exist_in_the_real_trigger_maps() -> None:
    all_real_triggers = set(EXACT_TRIGGER_TO_COMMAND_KEY) | set(PREFIX_TRIGGER_TO_COMMAND_KEY)
    for key in ("misc_lastseen", "misc_public_service_commands"):
        for trigger in get_command_spec(key).natural_triggers:
            assert trigger in all_real_triggers, f"{key}: natural trigger {trigger!r} not found"


def test_game_rules_ru_covers_every_launchable_game_kind() -> None:
    from selara.presentation.game_state import GAME_LAUNCHABLE_KINDS

    for kind in GAME_LAUNCHABLE_KINDS:
        assert kind in GAME_RULES_RU, f"{kind}: no rules text in GAME_RULES_RU (help.py's per-game menu would 404 on it)"


def test_moderation_actions_match_the_real_tuple_and_regex_dispatch() -> None:
    source = _real_source_text("moderation.py")
    spec = get_command_spec("admin_moderation_actions")
    assert spec.dispatch_kind == "both"

    # The slash form is Command(*_SLASH_MODERATION_COMMANDS), a tuple
    # unpacking that the plain Command("x") regex used elsewhere in this
    # file cannot see — checked directly against the tuple's own literal
    # source instead.
    assert "_SLASH_MODERATION_COMMANDS" in source
    assert "Command(*_SLASH_MODERATION_COMMANDS)" in source
    tuple_line_start = source.index("_SLASH_MODERATION_COMMANDS")
    tuple_line = source[tuple_line_start : tuple_line_start + 200].splitlines()[0]
    for base in ("pred", "warn", "unwarn", "ban", "unban"):
        assert f'"{base}"' in tuple_line, f"{base!r} missing from _SLASH_MODERATION_COMMANDS: {tuple_line!r}"

    # The natural-language reply-word path is a separate regex, not a
    # catalog.py trigger-map entry.
    assert "_REPLY_MODERATION_PATTERN" in source
    for trigger in spec.natural_triggers:
        assert trigger in source, f"natural trigger {trigger!r} not found in moderation.py's regex pattern"


def test_stats_syntax_matches_real_aiogram_command_registrations() -> None:
    real_commands = _real_aiogram_commands("stats.py")
    assert real_commands, "sanity check: stats.py should have at least one Command() registration"

    for spec in commands_for_category("stats"):
        if spec.key == "stats_award_grant":
            continue  # /award is real but purely instructional; the actual grant path is natural-language only
        for syntax_entry in spec.syntax:
            if not syntax_entry.startswith("/"):
                continue
            base = _base_command_word(syntax_entry)
            assert base in real_commands, f"{spec.key}: '{syntax_entry}' claims /{base}, not found in stats.py"


def test_stats_natural_triggers_exist_in_the_real_trigger_maps() -> None:
    all_real_triggers = set(EXACT_TRIGGER_TO_COMMAND_KEY) | set(PREFIX_TRIGGER_TO_COMMAND_KEY)
    for spec in commands_for_category("stats"):
        if spec.key == "stats_award_grant":
            continue  # "наградить" is regex-dispatched, not a catalog.py trigger-map entry — verified separately below
        for trigger in spec.natural_triggers:
            assert trigger in all_real_triggers, f"{spec.key}: natural trigger {trigger!r} not found"


def test_award_grant_natural_trigger_matches_the_real_regex_dispatch() -> None:
    source = _real_source_text("text_commands.py")
    assert "_PROFILE_AWARD_PATTERN" in source
    spec = get_command_spec("stats_award_grant")
    assert spec.dispatch_kind == "both"
    assert "наградить" in spec.natural_triggers


def test_achsync_is_really_admin_gated_as_the_notes_claim() -> None:
    source = _real_source_text("stats.py")
    assert "_ensure_chat_admin" in source
    # Loosely confirm the gate sits inside achsync_command, not merely
    # somewhere else in the file.
    achsync_start = source.index("async def achsync_command")
    achsync_body = source[achsync_start : achsync_start + 600]
    assert "_ensure_chat_admin" in achsync_body


def test_award_grant_command_alone_does_not_actually_grant_anything() -> None:
    source = _real_source_text("stats.py")
    award_start = source.index('Command("award")')
    award_body = source[award_start : award_start + 400]
    # The real grant path is the natural-language "наградить" phrase in
    # text_commands.py, not this handler — confirm /award's own body has no
    # award-granting repo call, only usage/help text.
    assert "grant" not in award_body.lower() and "award(" not in award_body


def test_social_natural_language_triggers_are_real() -> None:
    engagement_source = _real_source_text("engagement.py")
    text_commands_source = _real_source_text("text_commands.py")
    moderation_source = _real_source_text("moderation.py")

    karma = get_command_spec("social_karma_reply")
    assert karma.dispatch_kind == "natural_language"
    assert re.search(r"\^\\s\*\[\+\-\]\\s\*\$", engagement_source) or "[+-]" in engagement_source

    quote = get_command_spec("social_quote_card")
    assert quote.dispatch_kind == "natural_language"
    assert '"цитировать"' in text_commands_source or "цитировать" in text_commands_source

    personas = get_command_spec("social_personas")
    assert personas.dispatch_kind == "natural_language"
    assert "_PERSONA_GRANT_PATTERN" in moderation_source
    assert "_PERSONA_CLEAR_PATTERN" in moderation_source
    assert "_PERSONA_LIST_PATTERN" in moderation_source

    announcements = get_command_spec("social_announcements")
    assert announcements.dispatch_kind == "natural_language"
    assert "_ANNOUNCE_PATTERN" in text_commands_source

    naming = get_command_spec("social_naming")
    assert naming.dispatch_kind == "both"
    assert "_NAMING_PATTERN" in text_commands_source
    assert "/naming" in text_commands_source
    # Confirms /naming is NOT a plain aiogram Command() registration — same
    # ad-hoc regex dispatch class as /article, not the mechanism most other
    # "slash" entries in this catalog use.
    assert "naming" not in _real_aiogram_commands("help.py", "text_commands.py", "engagement.py")
