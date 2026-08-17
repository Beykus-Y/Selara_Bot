"""Drift guard for docs/USER_GUIDE.md and docs/ADMIN_GUIDE.md against
command_catalog.py.

These two files are static, hand-authored markdown (not rendered by any
route — confirmed by grepping the whole src/ tree for "USER_GUIDE"/
"ADMIN_GUIDE", the only hits are this test and command_catalog.py's own
docstring). Their command/trigger reference content is woven through many
pedagogical sections rather than sitting in one block (see
docs/WEB_UI_MODERNIZATION_TODO.md's Этап 3 journal, 2026-08-17, for why a
single "generated below this marker" file split was rejected in favor of
this narrower audit approach), so this test checks that each reference
list's real command words are present in the doc, the same class of check
that already found the "20 missing RP-actions" and "2 missing stats
triggers" bugs elsewhere in this catalog effort — it does not try to
regenerate the files byte-for-byte.
"""

from __future__ import annotations

import re
from pathlib import Path

from selara.presentation.commands.command_catalog import get_command_spec

ROOT = Path(__file__).resolve().parents[2]
USER_GUIDE = (ROOT / "docs/USER_GUIDE.md").read_text(encoding="utf-8")
ADMIN_GUIDE = (ROOT / "docs/ADMIN_GUIDE.md").read_text(encoding="utf-8")


def _section(markdown: str, heading: str) -> str:
    """Text from a heading (e.g. "## 4.1 Команды") up to the next heading
    of the same or shallower level."""
    lines = markdown.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == heading), None)
    assert start is not None, f"heading {heading!r} not found"
    level = len(re.match(r"#+", heading).group())
    end = len(lines)
    for i in range(start + 1, len(lines)):
        match = re.match(r"(#+)\s", lines[i])
        if match and len(match.group(1)) <= level:
            end = i
            break
    return "\n".join(lines[start:end])


def _base_words(*keys: str) -> list[str]:
    seen: list[str] = []
    for key in keys:
        for entry in get_command_spec(key).syntax:
            base = entry.split()[0]
            if base.startswith("/") and base not in seen:
                seen.append(base)
    return seen


def _assert_all_present(text: str, words: list[str], *, context: str) -> None:
    missing = [word for word in words if word not in text]
    assert not missing, f"{context}: missing {missing} — real catalog data not reflected in the doc"


def test_user_guide_profile_commands_match_stats_catalog() -> None:
    section = _section(USER_GUIDE, "## 4.1 Команды")
    _assert_all_present(
        section,
        _base_words("stats_profile", "stats_leaderboards", "misc_lastseen", "stats_achievements"),
        context="USER_GUIDE.md 4.1",
    )


def test_user_guide_profile_natural_triggers_match_stats_catalog() -> None:
    section = _section(USER_GUIDE, "## 4.2 Текстовые аналоги")
    profile = get_command_spec("stats_profile")
    for trigger in profile.natural_triggers:
        assert trigger in section, f"USER_GUIDE.md 4.2: missing natural trigger {trigger!r}"


def test_user_guide_games_list_matches_launchable_kinds() -> None:
    from selara.presentation.game_state import GAME_DEFINITIONS, GAME_LAUNCHABLE_KINDS

    section = _section(USER_GUIDE, "## 5.2 Какие режимы поддерживаются")
    for kind in GAME_LAUNCHABLE_KINDS:
        assert kind in section, (
            f"USER_GUIDE.md 5.2: launchable game kind {kind!r} "
            f"({GAME_DEFINITIONS[kind].title}) not listed"
        )


def test_user_guide_economy_commands_match_catalog() -> None:
    section = _section(USER_GUIDE, "## 6.1 Основные команды")
    _assert_all_present(
        section,
        _base_words("economy_panel", "economy_farm", "economy_shop_inventory_craft", "economy_market_transfer_auction"),
        context="USER_GUIDE.md 6.1",
    )


def test_admin_guide_role_commands_match_catalog() -> None:
    section = _section(ADMIN_GUIDE, "### Основные команды ролей")
    _assert_all_present(
        section,
        _base_words("admin_role_definitions", "admin_role_assignment", "admin_role_custom"),
        context="ADMIN_GUIDE.md 5 (role commands)",
    )


def test_admin_guide_settings_commands_match_catalog() -> None:
    section = _section(ADMIN_GUIDE, "## 6. Настройки группы")
    _assert_all_present(
        section,
        _base_words("misc_public_service_commands")[1:2] + _base_words("admin_settings_tools", "admin_command_ranks")[:1],
        context="ADMIN_GUIDE.md 6 (settings commands)",
    )


def test_admin_guide_moderation_commands_and_triggers_match_catalog() -> None:
    section = _section(ADMIN_GUIDE, "## 7. Модерация")
    _assert_all_present(section, _base_words("admin_moderation_actions"), context="ADMIN_GUIDE.md 7 (moderation commands)")
    # moderation.py's _REPLY_MODERATION_PATTERN accepts several synonyms per
    # action (e.g. "снять бан" / "разбан" / "анбан" all lift a ban) — the doc
    # is free to pick any one, so this checks "at least one real synonym is
    # mentioned per action" rather than the catalog's specific primary
    # trigger string verbatim.
    synonym_groups = (
        ("снять пред", "разпред", "анпред"),
        ("снять варн", "разварн", "анварн"),
        ("снять бан", "разбан", "анбан"),
    )
    for group in synonym_groups:
        assert any(word in section for word in group), f"ADMIN_GUIDE.md 7: none of {group} mentioned"
    # Found while writing this test: "повысить"/"понизить" (role-step) are
    # listed here alongside pred/warn/ban triggers, but they're a distinct
    # feature — verify they're still real rather than assuming the doc's
    # grouping implies they belong to admin_moderation_actions.
    role_step = get_command_spec("admin_role_step")
    for trigger in role_step.natural_triggers:
        assert trigger in section, f"ADMIN_GUIDE.md 7: role-step trigger {trigger!r} not found"


def test_admin_guide_alias_commands_match_catalog() -> None:
    section = _section(ADMIN_GUIDE, "## 8. Кастомные алиасы текстовых команд")
    _assert_all_present(section, _base_words("admin_aliases"), context="ADMIN_GUIDE.md 8 (aliases)")


def test_admin_guide_announcements_match_catalog() -> None:
    section = _section(ADMIN_GUIDE, "## 9. Объявления")
    announcements = get_command_spec("social_announcements")
    for trigger in announcements.natural_triggers:
        assert trigger in section, f"ADMIN_GUIDE.md 9: announcement trigger {trigger!r} not found"


def test_admin_guide_smart_triggers_match_catalog() -> None:
    section = _section(ADMIN_GUIDE, "## 9.1. Смарт-триггеры и шаблонные переменные")
    _assert_all_present(section, _base_words("admin_smart_triggers"), context="ADMIN_GUIDE.md 9.1 (smart triggers)")
