from __future__ import annotations

from unittest.mock import MagicMock

from selara.presentation.routers import build_router


def test_personal_ai_comes_after_private_panel_and_autoconfig_and_before_text_commands():
    # Module-level routers can be attached only once per process, so build exactly once.
    # llm_client=None also proves the router is registered with the LLM disabled: the handler
    # answers "unavailable" itself instead of silently ignoring private text.
    root = build_router(MagicMock(), activity_batcher=MagicMock(), llm_client=None)
    application = next(r for r in root.sub_routers if r.name == "application")
    names = [r.name for r in application.sub_routers]

    assert names.index("autoconfig") < names.index("personal_ai")
    assert names.index("private_panel") < names.index("personal_ai")
    assert names.index("personal_ai") < names.index("text_commands")
