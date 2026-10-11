# Personal AI simulated acceptance (#202)

The tool/prompt fix is already in PR #205. The additional acceptance regression
`test_simulated_personal_picture_then_source_edit_deliver_distinct_pngs` runs the
real Personal tool loop, artifact source validation, persistent artifact
repository, PNG validation and delivery code across two consecutive turns:

1. `read_skill → create_artifact → send_artifact` delivers a valid 1600×200 PNG.
2. `read_skill → get_artifact → create_artifact → send_artifact` retrieves the
   confirmed original source and delivers a new PNG with a different artifact ID;
   the original source remains unchanged.
3. The same sequence is checked after a plain-text `read_skill` promise, using
   the existing bounded corrective retry. Both turns stay within the round and
   priced cost budgets. The resolved `freeform`/`x-ai/grok-4.3` routing object is
   preserved across all rounds.

The provider is scripted, the renderer returns known valid PNGs via a mock HTTP
transport, and Telegram acknowledges `send_photo` via a fake bot. The artifact
store is real SQLite (with database-side DELETE synchronization to accommodate
SQLite's timezone-naive timestamps). Pixel/format/size assertions verify the
actual photo payloads passed to delivery. This does not verify rasterization
quality or actual Grok replies. Existing `test_agent_artifacts.py` separately
checks the local Chromium renderer and delivery failures.

The existing `test_personal_ai_character.py`, `test_personal_tools.py` and
`test_personal_tools_handler.py` suites cover honest capabilities, tools disabled,
no Personal entitlement, roleplay, allow-list restrictions, budget withdrawal,
last-round tool withdrawal, false success and repeated promises. The added
`test_model_without_tool_support_denies_artifacts_even_for_paid_user` explicitly
covers a model advertising `supports_tools=False` despite paid access and an
enabled artifact toggle.

Run:

```bash
python -m pytest -q tests/unit/test_personal_artifact_simulated_acceptance.py \
  tests/unit/test_personal_ai_character.py tests/unit/test_personal_tools.py \
  tests/unit/test_personal_tools_handler.py tests/unit/test_personal_ai_handlers.py
python -m pytest -q tests/unit/test_agent_artifacts.py
```

This is the simulated acceptance gate authorized by the user. Live Grok 4.3 /
Telegram delivery and production flags/budget evidence remain unverified; no
paid provider call or real Telegram message is needed by these tests.
