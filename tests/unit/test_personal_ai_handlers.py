from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.feature_access import AccessReason, AccessTier, FeatureAccessDecision
from selara.application.personal_config import StaticPersonalConfigProvider, config_from_settings
from selara.core.config import Settings
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import UserModel
from selara.infrastructure.db.personal_ai_repository import PersonalAiRepository
from selara.infrastructure.llm.client import LlmClientError
from selara.infrastructure.llm.features import AiFeature
from selara.infrastructure.llm import personal_ai as llm_personal_ai
from selara.presentation.handlers import personal_ai as handler
from selara.presentation.handlers import private_panel

USER_ID = 5


def _settings(monkeypatch, *, personal_price: str | None = "69", admin_id: str | None = None) -> Settings:
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/selara_test")
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "test-provider-key")
    monkeypatch.setenv("LLM_COOLDOWN_SECONDS", "0")
    for name, value in (("SELARA_PERSONAL_PRICE_STARS", personal_price), ("ADMIN_USER_ID", admin_id)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def _decision(**overrides) -> FeatureAccessDecision:
    values = dict(
        allowed=True, feature=AiFeature.PERSONAL_CHAT, scope_type="user", scope_id=str(USER_ID),
        access_tier=AccessTier.FREE, quota_limit=5, quota_used=1, quota_remaining=4,
        period_start=None, period_end=None, invocation_id=None,
    )
    values.update(overrides)
    return FeatureAccessDecision(**values)


class _FakeAccess:
    """Stands in for FeatureAccessService: records reservations instead of touching PostgreSQL."""

    instances: list["_FakeAccess"] = []
    decision: FeatureAccessDecision = _decision()

    def __init__(self, *args, **kwargs) -> None:
        self.init_kwargs = kwargs
        self.reservations: list[dict] = []
        self.released: list[tuple[int, str]] = []
        _FakeAccess.instances.append(self)

    async def reserve_feature_usage(self, **kwargs):
        self.reservations.append(kwargs)
        return _FakeAccess.decision

    async def release_if_no_provider_attempts(self, *, invocation_id, reason):
        self.released.append((invocation_id, reason))
        return True


@pytest.fixture(autouse=True)
def _fake_quota(monkeypatch):
    _FakeAccess.instances = []
    _FakeAccess.decision = _decision()
    monkeypatch.setattr(handler, "FeatureAccessService", _FakeAccess)
    monkeypatch.setattr(handler, "SqlAlchemyFeatureQuotaRepository", lambda *a, **k: object())
    monkeypatch.setattr(handler, "SqlAlchemyUserEntitlementResolver", lambda *a, **k: object())
    handler._pending_inputs.clear()
    handler._inflight_users.clear()
    private_panel._pending_cfg_inputs.clear()
    private_panel._pending_admin_inputs.clear()


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(UserModel(telegram_user_id=USER_ID, is_bot=False))
        await s.commit()
        yield s
    await engine.dispose()


def _message(text: str, *, chat_type: str = "private", message_id: int = 100, user_id: int = USER_ID):
    thinking = AsyncMock()
    message = MagicMock()
    message.text = text
    message.message_id = message_id
    message.chat = SimpleNamespace(id=user_id, type=chat_type)
    message.from_user = SimpleNamespace(id=user_id, is_bot=False, username="u")
    message.successful_payment = None
    message.answer = AsyncMock(return_value=thinking)
    message.bot = SimpleNamespace(send_chat_action=AsyncMock())
    message.thinking = thinking
    return message


class _FakeLlm:
    def __init__(self, *, answer: str = "Привет! Я здесь.", error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.chat_calls: list[list[dict]] = []
        self.tools_requested = False
        self.summarize_calls = 0

    async def chat_simple(self, messages, **kwargs):
        self.chat_calls.append(messages)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(value=self.answer)

    async def chat_with_tools(self, *args, **kwargs):  # pragma: no cover - must never be reached
        self.tools_requested = True
        raise AssertionError("personal AI must not request tools")

    async def summarize(self, messages, **kwargs):
        self.summarize_calls += 1
        return SimpleNamespace(value="сжатое резюме")


async def _run(message, session, settings, llm):
    await handler.personal_chat_handler(
        message,
        db_session=session,
        session_factory=MagicMock(),
        settings=settings,
        personal_config=StaticPersonalConfigProvider(config_from_settings(settings)),
        llm_client=llm,
    )


# --- who gets the dialogue -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "chat_type", "expected"),
    [
        ("расскажи анекдот", "private", True),
        ("/start", "private", False),
        ("/ai_reset", "private", False),
        ("привет", "group", False),
        ("привет", "supergroup", False),
        ("   ", "private", False),
        (None, "private", False),
    ],
)
async def test_chat_filter_only_takes_plain_private_text(text, chat_type, expected):
    assert await handler.PersonalChatFilter()(_message(text, chat_type=chat_type)) is expected


async def test_chat_filter_yields_to_other_private_flows_pending_input():
    private_panel._set_pending_cfg_input(user_id=USER_ID, chat_id=-100, key="warn_limit")
    assert await handler.PersonalChatFilter()(_message("5")) is False
    private_panel._clear_pending_cfg_input(USER_ID)

    private_panel._set_pending_admin_input(user_id=USER_ID, chat_id=-100, mode="broadcast")
    assert await handler.PersonalChatFilter()(_message("текст рассылки")) is False
    private_panel._pending_admin_inputs.clear()

    handler._set_pending_input(USER_ID, "name")
    assert await handler.PersonalChatFilter()(_message("Новое имя")) is False
    assert await handler.PendingPersonalInputFilter()(_message("Новое имя")) is True


async def test_chat_handler_is_registered_on_the_router_that_follows_text_commands():
    assert any(h.callback is handler.personal_chat_handler for h in handler.chat_router.message.handlers)
    assert not any(h.callback is handler.personal_chat_handler for h in handler.router.message.handlers)


# --- quota -------------------------------------------------------------------------


async def test_one_user_message_reserves_exactly_one_personal_request_for_the_user_scope(monkeypatch, session):
    settings = _settings(monkeypatch)
    llm = _FakeLlm()
    message = _message("привет", message_id=321)

    await _run(message, session, settings, llm)

    reservation = _FakeAccess.instances[0].reservations
    assert len(reservation) == 1
    call = reservation[0]
    assert call["feature"] == AiFeature.PERSONAL_CHAT
    assert call["scope"].scope_type.value == "user" and call["scope"].scope_id == USER_ID
    assert call["chat_type"] == "private" and call["source_message_id"] == 321
    assert call["idempotency_key"].endswith(":321") and call["owner_exempt"] is False
    assert len(llm.chat_calls) == 1


async def test_owner_is_exempt_in_private_chat_by_identity(monkeypatch, session):
    settings = _settings(monkeypatch, admin_id=str(USER_ID))

    await _run(_message("привет"), session, settings, _FakeLlm())

    assert _FakeAccess.instances[0].reservations[0]["owner_exempt"] is True


async def test_exhausted_free_quota_offers_personal_and_never_calls_the_model(monkeypatch, session):
    settings = _settings(monkeypatch, personal_price="69")
    _FakeAccess.decision = _decision(allowed=False, reason=AccessReason.QUOTA_EXHAUSTED, quota_used=5)
    llm = _FakeLlm()
    message = _message("ещё вопрос")

    await _run(message, session, settings, llm)

    assert llm.chat_calls == []
    args, kwargs = message.answer.await_args
    assert "5/5" in args[0]
    markup = kwargs["reply_markup"]
    assert [b.callback_data for row in markup.inline_keyboard for b in row] == ["premium:self"]


async def test_personal_offer_stays_hidden_without_a_configured_price(monkeypatch, session):
    settings = _settings(monkeypatch, personal_price=None)
    _FakeAccess.decision = _decision(allowed=False, reason=AccessReason.QUOTA_EXHAUSTED, quota_used=5)
    message = _message("ещё вопрос")

    await _run(message, session, settings, _FakeLlm())

    assert message.answer.await_args.kwargs["reply_markup"] is None


async def test_paid_user_who_used_the_limit_gets_no_purchase_button(monkeypatch, session):
    settings = _settings(monkeypatch)
    _FakeAccess.decision = _decision(
        allowed=False, reason=AccessReason.QUOTA_EXHAUSTED, access_tier=AccessTier.PAID, quota_limit=150, quota_used=150
    )
    message = _message("ещё вопрос")

    await _run(message, session, settings, _FakeLlm())

    assert message.answer.await_args.kwargs["reply_markup"] is None


async def test_unavailable_llm_is_reported_honestly_without_spending_quota(monkeypatch, session):
    message = _message("привет")

    await _run(message, session, _settings(monkeypatch), None)

    assert "недоступен" in message.answer.await_args.args[0]
    assert _FakeAccess.instances == []


async def test_too_long_message_does_not_spend_quota(monkeypatch, session):
    message = _message("x" * (llm_personal_ai.MAX_USER_TEXT_LENGTH + 1))

    await _run(message, session, _settings(monkeypatch), _FakeLlm())

    assert _FakeAccess.instances == []


async def test_replayed_update_is_not_processed_twice(monkeypatch, session):
    _FakeAccess.decision = _decision(reused=True)
    llm = _FakeLlm()

    await _run(_message("привет"), session, _settings(monkeypatch), llm)

    assert llm.chat_calls == []


# --- dialogue and history --------------------------------------------------------------


async def test_reply_is_sent_and_both_turns_are_stored_in_the_private_history(monkeypatch, session):
    settings = _settings(monkeypatch)
    llm = _FakeLlm(answer="Конечно, помогу.")
    message = _message("помоги")

    await _run(message, session, settings, llm)

    rows = await PersonalAiRepository(session).recent_messages(user_id=USER_ID, thread="assistant", limit=10)
    assert [(r.role, r.content) for r in rows] == [("user", "помоги"), ("assistant", "Конечно, помогу.")]
    message.thinking.edit_text.assert_awaited()
    assert "Конечно, помогу." in message.thinking.edit_text.await_args.args[0]
    assert llm.tools_requested is False


async def test_second_turn_sends_previous_history_to_the_model(monkeypatch, session):
    settings = _settings(monkeypatch)
    llm = _FakeLlm()

    await _run(_message("первое", message_id=1), session, settings, llm)
    await _run(_message("второе", message_id=2), session, settings, llm)

    second_prompt = llm.chat_calls[1]
    assert [m["content"] for m in second_prompt[1:]] == ["первое", "Привет! Я здесь.", "второе"]


async def test_roleplay_uses_its_own_history_thread(monkeypatch, session):
    settings = _settings(monkeypatch)
    repo = PersonalAiRepository(session)
    stored = await repo.get_or_create_profile(USER_ID)
    await repo.update_profile(USER_ID, expected_revision=stored.revision, mode="roleplay")
    await repo.add_message(user_id=USER_ID, thread="assistant", role="user", content="обычный разговор")
    llm = _FakeLlm()

    await _run(_message("Я вхожу в таверну"), session, settings, llm)

    prompt_text = " ".join(m["content"] for m in llm.chat_calls[0])
    assert "обычный разговор" not in prompt_text
    assert [r.content for r in await repo.recent_messages(user_id=USER_ID, thread="roleplay", limit=10)][0] == "Я вхожу в таверну"
    assert _FakeAccess.instances[0].reservations[0]["mode"] == "roleplay"


async def test_failed_model_call_stores_nothing_and_tells_the_user(monkeypatch, session):
    llm = _FakeLlm(error=LlmClientError("boom", usages=()))
    message = _message("привет")

    await _run(message, session, _settings(monkeypatch), llm)

    assert await PersonalAiRepository(session).recent_messages(user_id=USER_ID, thread="assistant", limit=10) == []
    assert "Не удалось" in message.thinking.edit_text.await_args.args[0]


async def test_old_history_is_compressed_into_a_summary_without_a_second_quota_charge(monkeypatch, session):
    settings = _settings(monkeypatch)
    repo = PersonalAiRepository(session)
    for i in range(llm_personal_ai.PERSONAL_CONTEXT_THRESHOLD):
        await repo.add_message(user_id=USER_ID, thread="assistant", role="user" if i % 2 == 0 else "assistant", content=f"m{i}")
    llm = _FakeLlm()

    await _run(_message("новое"), session, settings, llm)

    assert llm.summarize_calls == 1
    assert (await repo.latest_summary(user_id=USER_ID, thread="assistant")).content == "сжатое резюме"
    assert len(_FakeAccess.instances[0].reservations) == 1  # compression is internal, not a user request
    assert nothing_deleted(await repo.count_uncompressed(user_id=USER_ID, thread="assistant"))


def nothing_deleted(uncompressed_left: int) -> bool:
    # Compression marks rows instead of deleting them: the user's history stays until they erase it.
    return uncompressed_left == llm_personal_ai.PERSONAL_CONTEXT_THRESHOLD + 2 - llm_personal_ai.PERSONAL_COMPRESS_BATCH


# --- wizard and reset --------------------------------------------------------------------


async def test_ai_reset_clears_active_thread_and_keeps_profile(session):
    repo = PersonalAiRepository(session)
    stored = await repo.get_or_create_profile(USER_ID)
    await repo.update_profile(USER_ID, expected_revision=stored.revision, display_name="Селя")
    await repo.add_message(user_id=USER_ID, thread="assistant", role="user", content="a")
    message = _message("/ai_reset")

    await handler.ai_reset_command(message, session)

    assert await repo.recent_messages(user_id=USER_ID, thread="assistant", limit=5) == []
    assert (await repo.get_profile(USER_ID)).profile.display_name == "Селя"
    assert "очищена" in message.answer.await_args.args[0]


async def test_ai_commands_refuse_groups(session):
    message = _message("/ai", chat_type="supergroup")

    await handler.ai_settings_command(message, session)

    assert "личных сообщениях" in message.answer.await_args.args[0]
    assert await PersonalAiRepository(session).get_profile(USER_ID) is None


def _callback(data: str):
    query = MagicMock()
    query.data = data
    query.from_user = SimpleNamespace(id=USER_ID)
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.chat = SimpleNamespace(type="private")
    query.message.edit_text = AsyncMock()
    query.message.delete = AsyncMock()
    return query


async def test_wizard_toggle_saves_and_a_stale_revision_is_refused(session):
    repo = PersonalAiRepository(session)
    await repo.get_or_create_profile(USER_ID)

    await handler.ai_settings_callback(_callback("pai:set:emoji:0:0"), session)
    stale = _callback("pai:set:mode:roleplay:0")
    await handler.ai_settings_callback(stale, session)

    stored = await repo.get_profile(USER_ID)
    assert stored.profile.emoji_enabled is False and stored.revision == 1
    assert stored.profile.mode == "assistant"  # the stale button did not apply
    assert "уже изменились" in stale.answer.await_args.args[0]


async def test_wizard_rejects_unknown_values(session):
    repo = PersonalAiRepository(session)
    await repo.get_or_create_profile(USER_ID)

    await handler.ai_settings_callback(_callback("pai:set:mode:evil:0"), session)
    await handler.ai_settings_callback(_callback("pai:preset:nope:0"), session)

    stored = await repo.get_profile(USER_ID)
    assert stored.revision == 0 and stored.profile.mode == "assistant"


async def test_text_input_saves_custom_character_and_validates_length(session):
    repo = PersonalAiRepository(session)
    handler._set_pending_input(USER_ID, "custom")

    too_long = _message("я" * 501)
    await handler.ai_settings_input(too_long, session)
    assert "не больше 500" in too_long.answer.await_args.args[0]
    assert handler._get_pending_input(USER_ID) is not None

    good = _message("Говори как капитан пиратов")
    await handler.ai_settings_input(good, session)

    stored = await repo.get_profile(USER_ID)
    assert stored.profile.character_preset == "custom"
    assert stored.profile.character_custom == "Говори как капитан пиратов"
    assert handler._get_pending_input(USER_ID) is None


async def test_input_prompt_button_sets_pending_state(session):
    await PersonalAiRepository(session).get_or_create_profile(USER_ID)

    await handler.ai_settings_callback(_callback("pai:in:name:0"), session)

    assert handler._get_pending_input(USER_ID).field == "name"


# --- concurrency and failure edges ------------------------------------------------------


async def test_second_message_during_an_inflight_turn_is_refused_without_quota(monkeypatch, session):
    settings = _settings(monkeypatch)
    llm = _FakeLlm()
    handler._inflight_users.add(USER_ID)
    message = _message("ещё одно")

    await _run(message, session, settings, llm)

    assert "ещё отвечаю" in message.answer.await_args.args[0]
    assert _FakeAccess.instances == [] and llm.chat_calls == []


async def test_inflight_marker_is_released_after_success_and_after_failure(monkeypatch, session):
    settings = _settings(monkeypatch)

    await _run(_message("раз"), session, settings, _FakeLlm())
    assert USER_ID not in handler._inflight_users

    await _run(_message("два", message_id=101), session, settings, _FakeLlm(error=RuntimeError("x")))
    assert USER_ID not in handler._inflight_users


async def test_pending_user_row_is_committed_before_the_quota_reservation(monkeypatch, session):
    settings = _settings(monkeypatch)
    order: list[str] = []
    real_commit = session.commit

    async def tracking_commit():
        order.append("commit")
        await real_commit()

    monkeypatch.setattr(session, "commit", tracking_commit)
    original = _FakeAccess.reserve_feature_usage

    async def tracking_reserve(self, **kwargs):
        order.append("reserve")
        return await original(self, **kwargs)

    monkeypatch.setattr(_FakeAccess, "reserve_feature_usage", tracking_reserve)

    await _run(_message("привет"), session, settings, _FakeLlm())

    assert order.index("commit") < order.index("reserve")


async def test_failed_placeholder_send_still_releases_the_reservation(monkeypatch, session):
    settings = _settings(monkeypatch)
    _FakeAccess.decision = _decision(invocation_id=77)
    accounting = SimpleNamespace(finish_invocation_outcome=AsyncMock())
    llm = _FakeLlm()
    llm.accounting_service = accounting
    monkeypatch.setattr(handler, "LlmClient", _FakeLlm)
    message = _message("привет")
    message.answer = AsyncMock(side_effect=RuntimeError("telegram down"))

    with pytest.raises(RuntimeError):
        await _run(message, session, settings, llm)

    assert _FakeAccess.instances[0].released and _FakeAccess.instances[0].released[0][0] == 77
    accounting.finish_invocation_outcome.assert_awaited_once()
    assert USER_ID not in handler._inflight_users


# --- connections ----------------------------------------------------------------------------


async def test_no_transaction_is_held_open_during_the_provider_call(monkeypatch, session):
    settings = _settings(monkeypatch)
    seen: list[bool] = []

    class _Llm(_FakeLlm):
        async def chat_simple(self, messages, **kwargs):
            seen.append(session.in_transaction())
            return await super().chat_simple(messages, **kwargs)

    await _run(_message("привет"), session, settings, _Llm())

    assert seen == [False]


async def test_no_transaction_is_held_open_during_compression_call(monkeypatch, session):
    settings = _settings(monkeypatch)
    repo = PersonalAiRepository(session)
    for i in range(llm_personal_ai.PERSONAL_CONTEXT_THRESHOLD):
        await repo.add_message(user_id=USER_ID, thread="assistant", role="user", content=f"m{i}")
    seen: list[bool] = []

    class _Llm(_FakeLlm):
        async def summarize(self, messages, **kwargs):
            seen.append(session.in_transaction())
            return await super().summarize(messages, **kwargs)

    await _run(_message("новое"), session, settings, _Llm())

    assert seen == [False]


async def test_reset_during_compression_does_not_resurrect_deleted_messages(monkeypatch, session):
    repo = PersonalAiRepository(session)
    for i in range(llm_personal_ai.PERSONAL_CONTEXT_THRESHOLD):
        await repo.add_message(user_id=USER_ID, thread="assistant", role="user", content=f"m{i}")

    class _Llm(_FakeLlm):
        async def summarize(self, messages, **kwargs):
            await repo.reset_thread(user_id=USER_ID, thread="assistant")  # the user pressed /ai_reset meanwhile
            await session.commit()
            return await super().summarize(messages, **kwargs)

    done = await llm_personal_ai.maybe_compress_personal(
        repo=repo, llm_client=_Llm(), user_id=USER_ID, thread="assistant"
    )

    assert done is False
    assert await repo.latest_summary(user_id=USER_ID, thread="assistant") is None


async def test_blocked_bot_on_final_send_keeps_the_stored_turn(monkeypatch, session):
    from aiogram.exceptions import TelegramForbiddenError

    settings = _settings(monkeypatch)
    message = _message("привет")
    message.thinking.edit_text = AsyncMock(side_effect=RuntimeError("edit failed"))
    message.answer = AsyncMock(side_effect=[message.thinking, TelegramForbiddenError(method=MagicMock(), message="blocked")])

    await _run(message, session, settings, _FakeLlm())

    rows = await PersonalAiRepository(session).recent_messages(user_id=USER_ID, thread="assistant", limit=10)
    assert len(rows) == 2
