"""Built-in skills: instruction files the model reads on demand through ``read_skill``.

A skill is a ``skills/<name>/SKILL.md`` file shipped in the repository (never user-editable) with a small
front matter::

    ---
    name: artifacts
    version: 4
    description: One line telling the model when to use it.
    requires_tool: create_artifact
    ---
    (body)

``requires_tool`` hides the skill unless that tool is on for the request. A skill only describes how to use tools
that are already offered; it never grants one (every tool call is checked against the round's allowlist).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

MAX_BODY_CHARS = 8000
MAX_DESCRIPTION_CHARS = 200
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
_FRONT_MATTER_KEYS = frozenset({"name", "version", "description", "requires_tool"})


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    version: int
    description: str
    requires_tool: str
    body: str


def parse_skill(text: str, *, source: str = "skill") -> Skill:
    """Parse one SKILL.md; raises ``ValueError`` on a malformed header or an oversized body."""
    lines = text.lstrip("﻿").split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{source}: the file must start with a --- front matter")
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration:
        raise ValueError(f"{source}: the front matter is not closed") from None
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        key = key.strip()
        if not separator or key not in _FRONT_MATTER_KEYS or key in meta:
            raise ValueError(f"{source}: unexpected front matter line {line!r}")
        meta[key] = value.strip()
    missing = _FRONT_MATTER_KEYS - meta.keys()
    if missing:
        raise ValueError(f"{source}: missing front matter keys {sorted(missing)}")
    if not _NAME_RE.match(meta["name"]):
        raise ValueError(f"{source}: invalid skill name {meta['name']!r}")
    if not meta["version"].isdigit() or int(meta["version"]) < 1:
        raise ValueError(f"{source}: version must be a positive integer")
    if not meta["description"] or len(meta["description"]) > MAX_DESCRIPTION_CHARS:
        raise ValueError(f"{source}: description must be 1-{MAX_DESCRIPTION_CHARS} characters")
    if not re.match(r"^[a-z_]{1,40}$", meta["requires_tool"]):
        raise ValueError(f"{source}: invalid requires_tool")
    body = "\n".join(lines[end + 1 :]).strip()
    if not body or len(body) > MAX_BODY_CHARS:
        raise ValueError(f"{source}: the body must be 1-{MAX_BODY_CHARS} characters")
    return Skill(meta["name"], int(meta["version"]), meta["description"], meta["requires_tool"], body)


class SkillCatalog:
    def __init__(self, skills: dict[str, Skill]) -> None:
        self._skills = dict(skills)

    def get(self, name: object) -> Skill | None:
        return self._skills.get(name) if isinstance(name, str) else None

    def names(self) -> list[str]:
        return sorted(self._skills)

    def available(self, enabled_tools: frozenset[str] | set[str]) -> list[Skill]:
        """Skills whose required tool is on for this request, in a stable order."""
        return [skill for name, skill in sorted(self._skills.items()) if skill.requires_tool in enabled_tools]


@lru_cache(maxsize=1)
def load_catalog() -> SkillCatalog:
    """Read every bundled skill once (at first use) and validate its header."""
    skills: dict[str, Skill] = {}
    root = files("selara.infrastructure.llm").joinpath("skills")
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        path = entry.joinpath("SKILL.md")
        if not entry.is_dir() or not path.is_file():
            continue
        skill = parse_skill(path.read_text(encoding="utf-8"), source=f"skills/{entry.name}")
        if skill.name != entry.name:
            raise ValueError(f"skills/{entry.name}: name {skill.name!r} must match the directory")
        skills[skill.name] = skill
    return SkillCatalog(skills)
