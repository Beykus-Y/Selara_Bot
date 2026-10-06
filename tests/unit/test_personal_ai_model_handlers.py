"""PR 13 handlers: /ai model selection and AI Limits charging in the personal dialogue."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from selara.application.feature_access import AccessReason, AccessTier, FeatureUsageSummary
from selara.application.model_catalog import CatalogModel, CatalogSnapshot, ModelCapabilities, ModelProfile
from selara.application.personal_config import PersonalConfig, StaticPersonalConfigProvider, config_from_settings
from selara.infrastructure.db.personal_ai_repository import PersonalAiRepository
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.handlers import personal_ai as handler
from tests.unit.test_personal_ai_handlers import (  # noqa: F401 - fixtures are reused
    USER_ID,
    _callback,
    _decision,
    _fake_quota,
    _FakeAccess,
    _FakeLlm,
    _message,
    _settings,
    session,
)


def _catalog(*, creative_enabled: bool = True) -> CatalogSnapshot:
    models = tuple(
        CatalogModel(key, model_id, key.title(), capabilities=ModelCapabilities())
        for key, model_id in (("base", "model-basic"), ("a", "model-a"), ("c", "model-c"))
    )
    return CatalogSnapshot(models, (
        ModelProfile("basic", "Базовая", "base", Decimal("1")),
        ModelProfile("analytics", "Аналитик", "a", Decimal("2")),
        ModelProfile("creative", "Творческая", "c", Decimal("5"), enabled=creative_enabled),
    ))


class _CatalogLlm(_FakeLlm):
    default_model = "legacy-model"

    def __init__(self, snapshot: CatalogSnapshot | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.snapshot = snapshot or _catalog()
        self.kwargs: list[dict] = []
        provider = MagicMock()

        async def get():
            return self.snapshot

        provider.get = get
        self.model_catalog = provider

    async def chat_simple(self, messages, **kwargs):
        self.kwargs.append(kwargs)
        return await super().chat_simple(messages, **kwargs)


def _config(settings, *, ail: bool) -> StaticPersonalConfigProvider:
    base = config_from_settings(settings)
    if not ail:
        return StaticPersonalConfigProvider(base)
    from selara.application.personal_config import ail_limits_from

    return StaticPersonalConfigProvider(
        PersonalConfig(base.price_stars, base.duration_days, base.limits, quota_mode="ail",
                       ail_limits=ail_limits_from(10, 100))
    )


async def _chat(message, session, settings, llm, *, ail: bool):
    await handler.personal_chat_handler(
        message, db_session=session, session_factory=MagicMock(), settings=settings,
        personal_config=_config(settings, ail=ail), llm_client=llm,
    )


async def _choose(session, key: str) -> None:
    repo = PersonalAiRepository(session)
    stored = await repo.get_or_create_profile(USER_ID)
    await repo.update_profile(USER_ID, expected_revision=stored.revision, model_profile_key=key)
    await session.commit()


# --- dialogue --------------------------------------------------------------------------------


async def test_ail_turn_reserves_the_profile_multiplier_and_calls_that_exact_model(monkeypatch, session):
    settings = _settings(monkeypatch)
    await _choose(session, "analytics")
    _FakeAccess.decision = _decision(quota_unit="ail", quota_limit=10, quota_used=2, quota_remaining=8)
    llm = _CatalogLlm()

    await _chat(_message("посчитай", message_id=7), session, settings, llm, ail=True)

    call = _FakeAccess.instances[0].reservations[0]
    assert (call["units"], call["model_profile"]) == (Decimal("2"), "analytics")
    resolved = llm.kwargs[0]["resolved_model"]
    assert (resolved.model_id, resolved.profile_key, resolved.ail_multiplier) == ("model-a", "analytics", Decimal("2"))


async def test_requests_mode_keeps_the_legacy_call(monkeypatch, session):
    settings = _settings(monkeypatch)
    await _choose(session, "creative")
    _FakeAccess.decision = _decision(quota_unit="request")
    llm = _CatalogLlm()

    await _chat(_message("привет"), session, settings, llm, ail=False)

    assert "resolved_model" not in llm.kwargs[0]  # no ×5 model for one request
    assert len(llm.chat_calls) == 1


async def test_not_enough_ail_makes_no_provider_call(monkeypatch, session):
    settings = _settings(monkeypatch)
    await _choose(session, "creative")
    _FakeAccess.decision = _decision(
        allowed=False, reason=AccessReason.QUOTA_EXHAUSTED, quota_unit="ail", quota_limit=10, quota_used=7,
        quota_remaining=3, period_end=datetime.now(timezone.utc) + timedelta(hours=3),
    )
    llm = _CatalogLlm()
    message = _message("нарисуй")

    await _chat(message, session, settings, llm, ail=True)

    assert llm.chat_calls == []
    text = message.answer.await_args.args[0]
    assert "нужно 5 AIL, осталось 3" in text and "не списаны" in text and "/ai" in text
    assert message.answer.await_args.kwargs["reply_markup"] is not None  # Free: Selara Personal CTA


async def test_disabled_profile_falls_back_to_basic_with_one_notice(monkeypatch, session):
    settings = _settings(monkeypatch)
    await _choose(session, "creative")
    handler._fallback_notified.clear()
    _FakeAccess.decision = _decision(quota_unit="ail")
    llm = _CatalogLlm(_catalog(creative_enabled=False))

    first = _message("раз", message_id=1)
    await _chat(first, session, settings, llm, ail=True)
    second = _message("два", message_id=2)
    await _chat(second, session, settings, llm, ail=True)

    calls = _FakeAccess.instances
    assert [c.reservations[0]["units"] for c in calls] == [Decimal("1"), Decimal("1")]  # never creative's ×5
    assert llm.kwargs[0]["resolved_model"].model_id == "model-basic"
    notices = [c.args[0] for c in first.answer.await_args_list if "недоступен" in c.args[0]]
    assert len(notices) == 1
    assert not [c for c in second.answer.await_args_list if "недоступен" in c.args[0]]


# --- /ai selector ------------------------------------------------------------------------------


def _deps(settings, *, ail: bool, llm=None):
    return dict(settings=settings, session_factory=MagicMock(), personal_config=_config(settings, ail=ail),
                llm_client=llm or _CatalogLlm())


class _Summary(_FakeAccess):
    async def get_usage_summary(self, **kwargs):
        return FeatureUsageSummary(
            AiFeature.PERSONAL_CHAT, "user", str(USER_ID), AccessTier.FREE, 150, Decimal("77"), Decimal("73"),
            None, None, datetime.now(timezone.utc) + timedelta(hours=2), False, False, "p", quota_unit="ail",
        )


async def test_ai_shows_profile_cost_and_remaining_ail(monkeypatch, session):
    monkeypatch.setattr(handler, "FeatureAccessService", _Summary)
    settings = _settings(monkeypatch)
    await _choose(session, "analytics")
    message = _message("/ai")

    await handler.ai_settings_command(message, session, **_deps(settings, ail=True))

    text = message.answer.await_args.args[0]
    assert "Модель: 🧠 Аналитик" in text and "Стоимость запроса: 2 AIL" in text
    assert "Осталось сегодня: 73 / 150 AIL" in text and "model-a" not in text


async def test_selection_is_saved_and_survives_a_new_repository(monkeypatch, session):
    settings = _settings(monkeypatch)
    await PersonalAiRepository(session).get_or_create_profile(USER_ID)
    query = _callback("pai:model:analytics:0")

    await handler.ai_settings_callback(query, session, **_deps(settings, ail=True))
    await session.commit()

    assert (await PersonalAiRepository(session).get_profile(USER_ID)).model_profile_key == "analytics"


@pytest.mark.parametrize("data", [
    "pai:model:gpt-4o:0", "pai:model:openrouter/vendor/x:0", "pai:model:creative:0", "pai:model:fast:0",
])
async def test_forged_or_unavailable_profiles_are_rejected(monkeypatch, session, data):
    settings = _settings(monkeypatch)
    await PersonalAiRepository(session).get_or_create_profile(USER_ID)
    query = _callback(data)

    await handler.ai_settings_callback(query, session, **_deps(settings, ail=True, llm=_CatalogLlm(_catalog(creative_enabled=False))))

    stored = await PersonalAiRepository(session).get_profile(USER_ID)
    assert stored.model_profile_key == "basic" and stored.revision == 0
    assert "недоступен" in query.answer.await_args.args[0]


async def test_requests_mode_shows_profiles_but_does_not_sell_them_as_one_request(monkeypatch, session):
    settings = _settings(monkeypatch)
    await PersonalAiRepository(session).get_or_create_profile(USER_ID)
    screen = _callback("pai:models:0")
    await handler.ai_settings_callback(screen, session, **_deps(settings, ail=False))
    text = screen.message.edit_text.await_args.args[0]
    assert "станет доступен после включения AI Limits" in text
    buttons = [b.callback_data for row in screen.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
    assert buttons == ["pai:home"]

    pick = _callback("pai:model:creative:0")
    await handler.ai_settings_callback(pick, session, **_deps(settings, ail=False))
    assert (await PersonalAiRepository(session).get_profile(USER_ID)).model_profile_key == "basic"


async def test_repository_never_stores_a_physical_model_id(session):
    repo = PersonalAiRepository(session)
    await repo.get_or_create_profile(USER_ID)
    with pytest.raises(ValueError):
        await repo.update_profile(USER_ID, expected_revision=0, model_profile_key="provider/model-x")
    assert SimpleNamespace(key=(await repo.get_profile(USER_ID)).model_profile_key).key == "basic"
