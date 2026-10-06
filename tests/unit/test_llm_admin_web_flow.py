"""Regression tests for the `??` handler's web-tool flow (web_search/fetch_page).

Covers the review finding where the tool loop referenced `web_context` without
ever binding it (NameError right after the first web tool result entered the
loop) and the invariants around the post-web research boundary:

- the web executors really execute offline (httpx.MockTransport, same markup
  fixtures as tests/unit/test_llm_web_tools.py);
- after the first web result enters the model context the tool list is
  withdrawn: the follow-up chat_with_tools call omits the "tools" key entirely
  (an empty tools=[] is not accepted uniformly across OpenAI-compatible
  providers);
- a stray un-advertised call (e.g. ban_user after withdrawal) is denied by the
  handler's server-side allowlist before execute_tool can dispatch it;
- the web-tainted assistant answer is saved with is_context=False so it never
  re-enters a later `??` invocation's trusted context;
- a plain non-web `??` invocation keeps tools advertised and is_context=True.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from aiogram.types import Message

import selara.presentation.handlers.llm_admin as llm_admin_module
from selara.core.chat_settings import ChatSettings
from selara.infrastructure.http.web_search import DuckDuckGoProvider, WebSearchClient
from selara.presentation.handlers.llm_admin import llm_admin_context_handler

# Same markup shape as tests/unit/test_llm_web_tools.py: result-link anchors
# plus result-snippet tds on the DuckDuckGo lite endpoint.
DDG_LITE_HTML = """
<html><head><title>selara at DuckDuckGo</title></head><body>
<table>
<tr><td>1.&nbsp;</td><td><a rel="nofollow" class="result-link"
 href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle&amp;rut=aaa">Example   Article</a></td></tr>
<tr><td class="result-snippet">First   snippet   text</td></tr>
</table>
</body></html>
"""

PAGE_OK_HTML = "<html><head><title>OK</title></head><body><p>hello</p></body></html>"
PUBLIC_PAGE_URL = "http://1.2.3.4/page"  # public literal IP: passes the SSRF guard offline


def _chat_settings() -> ChatSettings:
    return ChatSettings(
        top_limit_default=10,
        top_limit_max=50,
        vote_daily_limit=20,
        leaderboard_hybrid_karma_weight=0.7,
        leaderboard_hybrid_activity_weight=0.3,
        leaderboard_7d_days=7,
        leaderboard_week_start_weekday=0,
        leaderboard_week_start_hour=0,
        mafia_night_seconds=90,
        mafia_day_seconds=120,
        mafia_vote_seconds=60,
        mafia_reveal_eliminated_role=True,
        text_commands_enabled=True,
        text_commands_locale="ru",
        actions_18_enabled=True,
        smart_triggers_enabled=True,
        welcome_enabled=True,
        welcome_text="Привет, {user}!",
        welcome_button_text="",
        welcome_button_url="",
        goodbye_enabled=False,
        goodbye_text="Пока, {user}.",
        welcome_cleanup_service_messages=True,
        entry_captcha_enabled=False,
        entry_captcha_timeout_seconds=180,
        entry_captcha_kick_on_fail=True,
        custom_rp_enabled=True,
        family_tree_enabled=True,
        titles_enabled=True,
        title_price=50000,
        craft_enabled=True,
        auctions_enabled=True,
        auction_duration_minutes=10,
        auction_min_increment=100,
        economy_enabled=True,
        economy_mode="global",
        economy_tap_cooldown_seconds=45,
        economy_daily_base_reward=120,
        economy_daily_streak_cap=7,
        economy_lottery_ticket_price=150,
        economy_lottery_paid_daily_limit=10,
        economy_transfer_daily_limit=5000,
        economy_transfer_tax_percent=5,
        economy_market_fee_percent=2,
        economy_negative_event_chance_percent=22,
        economy_negative_event_loss_percent=30,
        llm_enabled=True,
        llm_context_threshold=9999,
    )


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.web_search_max_calls_per_invocation = 4
    settings.web_search_max_results = 5
    settings.web_search_max_page_chars = 8000
    settings.llm_cooldown_seconds = 0
    settings.bot_timezone = "UTC"
    settings.admin_user_id = 111
    settings.artifact_renderer_url = "http://artifact-renderer:8090"
    return settings


def _admin_message(text: str = "?? вопрос") -> AsyncMock:
    message = AsyncMock(spec=Message)
    message.message_id = 100
    message.message_thread_id = None
    message.chat = SimpleNamespace(id=-100123, type="supergroup", title="Test group")
    message.from_user = SimpleNamespace(
        id=111, username="admin", first_name="Admin", last_name=None, is_bot=False
    )
    message.text = text
    thinking_msg = AsyncMock()
    message.reply = AsyncMock(return_value=thinking_msg)
    return message


def _llm_client(responses: list) -> SimpleNamespace:
    """A fake LLM client: not an LlmClient instance, so the handler skips
    accounting wiring exactly like in the other handler unit tests."""
    return SimpleNamespace(chat_with_tools=AsyncMock(side_effect=responses))


def _tool_call(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _tool_calls_response(tool_calls: list) -> SimpleNamespace:
    payload = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in tool_calls
        ],
    }
    message = SimpleNamespace(
        content=None,
        tool_calls=tool_calls,
        model_dump=lambda exclude_none=True: payload,
    )
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=message)])


def _text_response(text: str) -> SimpleNamespace:
    message = SimpleNamespace(
        content=text,
        tool_calls=None,
        model_dump=lambda exclude_none=True: {"role": "assistant", "content": text},
    )
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=message)])


def _ddg_web_search_client() -> WebSearchClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=DDG_LITE_HTML)

    return WebSearchClient(provider=DuckDuckGoProvider(transport=httpx.MockTransport(handler)))


def _fetch_page_client() -> WebSearchClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=PAGE_OK_HTML, headers={"Content-Type": "text/html"})

    return WebSearchClient(
        provider=DuckDuckGoProvider(transport=httpx.MockTransport(handler)),
        transport=httpx.MockTransport(handler),
    )


@contextmanager
def _patched_handler_env():
    """The same patch targets the other llm_admin handler unit tests use
    (module-level names in selara.presentation.handlers.llm_admin)."""
    access_service = SimpleNamespace(
        reserve_feature_usage=AsyncMock(return_value=SimpleNamespace(
            allowed=True, reused=False, invocation_id=None, reason=None,
        )),
        release_if_no_provider_attempts=AsyncMock(return_value=False),
    )
    llm_repo = MagicMock()
    llm_repo.get_last_user_message_at = AsyncMock(return_value=None)
    llm_repo.search_glossary = AsyncMock(return_value=[])
    llm_repo.add_admin_action = AsyncMock(return_value=SimpleNamespace(id=1))
    with patch.object(llm_admin_module, "has_permission", new=AsyncMock(return_value=(True, None, None))), \
         patch.object(llm_admin_module, "resolve_owner_admin_exemption", new=AsyncMock(return_value=False)), \
         patch.object(llm_admin_module, "FeatureAccessService", new=MagicMock(return_value=access_service)), \
         patch.object(llm_admin_module, "LlmRepository", new=MagicMock(return_value=llm_repo)), \
         patch.object(llm_admin_module, "load_context", new=AsyncMock(return_value=SimpleNamespace(messages=[]))), \
         patch.object(llm_admin_module, "save_interaction", new=AsyncMock()) as save_interaction, \
         patch.object(llm_admin_module, "maybe_compress", new=AsyncMock()):
        yield SimpleNamespace(save_interaction=save_interaction, llm_repo=llm_repo)


def _delivered_text(message: AsyncMock) -> str:
    return "".join(
        str(call.args[0]) for call in message.reply.return_value.edit_text.await_args_list
    )


async def test_web_search_flow_reaches_final_answer():
    """Full `??` round-trip: web_search tool call -> REAL offline search
    execution -> tool list withdrawn -> plain text answer. The old bug raised
    NameError right after the web tool result because `web_context` was
    referenced but never bound."""
    message = _admin_message("?? что нового про selara?")
    llm_client = _llm_client([
        _tool_calls_response([_tool_call("call-ws", "web_search", {"query": "selara"})]),
        _text_response("Готово"),
    ])
    with _patched_handler_env() as env:
        await llm_admin_context_handler(
            message, AsyncMock(), MagicMock(), _chat_settings(), llm_client,
            AsyncMock(), _settings(), object(),
            web_search_client=_ddg_web_search_client(),
        )

    chat_mock = llm_client.chat_with_tools
    assert chat_mock.await_count == 2

    first_kwargs = chat_mock.await_args_list[0].kwargs
    assert "tools" in first_kwargs
    assert any(d["function"]["name"] == "web_search" for d in first_kwargs["tools"])

    # Research boundary: the follow-up call withdraws the tool list by
    # omitting the key entirely (not by sending an empty list).
    second_kwargs = chat_mock.await_args_list[1].kwargs
    assert "tools" not in second_kwargs

    # The executor REALLY ran against the mocked DDG transport: the search
    # result (unwrapped uddg link) is in the tool message fed to round 2.
    round_two_tool_messages = [m for m in second_kwargs["messages"] if m["role"] == "tool"]
    assert round_two_tool_messages, "web_search result never reached the model context"
    assert "https://example.com/article" in round_two_tool_messages[-1]["content"]

    assert "Готово" in _delivered_text(message)
    assert env.save_interaction.await_count == 1
    # Web-tainted answer must not re-enter a later `??` invocation's context.
    assert env.save_interaction.await_args.kwargs["is_context"] is False


async def test_fetch_page_flow_reaches_final_answer():
    """Same shape via fetch_page: real SSRF-guarded client on a MockTransport,
    then tools withdrawn and the final text answer delivered."""
    message = _admin_message("?? прочитай страницу")
    llm_client = _llm_client([
        _tool_calls_response([_tool_call("call-fp", "fetch_page", {"url": PUBLIC_PAGE_URL})]),
        _text_response("Готово"),
    ])
    with _patched_handler_env() as env:
        await llm_admin_context_handler(
            message, AsyncMock(), MagicMock(), _chat_settings(), llm_client,
            AsyncMock(), _settings(), object(),
            web_search_client=_fetch_page_client(),
        )

    chat_mock = llm_client.chat_with_tools
    assert chat_mock.await_count == 2
    assert "tools" in chat_mock.await_args_list[0].kwargs

    second_kwargs = chat_mock.await_args_list[1].kwargs
    assert "tools" not in second_kwargs

    round_two_tool_messages = [m for m in second_kwargs["messages"] if m["role"] == "tool"]
    assert round_two_tool_messages, "fetch_page result never reached the model context"
    assert "hello" in round_two_tool_messages[-1]["content"]

    assert "Готово" in _delivered_text(message)
    assert env.save_interaction.await_args.kwargs["is_context"] is False


async def test_post_web_tool_call_is_denied_by_allowlist():
    """A compromised model emits ban_user AFTER the web tool list was
    withdrawn. The handler's server-side allowlist must deny dispatch before
    execute_tool runs, feed the model a corrective tool error, and still
    finish with the final answer."""
    message = _admin_message("?? найди и забань")
    llm_client = _llm_client([
        _tool_calls_response([_tool_call("call-ws", "web_search", {"query": "selara"})]),
        _tool_calls_response([_tool_call("call-ban", "ban_user", {"target": "@x"})]),
        _text_response("Готово"),
    ])
    activity_repo = MagicMock()
    activity_repo.apply_moderation_action = AsyncMock()
    bot = AsyncMock()
    with _patched_handler_env() as env:
        await llm_admin_context_handler(
            message, bot, activity_repo, _chat_settings(), llm_client,
            AsyncMock(), _settings(), object(),
            web_search_client=_ddg_web_search_client(),
        )

    chat_mock = llm_client.chat_with_tools
    assert chat_mock.await_count == 3
    assert "tools" not in chat_mock.await_args_list[1].kwargs
    assert "tools" not in chat_mock.await_args_list[2].kwargs

    # The ban never dispatched: no moderation state change, no Telegram side
    # effect, no audit row.
    activity_repo.apply_moderation_action.assert_not_awaited()
    bot.ban_chat_member.assert_not_awaited()
    env.llm_repo.add_admin_action.assert_not_awaited()

    saved_tool_messages = env.save_interaction.await_args.kwargs["tool_messages"]
    assert any(
        "Инструмент недоступен в текущей фазе запроса." in m["content"]
        for m in saved_tool_messages
    ), f"allowlist denial missing from tool messages: {saved_tool_messages}"

    assert "Готово" in _delivered_text(message)
    assert env.save_interaction.await_args.kwargs["is_context"] is False


async def test_non_web_flow_keeps_tools_in_kwargs():
    """Contrast case: a `??` invocation that never touches web tools keeps the
    tool list advertised on its single provider call and saves the answer as
    trusted context."""
    message = _admin_message("?? сколько участников?")
    llm_client = _llm_client([_text_response("Простой ответ")])
    with _patched_handler_env() as env:
        await llm_admin_context_handler(
            message, AsyncMock(), MagicMock(), _chat_settings(), llm_client,
            AsyncMock(), _settings(), object(),
            web_search_client=None,
        )

    chat_mock = llm_client.chat_with_tools
    assert chat_mock.await_count == 1
    first_kwargs = chat_mock.await_args_list[0].kwargs
    assert first_kwargs.get("tools"), "tools key missing on a non-web invocation"
    assert len(first_kwargs["tools"]) > 0

    assert "Простой ответ" in _delivered_text(message)
    assert env.save_interaction.await_args.kwargs["is_context"] is True
