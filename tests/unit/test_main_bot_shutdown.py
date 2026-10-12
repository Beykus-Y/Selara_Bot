"""Shutdown order of the bot. The gacha drain sends pending results through the bot session, so polling must leave that
session open, and the session may only close after the drain has run."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from selara import main as main_module
from selara.presentation.handlers import text_commands


def _recording(calls: list[str], name: str) -> AsyncMock:
    async def record(*_args, **_kwargs) -> None:
        calls.append(name)

    return AsyncMock(side_effect=record)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        bot_token="test-token",
        web_enabled=False,
        gacha_warmup_enabled=False,
        admin_user_id=None,
        telegram_update_concurrency_limit=40,
        activity_batch_flush_seconds=1.0,
        activity_batch_max_events=100,
        activity_batch_close_grace_seconds=5.0,
        message_archive_retention_days=0,
        web_search_enabled=False,
        web_search_provider="duckduckgo",
        web_search_base_url="",
        web_search_api_key="",
        web_search_searxng_url="",
        web_search_timeout_seconds=5.0,
    )


def _patch_startup(
    monkeypatch: pytest.MonkeyPatch, calls: list[str], *, polling_error: Exception | None = None
) -> AsyncMock:
    """Replaces everything `_run_bot` starts with recorders. Returns the mocked `start_polling`."""

    async def polling(*_args, **_kwargs) -> None:
        calls.append("start_polling")
        if polling_error is not None:
            raise polling_error

    start_polling = AsyncMock(side_effect=polling)
    dispatcher = SimpleNamespace(include_router=lambda _router: None, start_polling=start_polling)
    bot = SimpleNamespace(session=SimpleNamespace(close=_recording(calls, "session.close")))
    batcher = SimpleNamespace(start=AsyncMock(), close=_recording(calls, "activity_batcher.close"))

    monkeypatch.setattr(main_module, "Bot", lambda token: bot)
    monkeypatch.setattr(main_module, "get_achievement_catalog_from_settings", lambda settings: None)
    monkeypatch.setattr(main_module, "ActivityBatcher", lambda **kwargs: batcher)
    monkeypatch.setattr(main_module, "_build_stt_client", lambda settings: None)
    monkeypatch.setattr(main_module, "_build_llm_client", lambda settings, session_factory=None: None)
    monkeypatch.setattr(main_module, "build_web_search_client", lambda **kwargs: None)
    monkeypatch.setattr(main_module, "PaymentSafeDispatcher", lambda: dispatcher)
    monkeypatch.setattr(main_module, "DatabaseAdmission", SimpleNamespace(from_settings=lambda settings: object()))
    monkeypatch.setattr(main_module, "build_router", lambda *args, **kwargs: object())
    monkeypatch.setattr(main_module, "build_personal_config", lambda session_factory, settings: (None, None))
    monkeypatch.setattr(main_module, "run_startup_relationship_cleanup", AsyncMock())
    monkeypatch.setattr(main_module, "sync_bot_commands", AsyncMock())
    monkeypatch.setattr(main_module, "restore_phase_timers", AsyncMock())
    for name in (
        "run_interesting_facts_scheduler",
        "run_chat_member_count_snapshot_scheduler",
        "run_market_expiry_scheduler",
        "run_message_archive_retention_scheduler",
        "_refresh_bot_polling_heartbeat",
        "_log_db_pressure",
    ):
        monkeypatch.setattr(main_module, name, AsyncMock())
    monkeypatch.setattr(main_module, "drain_gacha_background_tasks", _recording(calls, "drain"))
    return start_polling


@pytest.mark.asyncio
async def test_polling_leaves_the_bot_session_open_until_the_gacha_drain_is_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    start_polling = _patch_startup(monkeypatch, calls)

    await main_module._run_bot(_settings(), session_factory=object())

    assert start_polling.await_args.kwargs["close_bot_session"] is False
    assert calls == ["start_polling", "drain", "activity_batcher.close", "session.close"]


@pytest.mark.asyncio
async def test_a_polling_failure_still_drains_before_the_bot_session_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _patch_startup(monkeypatch, calls, polling_error=RuntimeError("polling failed"))

    with pytest.raises(RuntimeError, match="polling failed"):
        await main_module._run_bot(_settings(), session_factory=object())

    assert calls == ["start_polling", "drain", "activity_batcher.close", "session.close"]


def test_stop_grace_period_outlasts_the_gacha_drain() -> None:
    compose = yaml.safe_load((Path(__file__).parents[2] / "docker-compose.yml").read_text(encoding="utf-8"))
    grace = str(compose["services"]["app"]["stop_grace_period"])
    # Docker sends SIGKILL when the grace period ends. The cleanup after the drain (for example the activity
    # batcher's close grace of up to 5 s) also needs time, so the grace period keeps 10 s beyond the drain budget.
    assert grace.endswith("s")
    assert float(grace.removesuffix("s")) >= text_commands._GACHA_SHUTDOWN_DRAIN_SECONDS + 10
