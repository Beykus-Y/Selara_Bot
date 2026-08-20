"""Drift guard for docs/bot_docs/roles.md against core/roles.py.

This file is served directly to the bot's own LLM assistant via the
read_bot_doc/list_bot_docs tools (src/selara/infrastructure/llm/tools.py) --
the assistant reads it to answer real admins' questions about who can do
what. A wrong permission claim here doesn't just mislead a doc reader, it
makes the bot itself give wrong answers. This test checks the doc's claims
against the actual SYSTEM_ROLE_TEMPLATES permission sets so it can't
silently drift again the way it did (junior_admin/senior_admin claims were
both wrong, and the co_owner tier was missing entirely -- found in a
2026-08-20 documentation audit).
"""

from __future__ import annotations

import re
from pathlib import Path

from selara.core.roles import PERM_MANAGE_ROLES, PERM_MODERATE_USERS, SYSTEM_ROLE_TEMPLATES

ROOT = Path(__file__).resolve().parents[2]
ROLES_DOC = (ROOT / "docs/bot_docs/roles.md").read_text(encoding="utf-8")

_MODERATION_ACTION_WORDS = ("варн", "пред", "бан", "рест")


def _role_section(markdown: str, role_code: str) -> str:
    """Text of the numbered role entry whose header contains `(role_code)`,
    up to the next numbered entry."""
    lines = markdown.splitlines()
    header_re = re.compile(r"^\d+\.\s")
    start = next(
        (i for i, line in enumerate(lines) if header_re.match(line) and f"({role_code})" in line),
        None,
    )
    assert start is not None, f"roles.md: no numbered entry for role_code {role_code!r}"
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if header_re.match(lines[i]) or lines[i].startswith("#"):
            end = i
            break
    return "\n".join(lines[start:end])


def test_roles_doc_lists_every_system_role_code() -> None:
    for template in SYSTEM_ROLE_TEMPLATES:
        assert f"({template.role_code})" in ROLES_DOC, (
            f"roles.md: role code {template.role_code!r} ({template.title_ru}) is missing entirely"
        )


def test_roles_doc_set_rank_enum_lists_every_role_code() -> None:
    section = _section_after(ROLES_DOC, "Допустимые значения:")
    for template in SYSTEM_ROLE_TEMPLATES:
        assert template.role_code in section, (
            f"roles.md: set_rank's documented `rank` enum is missing {template.role_code!r}"
        )


def test_roles_doc_does_not_claim_moderation_for_roles_without_moderate_users() -> None:
    """Only flag an affirmative "может ... <action>" claim -- a role's
    section may legitimately mention moderation words only to explicitly
    deny access to them (e.g. "не может выдавать варны")."""
    for template in SYSTEM_ROLE_TEMPLATES:
        if PERM_MODERATE_USERS in template.permissions:
            continue
        section = _role_section(ROLES_DOC, template.role_code).lower()
        for word in _MODERATION_ACTION_WORDS:
            affirmative_re = re.compile(rf"(?<!не )может\s+\w+[^.]*{word}")
            assert not affirmative_re.search(section), (
                f"roles.md: {template.role_code!r} does not have moderate_users by default, "
                f"but its section claims moderation capability (found {word!r})"
            )


def test_roles_doc_does_not_claim_set_rank_for_roles_without_manage_roles() -> None:
    """Bare 'set_rank' substring isn't enough -- a role's section may
    legitimately mention set_rank only to explicitly deny access to it
    (e.g. junior_admin's "не имеет доступа к set_rank"). Only flag an
    affirmative "может использовать ... set_rank" claim."""
    affirmative_re = re.compile(r"может\s+использовать[^.]*set_rank", re.IGNORECASE)
    for template in SYSTEM_ROLE_TEMPLATES:
        if PERM_MANAGE_ROLES in template.permissions:
            continue
        section = _role_section(ROLES_DOC, template.role_code)
        assert not affirmative_re.search(section), (
            f"roles.md: {template.role_code!r} does not have manage_roles by default, "
            f"but its section claims it can use set_rank"
        )


def _section_after(markdown: str, marker: str) -> str:
    idx = markdown.find(marker)
    assert idx != -1, f"roles.md: marker {marker!r} not found"
    return markdown[idx : idx + 200]
