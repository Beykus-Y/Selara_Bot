from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from selara.application.ai_character.group import LAST_ROUND_NOTICE
from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.infrastructure.llm.tools import ToolResult
from selara.presentation.handlers import llm_admin as handler
from selara.presentation.llm_formatting import html_to_plain_text


def response(content=None, calls=None, finish='stop'):
    message = SimpleNamespace(content=content, tool_calls=calls,
        model_dump=lambda **_: {'role': 'assistant', 'content': content, 'tool_calls': [
            {'id': c.id, 'type': 'function', 'function': {'name': c.function.name, 'arguments': c.function.arguments}}
            for c in calls or []]})
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])


def call(name, **arguments):
    return SimpleNamespace(id=name, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def top_result():
    return ToolResult('get_top', 'get_top', json.dumps({'mode': 'karma', 'period': '30d',
        'top': [{'user_id': 1, 'username': '@one', 'messages': 436, 'karma': 2}]}), 'Получен топ по карме')


async def run(responses, execute=None):
    settings = Settings(bot_token='123:TEST', database_url='sqlite+aiosqlite:///:memory:')
    message = SimpleNamespace(text='?? Покажи таблицу и график топа', message_id=1, message_thread_id=None,
        chat=SimpleNamespace(id=-100123, type='supergroup', title='Чат'),
        from_user=SimpleNamespace(id=1, username='one', first_name='Один', last_name=None, is_bot=False),
        reply=AsyncMock(return_value=AsyncMock()))
    client = SimpleNamespace(chat_with_tools=AsyncMock(side_effect=responses), chat_simple=AsyncMock())
    repo = MagicMock()
    repo.get_last_user_message_at = AsyncMock(return_value=None)
    repo.search_glossary = AsyncMock(return_value=[])
    execute = execute or AsyncMock(return_value=top_result())
    access_service = SimpleNamespace(
        reserve_feature_usage=AsyncMock(return_value=SimpleNamespace(
            allowed=True, reused=False, invocation_id=None, reason=None,
        )),
        release_if_no_provider_attempts=AsyncMock(return_value=False),
    )
    with patch.object(handler, 'has_permission', AsyncMock(return_value=(True, None, None))), \
         patch.object(handler, 'resolve_owner_admin_exemption', AsyncMock(return_value=False)), \
         patch.object(handler, 'FeatureAccessService', return_value=access_service), \
         patch.object(handler, 'LlmRepository', return_value=repo), \
         patch.object(handler, 'load_context', AsyncMock(return_value=SimpleNamespace(messages=[]))), \
         patch.object(handler, 'execute_tool', execute), \
         patch.object(handler, '_send_formatted_answer', AsyncMock()) as sent, \
         patch.object(handler, '_send_dm_summary', AsyncMock()) as summary, \
         patch.object(handler, 'save_interaction', AsyncMock()) as saved:
        await handler._handle(message, AsyncMock(), MagicMock(), replace(default_chat_settings(settings), llm_enabled=True), client,
            AsyncMock(), with_context=False, settings=settings, session_factory=object())
    return client, execute, sent, summary, saved


async def test_stop_metadata_does_not_discard_tool_calls():
    client, execute, sent, _, _ = await run([response(calls=[call('get_top')], finish='stop'), response('Готовый ответ')])
    execute.assert_awaited_once()
    assert sent.call_args.args[2] == 'Готовый ответ' and client.chat_with_tools.await_count == 2


@pytest.mark.parametrize('empty', [response(''), response('   '), response(), SimpleNamespace(choices=[])])
async def test_empty_completion_recovers_without_replaying_tools(empty):
    client, execute, sent, _, _ = await run([response(calls=[call('get_top')]), empty, response('Полученные результаты')])
    assert execute.await_count == 1 and client.chat_with_tools.await_count == 3
    assert sent.call_args.args[2] == 'Полученные результаты'
    messages = client.chat_with_tools.call_args.kwargs['messages']
    assert not any(m['role'] == 'assistant' and not m.get('content') and not m.get('tool_calls') for m in messages)


async def test_persistent_empty_output_uses_verified_numbers_and_saves_actual_reply():
    client, execute, sent, summary, saved = await run([
        response(calls=[call('get_top')]), response(), response(), response()])
    assert client.chat_with_tools.await_count == 4 and execute.await_count == 1
    answer = sent.call_args.args[2]
    assert 'Карма — последние 30 дней' in answer and '@one — 2' in answer
    assert '436' not in answer and 'Ассистент не дал ответа' not in answer
    assert saved.call_args.kwargs['assistant_response'] == answer
    assert summary.call_args.kwargs['final_answer'] == answer


async def test_recovered_artifact_delivery_is_terminal_and_not_repeated():
    async def execute(request, **ctx):
        if request.name == 'get_top':
            return top_result()
        ctx['artifact_context'].sent_artifacts.append('current')
        return ToolResult(request.call_id, request.name, '{"status":"sent"}', 'Ответ отправлен')
    c, execute, sent, summary, _ = await run([response(calls=[call('get_top')]), response(),
        response(calls=[call('send_artifact', artifact_id='current', caption='Пояснение')], finish='stop')], execute)
    assert c.chat_with_tools.await_count == 3
    sent.assert_not_awaited()
    assert summary.call_args.kwargs['sent_artifacts'] == ['current']


async def test_empty_recovery_stays_inside_total_round_budget():
    c, execute, sent, _, _ = await run([response(calls=[call('get_top')])] * 4)
    assert c.chat_with_tools.await_count == 4
    assert 'полученные данные' in sent.call_args.args[2]


async def test_last_round_offers_no_tools_tells_the_model_and_caps_tokens():
    c, execute, sent, _, _ = await run([response(calls=[call('get_top')])] * 3 + [response('Итог без документов')])
    assert c.chat_with_tools.await_count == 4 and execute.await_count == 3
    last = c.chat_with_tools.await_args_list[-1].kwargs
    assert last['tools'] == [] and last['messages'][-1]['content'] == LAST_ROUND_NOTICE
    assert all(call_.kwargs['tools'] for call_ in c.chat_with_tools.await_args_list[:-1])
    # The short cap is for the tool-free final answer; tool rounds (artifact arguments) stay uncapped by it.
    assert all('max_tokens' not in call_.kwargs for call_ in c.chat_with_tools.await_args_list[:-1])
    assert last['max_tokens'] == 800
    assert sent.call_args.args[2] == 'Итог без документов'


async def test_truncated_tool_arguments_become_a_tool_error_not_a_crash():
    broken = SimpleNamespace(id='create_artifact', function=SimpleNamespace(name='create_artifact', arguments='{"title": "x", "pages": ["<div'))
    c, execute, sent, _, _ = await run([response(calls=[broken], finish='length'), response('Ответ без артефакта')])
    execute.assert_not_awaited()
    tool_msg = [m for m in c.chat_with_tools.await_args_list[-1].kwargs['messages'] if m['role'] == 'tool'][0]
    assert 'Некорректные аргументы' in tool_msg['content']
    assert sent.call_args.args[2] == 'Ответ без артефакта'


async def test_final_answer_cut_by_the_token_cap_is_marked():
    c, execute, sent, _, _ = await run([response('Длинный ответ', finish='length')])
    assert sent.call_args.args[2].startswith('Длинный ответ') and 'обрезан' in sent.call_args.args[2]


async def test_stray_tool_call_on_the_last_round_is_not_executed():
    c, execute, sent, _, _ = await run([response(calls=[call('get_top')])] * 4)
    assert execute.await_count == 3


async def test_subscribed_chat_gets_eight_rounds():
    with patch.object(handler, 'chat_has_selara_ai', AsyncMock(return_value=True)):
        c, execute, sent, _, _ = await run([response(calls=[call('get_top')])] * 7 + [response('Итог')])
    assert c.chat_with_tools.await_count == 8 and execute.await_count == 7
    assert c.chat_with_tools.await_args_list[-1].kwargs['tools'] == []


async def test_private_summary_does_not_invent_delivery_or_call_a_model():
    bot = AsyncMock()
    await handler._send_dm_summary(bot, admin_user_id=1, chat_title='<script>чат</script>',
        query='Покажи топ', tool_results=[ToolResult('skill', 'read_skill', '{}', 'Навык прочитан'), top_result()], final_answer='Не удалось подготовить картинку.',
        sent_artifacts=[])
    text = html_to_plain_text(bot.send_message.call_args.kwargs['text'])
    assert 'Подтверждённо отправлено артефактов: 0' in text
    assert 'Отправка изображения не подтверждена' in text and 'Не удалось подготовить картинку.' in text
    assert 'Задача выполнена' not in text and '<script>' not in bot.send_message.call_args.kwargs['text']


async def test_private_summary_keeps_errors_and_confirmed_delivery_with_rollback_button():
    bot = AsyncMock()
    results = [ToolResult('a', 'warn_user', '{}', 'Выдано предупреждение', success=True,
        db_action_id=42, undo_payload={'x': 1}),
        ToolResult('b', 'create_artifact', '{"error":"Ошибка <svg>"}', '', success=False)]
    await handler._send_dm_summary(bot, admin_user_id=1, chat_title='Чат', query='Запрос',
        tool_results=results, final_answer='Пояснение', sent_artifacts=['id'])
    text = html_to_plain_text(bot.send_message.call_args.kwargs['text'])
    assert 'Подтверждённо отправлено артефактов: 1' in text and 'Ошибка <svg>' in text
    assert bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data == 'llm_rollback:42'


@pytest.mark.asyncio
async def test_malformed_tool_json_finalizes_invocation_after_provider_usage():
    from selara.infrastructure.llm.client import LlmClient, LlmConfig

    accounting = SimpleNamespace(
        create_invocation=AsyncMock(return_value=41),
        report_provider_attempt=AsyncMock(),
        finish_invocation_outcome=AsyncMock(),
    )
    llm_client = LlmClient(LlmConfig(api_key='test-key', model='gpt-4o-mini'), accounting_service=accounting)
    malformed_tool_message = SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(
            id='broken-call', function=SimpleNamespace(name='get_top', arguments='{not-json'),
        )],
        model_dump=lambda **_: {'role': 'assistant', 'tool_calls': []},
    )
    provider_response = SimpleNamespace(
        model='gpt-4o-mini', usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120),
        choices=[SimpleNamespace(message=malformed_tool_message, finish_reason='tool_calls')],
    )
    llm_client._client.chat.completions.create = AsyncMock(return_value=provider_response)
    settings = Settings(bot_token='123:TEST', database_url='sqlite+aiosqlite:///:memory:')
    message = SimpleNamespace(
        text='? Покажи топ', message_id=8, message_thread_id=None,
        chat=SimpleNamespace(id=-100123, type='supergroup', title='Чат'),
        from_user=SimpleNamespace(id=1, username='one', first_name='Один', last_name=None, is_bot=False),
        reply=AsyncMock(return_value=AsyncMock()),
    )
    repo = MagicMock()
    repo.get_last_user_message_at = AsyncMock(return_value=None)
    repo.search_glossary = AsyncMock(return_value=[])

    with patch.object(handler, 'has_permission', AsyncMock(return_value=(True, None, None))), \
         patch.object(handler, 'resolve_owner_admin_exemption', AsyncMock(return_value=False)), \
         patch.object(handler, 'FeatureAccessService', return_value=SimpleNamespace(
             reserve_feature_usage=AsyncMock(return_value=SimpleNamespace(
                 allowed=True, reused=False, invocation_id=41, reason=None,
             )),
             release_if_no_provider_attempts=AsyncMock(return_value=False),
         )), \
         patch.object(handler, 'LlmRepository', return_value=repo), \
         patch.object(handler, 'save_interaction', AsyncMock()):
        with pytest.raises(json.JSONDecodeError):
            await handler._handle(
                message, AsyncMock(), MagicMock(), replace(default_chat_settings(settings), llm_enabled=True),
                llm_client, AsyncMock(), with_context=False, settings=settings, session_factory=object(),
            )

    accounting.report_provider_attempt.assert_awaited_once()
    accounting.create_invocation.assert_not_awaited()
    accounting.finish_invocation_outcome.assert_awaited_once_with(
        invocation_id=41, status='failed', error_category='handler_error',
    )
