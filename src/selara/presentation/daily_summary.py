from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.daily_summary.eligibility import evaluate_daily_summary_eligibility
from selara.application.daily_summary.pipeline import run_daily_summary_pipeline
from selara.application.daily_summary.schedule import compute_scheduled_window_to
from selara.core.config import Settings
from selara.domain.entities import ChatSnapshot, DailySummaryRun
from selara.infrastructure.db.llm_repository import LlmRepository
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.infrastructure.llm.client import LlmClient

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 900  # 15 minutes -- accuracy to the hour isn't critical (see TODO doc)
_LEASE_SECONDS = 1800  # 30 minutes: how long a claim is considered "live" before it can be reclaimed


@dataclass(frozen=True)
class DailySummaryOutcome:
    sent: bool
    reason: str  # "sent" | "not_eligible:<gate>" | "already_run_today" | "claim_lost" | "pipeline_failed" | "send_failed"


def _resolve_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown BOT_TIMEZONE=%s for daily summary, falling back to UTC", timezone_name)
        return ZoneInfo("UTC")


async def _fetch_glossary_terms(session: AsyncSession, *, chat_id: int) -> list[tuple[str, str]]:
    try:
        rows = await LlmRepository(session).list_glossary(chat_id=chat_id)
        return [(row.term, row.definition) for row in rows]
    except Exception:
        logger.exception("daily summary chat_id=%s: failed to load glossary, continuing without it", chat_id)
        return []


async def _generate_and_finalize(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    llm_client: LlmClient,
    chat: ChatSnapshot,
    style: str,
    persona_enabled: bool,
    run_id: int,
    window_from: datetime,
    window_to: datetime,
) -> bool:
    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        glossary_terms = await _fetch_glossary_terms(session, chat_id=chat.telegram_chat_id)

        try:
            output = await run_daily_summary_pipeline(
                llm_client=llm_client,
                repo=repo,
                chat_id=chat.telegram_chat_id,
                chat_title=chat.title or "Чат",
                summary_run_id=run_id,
                window_from=window_from,
                window_to=window_to,
                style=style,
                persona_enabled=persona_enabled,
                glossary_terms=glossary_terms,
            )
        except Exception as exc:
            logger.exception("daily summary chat_id=%s run_id=%s: pipeline failed", chat.telegram_chat_id, run_id)
            await repo.mark_daily_summary_run_failed(run_id=run_id, error=str(exc))
            await session.commit()
            return False

        for usage in output.stage_usages:
            await repo.record_llm_usage(
                summary_run_id=run_id,
                chat_id=chat.telegram_chat_id,
                feature="daily_summary",
                stage=usage.stage,
                model=usage.model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                estimated_cost_usd=usage.estimated_cost_usd,
            )

        context_stt_cost = await repo.sum_context_stt_cost_in_window(
            chat_id=chat.telegram_chat_id, window_from=window_from, window_to=window_to
        )
        await repo.finalize_daily_summary_run_generated(
            run_id=run_id,
            generated_text=output.generated_text,
            topics_json=output.topics_json,
            diagnostics_json=asdict(output.diagnostics),
            pipeline_cost_usd=output.pipeline_cost_usd,
            context_stt_cost_usd=context_stt_cost,
        )
        await session.commit()
    return True


async def _send_and_mark(
    *,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    chat_id: int,
    run_id: int,
) -> bool:
    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        run = await repo.get_daily_summary_run_by_id(run_id=run_id)
        if run is None or not run.generated_text:
            return False

        try:
            await bot.send_message(chat_id=chat_id, text=run.generated_text, disable_web_page_preview=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("daily summary chat_id=%s run_id=%s: failed to send", chat_id, run_id)
            await repo.mark_daily_summary_run_send_failed(run_id=run_id, error=str(exc))
            await session.commit()
            return False

        await repo.mark_daily_summary_run_sent(run_id=run_id, sent_at=datetime.now(timezone.utc))
        await session.commit()
    return True


async def attempt_daily_summary_run(
    *,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    llm_client: LlmClient,
    chat: ChatSnapshot,
    trigger: str,
    window_to: datetime,
    summary_date,
    now_utc: datetime,
) -> DailySummaryOutcome:
    """One claim -> generate -> send cycle for one chat, for either trigger.

    Safe to call repeatedly (e.g. every scheduler tick, or a repeated `/summary`):
    an already-`sent`/`failed` run is a no-op, an already-`generated` run is just
    resent without re-running the LLM pipeline, and a live claim held by another
    concurrent caller is left alone (see `claim_daily_summary_run`).
    """
    window_from = window_to - timedelta(hours=24)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        chat_settings = await repo.get_chat_settings(chat_id=chat.telegram_chat_id)
        if chat_settings is None:
            return DailySummaryOutcome(False, "not_eligible:no_settings")

        existing = await repo.get_daily_summary_run(chat_id=chat.telegram_chat_id, summary_date=summary_date, trigger=trigger)
        if existing is not None and existing.status in ("sent", "failed"):
            return DailySummaryOutcome(False, "already_run_today")
        if existing is not None and existing.status == "generated":
            sent = await _send_and_mark(bot=bot, session_factory=session_factory, chat_id=chat.telegram_chat_id, run_id=existing.id)
            return DailySummaryOutcome(sent, "sent" if sent else "send_failed")

        message_count = await repo.count_archived_messages_in_window(
            chat_id=chat.telegram_chat_id, window_from=window_from, window_to=window_to
        )
        # `/summary` (trigger="manual") works regardless of whether the daily
        # automation toggle is on -- only save_message/lock/threshold gate it. The
        # scheduled path is the only one that must respect daily_summary_enabled.
        eligibility_settings = (
            chat_settings if trigger == "scheduled" else replace(chat_settings, daily_summary_enabled=True)
        )
        eligibility = evaluate_daily_summary_eligibility(
            settings=eligibility_settings,
            message_count_in_window=message_count,
            already_run_today=existing is not None,  # a live 'claimed'/'generating'/'send_failed' row
        )
        if not eligibility.eligible:
            return DailySummaryOutcome(False, f"not_eligible:{eligibility.reason}")

        run: DailySummaryRun | None = await repo.claim_daily_summary_run(
            chat=chat,
            summary_date=summary_date,
            window_from=window_from,
            window_to=window_to,
            trigger=trigger,
            lease_seconds=_LEASE_SECONDS,
            now=now_utc,
        )
        await session.commit()

    if run is None:
        return DailySummaryOutcome(False, "claim_lost")

    generated = await _generate_and_finalize(
        session_factory=session_factory,
        llm_client=llm_client,
        chat=chat,
        style=chat_settings.daily_summary_style,
        persona_enabled=chat_settings.persona_enabled,
        run_id=run.id,
        window_from=window_from,
        window_to=window_to,
    )
    if not generated:
        return DailySummaryOutcome(False, "pipeline_failed")

    sent = await _send_and_mark(bot=bot, session_factory=session_factory, chat_id=chat.telegram_chat_id, run_id=run.id)
    return DailySummaryOutcome(sent, "sent" if sent else "send_failed")


class DailySummaryScheduler:
    def __init__(
        self,
        *,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
        llm_client: LlmClient,
        settings: Settings,
    ) -> None:
        self._bot = bot
        self._session_factory = session_factory
        self._llm_client = llm_client
        self._settings = settings

    async def run_once(self, *, now: datetime | None = None) -> int:
        now_utc = now or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        async with self._session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            chats = await repo.list_chats_with_daily_summary_enabled()

        local_tz = _resolve_timezone(self._settings.bot_timezone)
        sent_count = 0
        for chat in chats:
            try:
                if await self._process_chat(chat=chat, now_utc=now_utc, local_tz=local_tz):
                    sent_count += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Daily summary dispatch failed", extra={"chat_id": chat.telegram_chat_id})
        return sent_count

    async def _process_chat(self, *, chat: ChatSnapshot, now_utc: datetime, local_tz: ZoneInfo) -> bool:
        async with self._session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            chat_settings = await repo.get_chat_settings(chat_id=chat.telegram_chat_id)
        if chat_settings is None or not chat_settings.daily_summary_enabled:
            return False

        now_local = now_utc.astimezone(local_tz)
        window_to_local = compute_scheduled_window_to(hour=chat_settings.daily_summary_hour, now_local=now_local)
        window_to = window_to_local.astimezone(timezone.utc)
        summary_date = window_to_local.date()

        outcome = await attempt_daily_summary_run(
            bot=self._bot,
            session_factory=self._session_factory,
            llm_client=self._llm_client,
            chat=chat,
            trigger="scheduled",
            window_to=window_to,
            summary_date=summary_date,
            now_utc=now_utc,
        )
        return outcome.sent


async def run_daily_summary_scheduler(
    *,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    llm_client: LlmClient,
    settings: Settings,
) -> None:
    scheduler = DailySummaryScheduler(bot=bot, session_factory=session_factory, llm_client=llm_client, settings=settings)
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        try:
            await scheduler.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Daily summary scheduler iteration failed")
