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
from selara.application.feature_access import (
    FeatureAccessDecision,
    FeatureAccessService,
    message_idempotency_key,
)
from selara.core.config import Settings, get_settings
from selara.domain.entities import ChatSnapshot, DailySummaryRun
from selara.domain.glossary import GlossaryEntry
from selara.infrastructure.db.artifact_repository import ArtifactRepository
from selara.infrastructure.db.ai_accounting import AiAccountingService
from selara.infrastructure.llm.artifact_tools import ArtifactRequestContext, deliver_artifact
from selara.infrastructure.llm.tools import ToolCall
from selara.presentation.llm_formatting import split_telegram_html
from selara.infrastructure.db.llm_repository import LlmRepository
from selara.infrastructure.db.feature_quota import SqlAlchemyFeatureQuotaRepository
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.infrastructure.llm.client import LlmClient
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.auth import resolve_owner_admin_exemption
from selara.presentation.feature_access_messages import quota_exhausted_message

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 900  # 15 minutes -- accuracy to the hour isn't critical (see TODO doc)
_LEASE_SECONDS = 1800  # 30 minutes: how long a claim is considered "live" before it can be reclaimed


@dataclass(frozen=True)
class DailySummaryOutcome:
    sent: bool
    reason: str  # "sent" | "not_eligible:<gate>" | "already_run_today" | "claim_lost" | "pipeline_failed" | "send_failed"
    access_decision: FeatureAccessDecision | None = None


def _resolve_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown BOT_TIMEZONE=%s for daily summary, falling back to UTC", timezone_name)
        return ZoneInfo("UTC")


async def _fetch_glossary_terms(session: AsyncSession, *, chat_id: int) -> list[GlossaryEntry]:
    try:
        rows = await LlmRepository(session).list_glossary(chat_id=chat_id)
        return [GlossaryEntry(row.term, row.definition, tuple(a.alias for a in row.aliases)) for row in rows]
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
    trigger: str,
    actor_user_id: int | None,
    window_from: datetime,
    window_to: datetime,
    invocation_id: int | None = None,
    quota_access_service: FeatureAccessService | None = None,
) -> bool:
    accounting = getattr(llm_client, "accounting_service", None)
    if invocation_id is None and accounting is not None:
        invocation_id = await accounting.create_invocation(
            feature=AiFeature.DAILY_SUMMARY, trigger=trigger, chat_id=chat.telegram_chat_id,
            actor_user_id=actor_user_id, summary_run_id=run_id,
        )
    elif invocation_id is not None and accounting is None:
        accounting = AiAccountingService(session_factory)
    outcome = {"status": "failed", "error_category": "pipeline_failed"}
    try:
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
                    invocation_id=invocation_id,
                    window_from=window_from,
                    window_to=window_to,
                    style=style,
                    persona_enabled=persona_enabled,
                    glossary_terms=glossary_terms,
                    artifact_context=ArtifactRequestContext(
                        repository=ArtifactRepository(session), renderer_url=get_settings().artifact_renderer_url,
                        chat_id=chat.telegram_chat_id, creator_id=0, message_id=None, summary_run_id=run_id,
                    ),
                )
            except Exception as exc:
                logger.exception("daily summary chat_id=%s run_id=%s: pipeline failed", chat.telegram_chat_id, run_id)
                aggregate = await accounting.aggregate_invocation(invocation_id=invocation_id) if accounting and invocation_id else None
                await repo.mark_daily_summary_run_failed(
                    run_id=run_id, error=str(exc),
                    pipeline_cost_usd=aggregate.known_cost_usd if aggregate else None,
                    pipeline_has_unknown_cost=aggregate.has_unknown_cost if aggregate else None,
                )
                await session.commit()
                return False

            aggregate = await accounting.aggregate_invocation(invocation_id=invocation_id) if accounting and invocation_id else None
            context_stt_cost = await repo.sum_context_stt_cost_in_window(
                chat_id=chat.telegram_chat_id, window_from=window_from, window_to=window_to
            )
            await repo.finalize_daily_summary_run_generated(
                run_id=run_id,
                generated_text=output.generated_text,
                topics_json=output.topics_json,
                diagnostics_json=asdict(output.diagnostics),
                pipeline_cost_usd=aggregate.known_cost_usd if aggregate else output.pipeline_cost_usd,
                pipeline_has_unknown_cost=aggregate.has_unknown_cost if aggregate else output.has_unknown_cost,
                context_stt_cost_usd=context_stt_cost,
            )
            await session.commit()
        outcome["status"] = "succeeded"
        outcome["error_category"] = None
        return True

    except asyncio.CancelledError:
        outcome["error_category"] = "cancelled"
        raise
    finally:
        if accounting is not None and invocation_id is not None:
            if quota_access_service is not None and outcome["status"] != "succeeded":
                try:
                    await quota_access_service.release_if_no_provider_attempts(
                        invocation_id=invocation_id,
                        reason=outcome["error_category"] or "pre_provider_failure",
                    )
                except Exception:
                    logger.exception("Could not release unused Daily Summary quota invocation_id=%s", invocation_id)
            await accounting.finish_invocation_outcome(
                invocation_id=invocation_id,
                status=outcome["status"],
                error_category=outcome["error_category"],
            )


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
        if run is None or run.chat_id != chat_id or not run.generated_text:
            return False

        try:
            artifact_id = (run.topics_json or {}).get("artifact_id")
            artifact_repo = ArtifactRepository(session)
            artifact = await artifact_repo.get(artifact_id=str(artifact_id), chat_id=chat_id, thread_id=None) if artifact_id else None
            if artifact is not None and artifact.source.get("summary_run_id") == run_id:
                result = await deliver_artifact(
                    call=ToolCall("send_artifact", {"artifact_id": artifact_id}, f"summary-{run_id}"),
                    ctx=ArtifactRequestContext(repository=artifact_repo, renderer_url="", chat_id=chat_id,
                        creator_id=0, message_id=None, summary_run_id=run_id),
                    bot=bot, caption=run.generated_text, caption_is_html=True, fallback_to_text=True,
                )
                if not result.success:
                    raise RuntimeError(result.result_text)
            else:
                for chunk in split_telegram_html(run.generated_text):
                    await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML", disable_web_page_preview=True)
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
    actor_user_id: int | None = None,
    source_message_id: int | None = None,
    settings: Settings | None = None,
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
        if existing is not None and existing.status in ("generated", "send_failed"):
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

    async def _release_unstarted_claim() -> None:
        try:
            async with session_factory() as session:
                repo = SqlAlchemyActivityRepository(session)
                await repo.release_unstarted_daily_summary_claim(run_id=run.id, claimed_at=run.claimed_at)
                await session.commit()
        except Exception:
            logger.exception(
                "Could not release unstarted Daily Summary claim chat_id=%s run_id=%s",
                chat.telegram_chat_id,
                run.id,
            )

    quota_access_service = None
    reserved_invocation_id = None
    access_decision = None
    if trigger == "manual":
        settings = settings or get_settings()
        owner_exempt = await resolve_owner_admin_exemption(
            bot=bot,
            chat_id=chat.telegram_chat_id,
            admin_user_id=settings.admin_user_id,
        )
        quota_access_service = FeatureAccessService(SqlAlchemyFeatureQuotaRepository(session_factory))
        idempotency_key = (
            message_idempotency_key(
                feature=AiFeature.DAILY_SUMMARY,
                chat_id=chat.telegram_chat_id,
                source_message_id=source_message_id,
            )
            if source_message_id is not None
            else f"{AiFeature.DAILY_SUMMARY.value}:{chat.telegram_chat_id}:run:{run.id}"
        )
        try:
            access_decision = await quota_access_service.reserve_feature_usage(
                feature=AiFeature.DAILY_SUMMARY,
                chat_id=chat.telegram_chat_id,
                chat_type=chat.chat_type,
                chat_title=chat.title,
                actor_user_id=actor_user_id,
                trigger="manual",
                timezone_name=settings.bot_timezone,
                idempotency_key=idempotency_key,
                source_message_id=source_message_id,
                summary_run_id=run.id,
                owner_exempt=owner_exempt,
                now=now_utc,
            )
        except Exception:
            logger.exception(
                "Daily Summary access reservation failed chat_id=%s run_id=%s",
                chat.telegram_chat_id,
                run.id,
            )
            await _release_unstarted_claim()
            return DailySummaryOutcome(False, "access_unavailable")

        if not access_decision.allowed or access_decision.reused:
            await _release_unstarted_claim()
            reason = (
                access_decision.reason.value
                if access_decision.reason is not None
                else "duplicate_request"
            )
            return DailySummaryOutcome(False, reason, access_decision)
        reserved_invocation_id = access_decision.invocation_id

    generated = await _generate_and_finalize(
        session_factory=session_factory,
        llm_client=llm_client,
        chat=chat,
        style=chat_settings.daily_summary_style,
        persona_enabled=chat_settings.persona_enabled,
        run_id=run.id,
        trigger=trigger,
        actor_user_id=actor_user_id,
        window_from=window_from,
        window_to=window_to,
        invocation_id=reserved_invocation_id,
        quota_access_service=quota_access_service,
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
            settings=self._settings,
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
