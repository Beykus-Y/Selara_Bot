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
    real_commands = _real_aiogram_commands("moderation.py", "aliases.py", "settings.py")
    assert real_commands, "sanity check: at least one Command() registration expected"

    for spec in commands_for_category("admin"):
        for syntax_entry in spec.syntax:
            base = _base_command_word(syntax_entry)
            assert base in real_commands, (
                f"{spec.key}: '{syntax_entry}' claims /{base}, but no matching "
                f"@router.message(Command(\"{base}\")) found in moderation.py/aliases.py/settings.py"
            )


def test_admin_commands_have_no_invented_natural_triggers() -> None:
    # Every admin-only command checked directly against catalog.py's trigger
    # maps was confirmed slash-only — verify that stays true rather than
    # silently claiming a natural-language form that doesn't exist.
    for spec in commands_for_category("admin"):
        assert spec.natural_triggers == ()
        assert spec.dispatch_kind == "slash"


def test_misc_public_utility_natural_triggers_exist_in_the_real_trigger_maps() -> None:
    all_real_triggers = set(EXACT_TRIGGER_TO_COMMAND_KEY) | set(PREFIX_TRIGGER_TO_COMMAND_KEY)
    for key in ("misc_lastseen", "misc_public_service_commands"):
        for trigger in get_command_spec(key).natural_triggers:
            assert trigger in all_real_triggers, f"{key}: natural trigger {trigger!r} not found"
