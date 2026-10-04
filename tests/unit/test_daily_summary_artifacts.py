from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from selara.application.daily_summary.artifacts import create_daily_infographic
from selara.infrastructure.llm.client import LlmAccountingContext, LlmCallResult, LlmCallUsage
from uuid import uuid4


def response(*calls, content=''):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=[
        SimpleNamespace(id=str(i), function=SimpleNamespace(name=name, arguments=args))
        for i, (name, args) in enumerate(calls)
    ]))])


async def run(client, context, usage):
    return await create_daily_infographic(client=client, context=context, text='<b>Итоги</b>',
        themes=[{'title': 'Тема', 'episode_count': 2}], participant_directory=[], record_usages=usage,
        accounting_context=None)


def call_result(value):
    usage = LlmCallUsage(str(uuid4()), 'test-model', 1, 1, 2, None, 'unknown', 1, 'succeeded')
    return LlmCallResult(value, (usage,))


@pytest.fixture
def context():
    return SimpleNamespace(accompanying_text='', created_artifacts=[], chat_id=1)


async def test_model_can_skip_infographic(context):
    client = SimpleNamespace(chat_with_tools=AsyncMock(return_value=call_result(response(content='Картинка не нужна'))))
    usage = Mock()
    assert await run(client, context, usage) is None
    usage.assert_called_once()
    assert context.accompanying_text == 'Итоги'
    tools = client.chat_with_tools.call_args.kwargs['tools']
    assert {t['function']['name'] for t in tools} == {'read_skill', 'create_artifact'}


async def test_uses_created_id_not_arbitrary_model_output(context, monkeypatch):
    async def create(call, artifact_context):
        artifact_context.created_artifacts.append('trusted-created-id')
        return SimpleNamespace(success=True, result_text='{}')
    monkeypatch.setitem(__import__('selara.application.daily_summary.artifacts', fromlist=['_ALLOWED'])._ALLOWED, 'create_artifact', create)
    client = SimpleNamespace(chat_with_tools=AsyncMock(return_value=call_result(response(('create_artifact', '{}')))))
    assert await run(client, context, Mock()) == 'trusted-created-id'


async def test_arbitrary_final_id_is_ignored(context):
    client = SimpleNamespace(chat_with_tools=AsyncMock(return_value=call_result(response(content='{"artifact_id":"another-chat-id"}'))))
    assert await run(client, context, Mock()) is None


async def test_never_dispatches_sending_or_moderation(context):
    client = SimpleNamespace(chat_with_tools=AsyncMock(return_value=call_result(response(('ban_user', '{}')))))
    assert await run(client, context, Mock()) is None


async def test_failed_stage_preserves_text(context):
    client = SimpleNamespace(chat_with_tools=AsyncMock(side_effect=RuntimeError('LLM unavailable')))
    assert await run(client, context, Mock()) is None
    assert context.accompanying_text == 'Итоги'


async def test_tool_loop_is_bounded(context, monkeypatch):
    handler = AsyncMock(return_value=SimpleNamespace(success=False, result_text='failed'))
    monkeypatch.setitem(__import__('selara.application.daily_summary.artifacts', fromlist=['_ALLOWED'])._ALLOWED, 'create_artifact', handler)
    client = SimpleNamespace(chat_with_tools=AsyncMock(return_value=call_result(response(('create_artifact', '{}')))))
    assert await run(client, context, Mock()) is None
    assert client.chat_with_tools.call_count == 7
