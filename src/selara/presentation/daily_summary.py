from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.daily_summary.eligibility import evaluate_daily_summary_eligibility
from selara.application.daily_summary.pipeline import run_daily_summary_pipeline
from selara.application.daily_summary.schedule import compute_scheduled_window_to
from selara.application.feature_access import (
    AccessReason,
    AccessTier,
    FeatureAccessDecision,
    FeatureAccessService,
)
from selara.core.config import Settings, get_settings
from selara.domain.entities import ChatSnapshot, DailySummaryRun
from selara.domain.glossary import GlossaryEntry
from selara.infrastructure.db.artifact_repository import ArtifactRepository
from selara.infrastructure.db.ai_accounting import AiAccountingService
from selara.infrastructure.db.models import ChatModel
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
_ACCESS_LOG_KEYS: OrderedDict[tuple[int, date, str], None] = OrderedDict()
_MAX_ACCESS_LOG_KEYS = 4096


@dataclass(frozen=True)
class DailySummaryOutcome:
    sent: bool
    reason: str  # Includes eligibility, access, claim, generation and delivery outcomes.
    access_decision: FeatureAccessDecision | None = None


def _log_scheduled_access_decision(
    *,
    chat_id: int,
    summary_date: date,
    decision: FeatureAccessDecision,
) -> None:
    if decision.allowed and decision.access_tier == AccessTier.OWNER_INTERNAL:
        event = "owner_internal"
        message = "Scheduled Daily Summary access granted: owner internal"
        level = logging.INFO
    elif decision.allowed and decision.access_tier == AccessTier.PAID:
        event = "paid"
        message = "Scheduled Daily Summary access granted: paid entitlement"
        level = logging.INFO
    elif decision.reason == AccessReason.ACCESS_REQUIRED:
        event = "access_required"
        message = "Scheduled Daily Summary skipped: access required"
        level = logging.INFO
    elif decision.reason == AccessReason.ACCESS_UNAVAILABLE:
        event = "access_unavailable"
        message = "Scheduled Daily Summary access unavailable"
        level = logging.WARNING
    else:
        return

    key = (chat_id, summary_date, event)
    if key in _ACCESS_LOG_KEYS:
        _ACCESS_LOG_KEYS.move_to_end(key)
        return
    _ACCESS_LOG_KEYS[key] = None
    if len(_ACCESS_LOG_KEYS) > _MAX_ACCESS_LOG_KEYS:
        _ACCESS_LOG_KEYS.popitem(last=False)

    logger.log(
        level,
        message,
        extra={
            "chat_id": chat_id,
            "summary_date": summary_date.isoformat(),
            "trigger": "scheduled",
            "feature": AiFeature.DAILY_SUMMARY.value,
            "access_reason": decision.reason.value if decision.reason else None,
            "access_tier": decision.access_tier.value,
            "entitlement_source": decision.entitlement_source,
        },
    )


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
    quota_invocation_id: int | None = None,
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
                logger.exception(
                    "Daily Summary pipeline failed",
                    extra={"chat_id": chat.telegram_chat_id, "run_id": run_id, "trigger": trigger},
                )
                aggregate = await accounting.aggregate_summary_run(summary_run_id=run_id) if accounting else None
                await repo.mark_daily_summary_run_failed(
                    run_id=run_id, error=str(exc),
                    pipeline_cost_usd=aggregate.known_cost_usd if aggregate else None,
                    pipeline_has_unknown_cost=aggregate.has_unknown_cost if aggregate else None,
                )
                await session.commit()
                return False

            aggregate = await accounting.aggregate_summary_run(summary_run_id=run_id) if accounting else None
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
                        invocation_id=(quota_invocation_id if quota_invocation_id is not None else invocation_id),
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
            logger.exception(
                "Daily Summary send failed",
                extra={"chat_id": chat_id, "run_id": run_id},
            )
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
    feature_access_service: FeatureAccessService | None = None,
) -> DailySummaryOutcome:
    """One claim -> generate -> send cycle for one chat, for either trigger.

    Safe to call repeatedly (e.g. every scheduler tick, or a repeated `/summary`):
    an already-`sent`/`failed` run is a no-op, an already-`generated` run is just
    resent without re-running the LLM pipeline, and a live claim held by another
    concurrent caller is left alone (see `claim_daily_summary_run`).
    """
    if trigger not in {"manual", "scheduled"}:
        raise ValueError(f"Unsupported Daily Summary trigger: {trigger!r}")
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    if trigger == "scheduled":
        settings = settings or get_settings()
    feature_access_service = feature_access_service or FeatureAccessService(
        SqlAlchemyFeatureQuotaRepository(session_factory)
    )
    window_from = window_to - timedelta(hours=24)
    existing: DailySummaryRun | None = None

    async def _resolve_scheduled_access() -> FeatureAccessDecision:
        owner_exempt = await resolve_owner_admin_exemption(
            bot=bot,
            chat_id=chat.telegram_chat_id,
            admin_user_id=settings.admin_user_id,
        )
        try:
            decision = await feature_access_service.resolve_feature_access(
                feature=AiFeature.DAILY_SUMMARY,
                chat_id=chat.telegram_chat_id,
                trigger="scheduled",
                owner_exempt=owner_exempt,
                now=now_utc,
            )
        except Exception:
            logger.exception(
                "Scheduled Daily Summary access check failed chat_id=%s summary_date=%s",
                chat.telegram_chat_id,
                summary_date,
            )
            decision = FeatureAccessDecision(
                allowed=False,
                feature=AiFeature.DAILY_SUMMARY,
                scope_type="chat",
                scope_id=str(chat.telegram_chat_id),
                access_tier=AccessTier.FREE,
                quota_limit=None,
                quota_used=None,
                quota_remaining=None,
                period_start=None,
                period_end=None,
                reason=AccessReason.ACCESS_UNAVAILABLE,
            )
        _log_scheduled_access_decision(
            chat_id=chat.telegram_chat_id,
            summary_date=summary_date,
            decision=decision,
        )
        return decision

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        chat_settings = await repo.get_chat_settings(chat_id=chat.telegram_chat_id)
        if chat_settings is None:
            return DailySummaryOutcome(False, "not_eligible:no_settings")

        existing = await repo.get_daily_summary_run(
            chat_id=chat.telegram_chat_id,
            summary_date=summary_date,
            trigger=trigger,
        )
        if existing is not None and existing.status in ("sent", "failed"):
            return DailySummaryOutcome(False, "already_run_today")

        is_resend = existing is not None and existing.status in ("generated", "send_failed")
        # Manual /summary remains independent of the automatic toggle. Existing
        # generated summaries keep their resend path and do not need another quota.
        if trigger == "manual" and is_resend:
            logger.info(
                "Daily Summary resend",
                extra={"chat_id": chat.telegram_chat_id, "run_id": existing.id, "trigger": trigger},
            )
            sent = await _send_and_mark(
                bot=bot,
                session_factory=session_factory,
                chat_id=chat.telegram_chat_id,
                run_id=existing.id,
            )
            return DailySummaryOutcome(sent, "sent" if sent else "send_failed")

        # The toggle is only the scheduled execution setting. Check it before
        # access resolution and preserve generated scheduled resends when the
        # user has since turned off message archiving or a write lock is active.
        if trigger == "scheduled" and not chat_settings.daily_summary_enabled:
            return DailySummaryOutcome(False, "not_eligible:disabled")

        if trigger == "scheduled":
            if is_resend:
                access_decision = await _resolve_scheduled_access()
                if not access_decision.allowed:
                    reason = access_decision.reason or AccessReason.ACCESS_REQUIRED
                    return DailySummaryOutcome(False, reason.value, access_decision)
                logger.info(
                    "Daily Summary resend",
                    extra={"chat_id": chat.telegram_chat_id, "run_id": existing.id, "trigger": trigger},
                )
                sent = await _send_and_mark(
                    bot=bot,
                    session_factory=session_factory,
                    chat_id=chat.telegram_chat_id,
                    run_id=existing.id,
                )
                return DailySummaryOutcome(sent, "sent" if sent else "send_failed", access_decision)

        # Run the inexpensive settings gates before entitlement lookup. Archived
        # message counting remains after the premium gate so free chats do not
        # incur even that scheduler work on every polling tick.
        eligibility_settings = (
            chat_settings if trigger == "scheduled" else replace(chat_settings, daily_summary_enabled=True)
        )
        preliminary_eligibility = evaluate_daily_summary_eligibility(
            settings=eligibility_settings,
            message_count_in_window=None,
            already_run_today=False,
        )
        if not preliminary_eligibility.eligible:
            return DailySummaryOutcome(False, f"not_eligible:{preliminary_eligibility.reason}")

        access_decision = None
        if trigger == "scheduled":
            access_decision = await _resolve_scheduled_access()
            if not access_decision.allowed:
                reason = access_decision.reason or AccessReason.ACCESS_REQUIRED
                return DailySummaryOutcome(False, reason.value, access_decision)

        message_count = await repo.count_archived_messages_in_window(
            chat_id=chat.telegram_chat_id,
            window_from=window_from,
            window_to=window_to,
        )
        eligibility = evaluate_daily_summary_eligibility(
            settings=eligibility_settings,
            message_count_in_window=message_count,
            # Completed runs were returned above. In-progress rows must reach
            # the atomic claim so an expired worker lease can be reclaimed.
            already_run_today=False,
        )
        if not eligibility.eligible:
            return DailySummaryOutcome(False, f"not_eligible:{eligibility.reason}", access_decision)

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
        return DailySummaryOutcome(False, "claim_lost", access_decision)

    if (
        existing is not None
        and existing.status in ("claimed", "generating")
        and existing.lease_until <= now_utc
    ):
        logger.info(
            "Daily Summary run reclaimed",
            extra={
                "chat_id": chat.telegram_chat_id,
                "run_id": run.id,
                "trigger": trigger,
                "summary_date": summary_date.isoformat(),
            },
        )

    async def _release_unstarted_claim() -> None:
        try:
            async with session_factory() as session:
                repo = SqlAlchemyActivityRepository(session)
                await repo.release_unstarted_daily_summary_claim(run_id=run.id, claimed_at=run.claimed_at)
                await session.commit()
        except Exception:
            logger.exception(
                "Could not release unstarted Daily Summary claim",
                extra={"chat_id": chat.telegram_chat_id, "run_id": run.id, "trigger": trigger},
            )

    async def _refresh_claimed_run_chat() -> tuple[bool, bool]:
        """Follow a group migration that moved this claimed run to a new chat ID.

        On a run-ID collision the source row has already been merged into the
        canonical run and deleted; this worker stops before it creates an
        invocation against stale chat/run identity.
        """
        nonlocal chat, chat_settings, run
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            current_run = await repo.get_daily_summary_run_by_id(run_id=run.id)
            if current_run is None:
                logger.info(
                    "Daily Summary claim superseded by chat migration",
                    extra={"chat_id": chat.telegram_chat_id, "run_id": run.id, "trigger": trigger},
                )
                return False, False
            changed = current_run.chat_id != chat.telegram_chat_id
            chat_row = await session.get(ChatModel, current_run.chat_id)
            current_settings = await repo.get_chat_settings(chat_id=current_run.chat_id)
            if chat_row is None or current_settings is None:
                logger.warning(
                    "Daily Summary claimed run has no canonical chat settings",
                    extra={"chat_id": current_run.chat_id, "run_id": current_run.id, "trigger": trigger},
                )
                return False, False
            if changed:
                chat = replace(
                    chat,
                    telegram_chat_id=current_run.chat_id,
                    chat_type=chat_row.type,
                    title=chat_row.title,
                )
            chat_settings = current_settings
            run = current_run
            return True, changed

    active, _ = await _refresh_claimed_run_chat()
    if not active:
        await _release_unstarted_claim()
        return DailySummaryOutcome(False, "claim_lost", access_decision)
    post_claim_settings = (
        chat_settings if trigger == "scheduled" else replace(chat_settings, daily_summary_enabled=True)
    )
    post_claim_eligibility = evaluate_daily_summary_eligibility(
        settings=post_claim_settings,
        message_count_in_window=message_count,
        already_run_today=False,
    )
    if not post_claim_eligibility.eligible:
        await _release_unstarted_claim()
        return DailySummaryOutcome(
            False,
            f"not_eligible:{post_claim_eligibility.reason}",
            access_decision,
        )

    quota_access_service = None
    quota_invocation_id = None
    pipeline_invocation_id = None
    if trigger == "manual":
        settings = settings or get_settings()
        owner_exempt = await resolve_owner_admin_exemption(
            bot=bot,
            chat_id=chat.telegram_chat_id,
            admin_user_id=settings.admin_user_id,
        )
        quota_access_service = feature_access_service
        # A manual summary is one stable logical run. Telegram message ID is
        # retained as audit metadata, while recovery/reclaims reuse run.id.
        idempotency_key = f"{AiFeature.DAILY_SUMMARY.value}:run:{run.id}"
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
                "Daily Summary access reservation failed",
                extra={"chat_id": chat.telegram_chat_id, "run_id": run.id, "trigger": trigger},
            )
            await _release_unstarted_claim()
            return DailySummaryOutcome(False, "access_unavailable")

        if not access_decision.allowed:
            if access_decision.reason == AccessReason.QUOTA_EXHAUSTED:
                logger.info(
                    "Manual Daily Summary quota denied",
                    extra={
                        "chat_id": chat.telegram_chat_id,
                        "run_id": run.id,
                        "trigger": trigger,
                        "access_reason": access_decision.reason.value,
                        "quota_used": access_decision.quota_used,
                        "quota_limit": access_decision.quota_limit,
                    },
                )
            await _release_unstarted_claim()
            reason = access_decision.reason.value if access_decision.reason is not None else "duplicate_request"
            return DailySummaryOutcome(False, reason, access_decision)
        quota_invocation_id = access_decision.invocation_id
        pipeline_invocation_id = quota_invocation_id
        if access_decision.reused and quota_invocation_id is not None:
            recovery_accounting = AiAccountingService(session_factory)
            try:
                pipeline_invocation_id = await recovery_accounting.create_recovery_invocation_if_provider_started(
                    invocation_id=quota_invocation_id,
                )
            except Exception:
                logger.exception(
                    "Daily Summary recovery accounting setup failed",
                    extra={"chat_id": chat.telegram_chat_id, "run_id": run.id},
                )
                try:
                    await quota_access_service.release_if_no_provider_attempts(
                        invocation_id=quota_invocation_id,
                        reason="recovery_accounting_setup_failed",
                    )
                except Exception:
                    logger.exception(
                        "Could not release unused quota after recovery accounting setup failure",
                        extra={"chat_id": chat.telegram_chat_id, "run_id": run.id},
                    )
                await _release_unstarted_claim()
                return DailySummaryOutcome(False, "access_unavailable", access_decision)
    else:
        # Revalidate after the claim, immediately before creating the logical
        # invocation. If Telegram migrated the group while resolving access,
        # refresh run/chat identity and resolve access for the canonical ID.
        for _ in range(3):
            if not chat_settings.daily_summary_enabled:
                await _release_unstarted_claim()
                return DailySummaryOutcome(False, "not_eligible:disabled", access_decision)
            previous_chat_id = chat.telegram_chat_id
            access_decision = await _resolve_scheduled_access()
            if not access_decision.allowed:
                await _release_unstarted_claim()
                reason = access_decision.reason or AccessReason.ACCESS_REQUIRED
                return DailySummaryOutcome(False, reason.value, access_decision)
            active, _ = await _refresh_claimed_run_chat()
            if not active:
                await _release_unstarted_claim()
                return DailySummaryOutcome(False, "claim_lost", access_decision)
            current_eligibility = evaluate_daily_summary_eligibility(
                settings=chat_settings,
                message_count_in_window=message_count,
                already_run_today=False,
            )
            if not current_eligibility.eligible:
                await _release_unstarted_claim()
                return DailySummaryOutcome(
                    False,
                    f"not_eligible:{current_eligibility.reason}",
                    access_decision,
                )
            if chat.telegram_chat_id == previous_chat_id:
                break
        else:
            await _release_unstarted_claim()
            logger.error(
                "Daily Summary chat identity kept changing during access check",
                extra={"chat_id": chat.telegram_chat_id, "run_id": run.id, "trigger": trigger},
            )
            return DailySummaryOutcome(False, "access_unavailable", access_decision)

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
        invocation_id=pipeline_invocation_id,
        quota_invocation_id=quota_invocation_id,
        quota_access_service=quota_access_service,
    )
    if not generated:
        return DailySummaryOutcome(False, "pipeline_failed", access_decision)

    logger.info(
        "Daily Summary generated",
        extra={
            "chat_id": chat.telegram_chat_id,
            "run_id": run.id,
            "trigger": trigger,
            "access_tier": access_decision.access_tier.value if access_decision else None,
        },
    )

    sent = await _send_and_mark(bot=bot, session_factory=session_factory, chat_id=chat.telegram_chat_id, run_id=run.id)
    return DailySummaryOutcome(sent, "sent" if sent else "send_failed", access_decision)


class DailySummaryScheduler:
    def __init__(
        self,
        *,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
        llm_client: LlmClient,
        settings: Settings,
        feature_access_service: FeatureAccessService | None = None,
    ) -> None:
        self._bot = bot
        self._session_factory = session_factory
        self._llm_client = llm_client
        self._settings = settings
        self._feature_access_service = feature_access_service or FeatureAccessService(
            SqlAlchemyFeatureQuotaRepository(session_factory)
        )

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
            feature_access_service=self._feature_access_service,
        )
        return outcome.sent


async def run_daily_summary_scheduler(
    *,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    llm_client: LlmClient,
    settings: Settings,
    feature_access_service: FeatureAccessService | None = None,
) -> None:
    scheduler = DailySummaryScheduler(
        bot=bot,
        session_factory=session_factory,
        llm_client=llm_client,
        settings=settings,
        feature_access_service=feature_access_service,
    )
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        try:
            await scheduler.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Daily summary scheduler iteration failed")
