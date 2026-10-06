from __future__ import annotations

import inspect

from selara.presentation.routers import build_router


def test_personal_ai_comes_after_private_panel_and_autoconfig_and_before_text_commands():
    # Module-level routers attach to a parent only once per process (other tests already call
    # build_router), so the registration order is checked from the include calls themselves.
    # personal_ai is included unconditionally: with the LLM off it answers "unavailable" itself.
    source = inspect.getsource(build_router)

    def position(name: str) -> int:
        if name == "personal_ai_chat":
            return source.index("application.include_router(personal_ai_chat_router)")
        return source.index(f"application.include_router({name}_router)")

    assert position("autoconfig") < position("personal_ai")
    assert position("private_panel") < position("personal_ai")
    assert position("personal_ai") < position("text_commands")
    # The dialogue is the fallback of text_commands (which hands over unrecognised private text).
    assert position("text_commands") < position("personal_ai_chat")
    assert "if llm_client is not None:\n        application.include_router(personal_ai_chat_router)" not in source
