from __future__ import annotations

import pytest

from selara.infrastructure.llm.skill_catalog import MAX_BODY_CHARS, SkillCatalog, load_catalog, parse_skill

GOOD = """---
name: demo
version: 2
description: Demo skill.
requires_tool: create_artifact
---
Body text.
"""


def test_bundled_skills_have_valid_headers_and_stay_small():
    catalog = load_catalog()
    assert {"artifacts", "web-search"} <= set(catalog.names())
    for name in catalog.names():
        skill = catalog.get(name)
        assert skill.name == name and skill.version >= 1
        assert 0 < len(skill.body) <= MAX_BODY_CHARS
        assert skill.description and skill.requires_tool


def test_parse_reads_the_header_and_body():
    skill = parse_skill(GOOD)
    assert (skill.name, skill.version, skill.requires_tool, skill.body) == ("demo", 2, "create_artifact", "Body text.")


@pytest.mark.parametrize(
    "text",
    [
        "no front matter",
        "---\nname: demo\n",
        GOOD.replace("name: demo", "name: ../etc"),
        GOOD.replace("version: 2", "version: x"),
        GOOD.replace("requires_tool: create_artifact", "extra: 1"),
        GOOD.replace("description: Demo skill.", "description:"),
        GOOD.replace("Body text.", "x" * (MAX_BODY_CHARS + 1)),
        GOOD.replace("Body text.\n", ""),
    ],
)
def test_malformed_skill_files_are_rejected(text):
    with pytest.raises(ValueError):
        parse_skill(text)


def test_available_skills_follow_the_enabled_tools():
    catalog = load_catalog()
    assert [s.name for s in catalog.available({"web_search"})] == ["web-search"]
    assert [s.name for s in catalog.available({"create_artifact"})] == ["artifacts"]
    assert catalog.available(set()) == []


def test_lookup_accepts_only_known_string_names():
    catalog = SkillCatalog({"demo": parse_skill(GOOD)})
    assert catalog.get("demo") is not None
    for bad in ("../demo", "demo/../demo", None, 5, ["demo"]):
        assert catalog.get(bad) is None


async def test_group_read_skill_still_serves_only_the_artifacts_skill():
    from selara.infrastructure.llm.artifact_tools import ArtifactRequestContext, read_skill
    from selara.infrastructure.llm.tools import ToolCall

    context = ArtifactRequestContext(repository=None, renderer_url="", chat_id=1, creator_id=1, message_id=1)
    ok = await read_skill(ToolCall("read_skill", {"name": "artifacts"}, "1"), artifact_context=context)
    other = await read_skill(ToolCall("read_skill", {"name": "web-search"}, "2"), artifact_context=context)
    assert ok.success and "artifacts" in context.loaded_skills
    assert not other.success
