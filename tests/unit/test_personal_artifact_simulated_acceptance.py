"""Issue #202 simulated acceptance: scripted provider, local DB, fake Telegram.

This exercises the real Personal tool loop and artifact create/delivery functions.
It verifies PNG payloads and edit persistence; it does not claim live Grok behavior.
"""
from __future__ import annotations

import base64
import json
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image
from sqlalchemy import event

from selara.application.model_catalog import ModelCapabilities
from selara.application.model_router import ResolvedModel
from selara.infrastructure.llm import artifact_tools
from selara.infrastructure.llm.artifact_tools import ArtifactRequestContext
from selara.infrastructure.llm.personal_tools import PersonalToolRun, run_tool_dialogue
from selara.infrastructure.llm.web_tools import WebToolContext
from tests.unit.test_agent_artifacts import context  # noqa: F401 - real SQLite artifact fixture
from tests.unit.test_personal_tools import _response, _tool_call


class ScriptedArtifactProvider:
    """Fixed actions with IDs read from actual tool results, never invented IDs."""
    def __init__(self, actions, *, resolved, revised=False):
        self.actions = iter(actions)
        self.executed = []
        self.revised = revised
        self.resolved = resolved
        self.original_source = None

    async def chat_with_tools(self, messages, tools, **kwargs):
        assert kwargs["resolved_model"] is self.resolved
        action = next(self.actions)
        if action == "promise":
            return _response(content="Сейчас вызову read_skill и сделаю картинку.")
        offered = {t["function"]["name"] for t in tools}
        assert action in offered
        arguments = {"name": "artifacts"} if action == "read_skill" else {}
        if action == "create_artifact":
            if self.revised:
                self.original_source = json.loads(messages[-1]["content"])
                assert self.original_source["status"] == "sent"
                assert self.original_source["pages"] == ["<h1>Мои возможности</h1>"]
            arguments = {"title": "Мои возможности", "pages": ["<h1>Мои возможности</h1>"],
                         "css": "h1{color:purple}" if self.revised else "h1{color:blue}"}
        elif action == "send_artifact":
            created = json.loads(messages[-1]["content"])
            assert created.get("checks") == "passed" and not created["sent"], created
            arguments = {"artifact_id": created["artifact_id"], "caption": "Вот картинка"}
        self.executed.append(action)
        return _response(tool_calls=[_tool_call(action, **arguments)])


@pytest.mark.parametrize("initial_promise", [False, True])
async def test_simulated_personal_picture_then_source_edit_deliver_distinct_pngs(
    context, monkeypatch, initial_promise,
):
    # SQLite drops timestamp offsets. Let the database evaluate pruning DELETEs
    # instead of SQLAlchemy comparing its naive loaded rows to UTC in Python.
    def database_prunes_rows(state):
        if state.is_delete:
            state.update_execution_options(synchronize_session=False)
    event.listen(context.repository.session.sync_session, "do_orm_execute", database_prunes_rows)
    renderer_sources = []
    original_client = httpx.AsyncClient

    def render(request):
        source = json.loads(request.content)
        renderer_sources.append(source)
        image = Image.new("RGB", (1600, 200), "purple" if "purple" in source["css"] else "blue")
        output = BytesIO()
        image.save(output, format="PNG")
        return httpx.Response(200, json={"pages": [base64.b64encode(output.getvalue()).decode()]})

    # The renderer response is simulated; PNG integrity validation stays enabled.
    monkeypatch.setattr(artifact_tools.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(render), **kwargs,
    ))
    bot = SimpleNamespace(send_photo=AsyncMock(side_effect=[
        SimpleNamespace(message_id=201), SimpleNamespace(message_id=202),
    ]), send_message=AsyncMock())
    original_id = None
    for revised in (False, True):
        turn_context = ArtifactRequestContext(
            repository=context.repository, renderer_url="http://renderer", chat_id=context.chat_id,
            creator_id=context.creator_id, message_id=context.message_id, thread_id=context.thread_id,
        )
        run = PersonalToolRun(
            web_enabled=False, artifacts_enabled=True, web_context=WebToolContext(client=None),
            artifact_context=turn_context, bot=bot, total_rounds=6, cost_budget_usd=Decimal("0.02"),
        )
        actions = (["promise"] if initial_promise and not revised else []) + ["read_skill"]
        actions += (["get_artifact"] if revised else []) + ["create_artifact", "send_artifact"]
        # This label checks routing retention only, not a real Grok endpoint.
        resolved = ResolvedModel(model_id="x-ai/grok-4.3", profile_key="freeform",
                                 capabilities=ModelCapabilities(supports_tools=True), is_fallback=False)
        provider = ScriptedArtifactProvider(actions, resolved=resolved, revised=revised)
        usages = []
        result = await run_tool_dialogue(
            llm_client=provider, run=run, usage_sink=usages, resolved_model=resolved,
            messages=[{"role": "user", "content": "Сделай красивее" if revised else "Покажи графически"}],
        )
        assert result.artifact_sent and result.text == "Вот картинка"
        assert provider.executed == ["read_skill"] + (["get_artifact"] if revised else []) + ["create_artifact", "send_artifact"]
        assert turn_context.created_artifacts == turn_context.sent_artifacts
        row = await context.repository.get(
            artifact_id=turn_context.sent_artifacts[0], chat_id=context.chat_id, thread_id=context.thread_id,
        )
        assert row.delivery["complete"] and row.delivery["message_ids"] == [202 if revised else 201]
        assert not row.delivery.get("text_only")
        assert sum(u.estimated_cost_usd for u in usages) < run.cost_budget_usd
        if revised:
            assert row.id != original_id and provider.original_source["artifact_id"] == original_id
            original = await context.repository.get(artifact_id=original_id, chat_id=context.chat_id, thread_id=context.thread_id)
            assert original.source["css"] == "h1{color:blue}"
        else:
            original_id = row.id
    assert len(renderer_sources) == bot.send_photo.await_count == 2
    bot.send_message.assert_not_awaited()
    for invocation, color in zip(bot.send_photo.await_args_list, [(0, 0, 255), (128, 0, 128)], strict=True):
        photo = invocation.kwargs["photo"]
        with Image.open(BytesIO(photo.data)) as image:
            assert image.format == "PNG" and image.size == (1600, 200)
            assert image.getpixel((0, 0)) == color
