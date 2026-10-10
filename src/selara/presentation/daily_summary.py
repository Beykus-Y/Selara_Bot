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
from selara.application.daily_summary.pipeline import DailySummaryClaimLost, run_daily_summary_pipeline
from selara.application.daily_summary.schedule import compute_scheduled_window_to, is_stale_scheduled_window
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
from selara.infrastructure.db.telegram_stars import SqlAlchemyChatEntitlementResolver
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.infrastructure.llm.client import LlmClient
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.auth import lookup_owner_admin_status, resolve_owner_admin_exemption
from selara.presentation.feature_access_messages import quota_exhausted_message

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 900  # 15 minutes -- accuracy to the hour isn't critical (see TODO doc)
_LEASE_SECONDS = 1800  # 30 minutes: how long a claim is considered "live" before it can be reclaimed
_SCHEDULED_DELIVERY_MAX_AGE = timedelta(hours=6)  # bounded retry without next-day reports
_ACCESS_LOG_KEYS: OrderedDict[tuple[int, date, str], None] = OrderedDict()
_MAX_ACCESS_LOG_KEYS = 4096
_OWNER_DENIAL_CACHE_TTL = timedelta(hours=2)  # how long a scheduler remembers "owner is not admin here"


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
    claimed_at: datetime,
    trigger: str,
    actor_user_id: int | None,
    window_from: datetime,
    window_to: datetime,
    invocation_id: int | None = None,
    quota_invocation_id: int | None = None,
    quota_access_service: FeatureAccessService | None = None,
) -> bool | None:
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

            async def ensure_claim_owned() -> bool:
                async with session_factory() as ownership_session:
                    ownership_repo = SqlAlchemyActivityRepository(ownership_session)
                    current_run = await ownership_repo.get_daily_summary_run_by_id(run_id=run_id)
                return bool(
                    current_run is not None
                    and current_run.claimed_at == claimed_at
                    and current_run.chat_id == chat.telegram_chat_id
                    and current_run.status in ("claimed", "generating")
                    and current_run.lease_until > datetime.now(timezone.utc)
                )

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
                    claim_check=ensure_claim_owned,
                )
            except DailySummaryClaimLost:
                outcome["error_category"] = "claim_lost"
                return None
            except Exception as exc:
                logger.exception(
                    "Daily Summary pipeline failed",
                    extra={"chat_id": chat.telegram_chat_id, "run_id": run_id, "trigger": trigger},
                )
                aggregate = await accounting.aggregate_summary_run(summary_run_id=run_id) if accounting else None
                updated = await repo.mark_daily_summary_run_failed(
                    run_id=run_id, claimed_at=claimed_at, error=str(exc),
                    pipeline_cost_usd=aggregate.known_cost_usd if aggregate else None,
                    pipeline_has_unknown_cost=aggregate.has_unknown_cost if aggregate else None,
                )
                await session.commit()
                if not updated:
                    outcome["error_category"] = "claim_lost"
                    return None
                return False

            if not await ensure_claim_owned():
                raise DailySummaryClaimLost(f"Daily Summary claim lost before finalizing run {run_id}")

            aggregate = await accounting.aggregate_summary_run(summary_run_id=run_id) if accounting else None
            context_stt_cost = await repo.sum_context_stt_cost_in_window(
                chat_id=chat.telegram_chat_id, window_from=window_from, window_to=window_to
            )
            finalized = await repo.finalize_daily_summary_run_generated(
                run_id=run_id,
                claimed_at=claimed_at,
                generated_text=output.generated_text,
                topics_json=output.topics_json,
                diagnostics_json=asdict(output.diagnostics),
                pipeline_cost_usd=aggregate.known_cost_usd if aggregate else output.pipeline_cost_usd,
                pipeline_has_unknown_cost=aggregate.has_unknown_cost if aggregate else output.has_unknown_cost,
                context_stt_cost_usd=context_stt_cost,
            )
            await session.commit()
            if not finalized:
                outcome["error_category"] = "claim_lost"
                return None
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
    claimed_at: datetime,
) -> bool | None:
    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        run = await repo.get_daily_summary_run_by_id(run_id=run_id)
        if run is None or run.chat_id != chat_id or not run.generated_text:
            return False
        if run.claimed_at != claimed_at:
            return None
        # A provider worker which began on time can still finish very late:
        # check again immediately before reserving Telegram delivery.
        if run.trigger == "scheduled" and is_stale_scheduled_window(
            scheduled_at=run.window_to, now=datetime.now(timezone.utc),
            grace=_SCHEDULED_DELIVERY_MAX_AGE,
        ):
            logger.info("scheduled_skipped_stale_delivery", extra={
                "chat_id": chat_id, "run_id": run_id,
                "summary_date": run.summary_date.isoformat(), "trigger": "scheduled",
                "window_to": run.window_to.isoformat(),
                "reason": "outside_delivery_recovery_window",
            })
            return False

        delivery_claimed_at = await repo.claim_daily_summary_delivery(
            run_id=run_id,
            claimed_at=claimed_at,
            lease_seconds=_LEASE_SECONDS,
        )
        await session.commit()
        if delivery_claimed_at is None:
            return None

        async def delivery_claim_is_current() -> bool:
            return await repo.is_daily_summary_delivery_claim_current(
                run_id=run_id,
                chat_id=chat_id,
                claimed_at=delivery_claimed_at,
            )

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
                    claim_check=delivery_claim_is_current,
                )
                if not result.success:
                    raise RuntimeError(result.result_text)
            else:
                for chunk in split_telegram_html(run.generated_text):
                    if not await delivery_claim_is_current():
                        return None
                    await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML", disable_web_page_preview=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "Daily Summary send failed",
                extra={"chat_id": chat_id, "run_id": run_id},
            )
            updated = await repo.mark_daily_summary_run_send_failed(
                run_id=run_id, claimed_at=delivery_claimed_at, error=str(exc)
            )
            await session.commit()
            if not updated:
                return None
            return False

        updated = await repo.mark_daily_summary_run_sent(
            run_id=run_id, claimed_at=delivery_claimed_at, sent_at=datetime.now(timezone.utc)
        )
        await session.commit()
        if not updated:
            return None
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
    owner_denial_cache: dict[int, datetime] | None = None,
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
        SqlAlchemyFeatureQuotaRepository(session_factory),
        entitlement_resolver=SqlAlchemyChatEntitlementResolver(session_factory),
    )
    window_from = window_to - timedelta(hours=24)
    existing: DailySummaryRun | None = None

    async def _owner_exempt_for_denied_chat() -> bool:
        # Entitlement is resolved first so paid chats never pay for a
        # getChatMember call. Chats that stay denied would repeat the call on
        # every tick, so a negative answer is remembered for a while.
        cached_until = owner_denial_cache.get(chat.telegram_chat_id) if owner_denial_cache is not None else None
        if cached_until is not None and now_utc < cached_until:
            return False
        owner_status = await lookup_owner_admin_status(
            bot=bot,
            chat_id=chat.telegram_chat_id,
            admin_user_id=settings.admin_user_id,
        )
        if owner_denial_cache is not None:
            if owner_status is False:
                owner_denial_cache[chat.telegram_chat_id] = now_utc + _OWNER_DENIAL_CACHE_TTL
            else:
                # Confirmed admin or an unverified lookup: never remember it, retry next tick.
                owner_denial_cache.pop(chat.telegram_chat_id, None)
        return bool(owner_status)

    async def _resolve_scheduled_access() -> FeatureAccessDecision:
        try:
            decision = await feature_access_service.resolve_feature_access(
                feature=AiFeature.DAILY_SUMMARY,
                chat_id=chat.telegram_chat_id,
                trigger="scheduled",
                owner_exempt=False,
                now=now_utc,
            )
            if not decision.allowed and await _owner_exempt_for_denied_chat():
                decision = await feature_access_service.resolve_feature_access(
                    feature=AiFeature.DAILY_SUMMARY,
                    chat_id=chat.telegram_chat_id,
                    trigger="scheduled",
                    owner_exempt=True,
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
        if existing is not None and existing.status == "failed":
            return DailySummaryOutcome(False, "already_failed_today")
        if existing is not None and existing.status == "sent":
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
                claimed_at=existing.claimed_at,
            )
            if sent is None:
                return DailySummaryOutcome(False, "claim_lost")
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
                    claimed_at=existing.claimed_at,
                )
                if sent is None:
                    return DailySummaryOutcome(False, "claim_lost", access_decision)
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
    claim_token = run.claimed_at

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
                await repo.release_unstarted_daily_summary_claim(run_id=run.id, claimed_at=claim_token)
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
            if current_run.claimed_at != claim_token:
                logger.info(
                    "Daily Summary claim superseded by a newer worker",
                    extra={"chat_id": current_run.chat_id, "run_id": current_run.id, "trigger": trigger},
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

    # Recheck ownership after access/quota resolution and immediately before
    # the expensive pipeline. This avoids starting provider work when a newer
    # worker already reclaimed the lease during those checks.
    async with session_factory() as session:
        current_run = await SqlAlchemyActivityRepository(session).get_daily_summary_run_by_id(run_id=run.id)
    if current_run is None or current_run.claimed_at != claim_token:
        if quota_access_service is not None and quota_invocation_id is not None:
            try:
                await quota_access_service.release_if_no_provider_attempts(
                    invocation_id=quota_invocation_id, reason="claim_lost"
                )
            except Exception:
                logger.exception(
                    "Could not release unused quota after Daily Summary claim was lost",
                    extra={"chat_id": chat.telegram_chat_id, "run_id": run.id},
                )
        if pipeline_invocation_id is not None:
            accounting = getattr(llm_client, "accounting_service", None) or AiAccountingService(session_factory)
            try:
                await accounting.finish_invocation_outcome(
                    invocation_id=pipeline_invocation_id,
                    status="failed",
                    error_category="claim_lost",
                )
            except Exception:
                logger.exception(
                    "Could not finish Daily Summary invocation after claim loss",
                    extra={"chat_id": chat.telegram_chat_id, "run_id": run.id},
                )
        return DailySummaryOutcome(False, "claim_lost", access_decision)

    generated = await _generate_and_finalize(
        session_factory=session_factory,
        llm_client=llm_client,
        chat=chat,
        style=chat_settings.daily_summary_style,
        persona_enabled=chat_settings.persona_enabled,
        run_id=run.id,
        claimed_at=claim_token,
        trigger=trigger,
        actor_user_id=actor_user_id,
        window_from=window_from,
        window_to=window_to,
        invocation_id=pipeline_invocation_id,
        quota_invocation_id=quota_invocation_id,
        quota_access_service=quota_access_service,
    )
    if generated is None:
        return DailySummaryOutcome(False, "claim_lost", access_decision)
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

    sent = await _send_and_mark(
        bot=bot,
        session_factory=session_factory,
        chat_id=chat.telegram_chat_id,
        run_id=run.id,
        claimed_at=claim_token,
    )
    if sent is None:
        return DailySummaryOutcome(False, "claim_lost", access_decision)
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
            SqlAlchemyFeatureQuotaRepository(session_factory),
            entitlement_resolver=SqlAlchemyChatEntitlementResolver(session_factory),
        )
        self._owner_denial_cache: dict[int, datetime] = {}

    async def run_once(self, *, now: datetime | None = None) -> int:
        # An explicit `now` pins every chat to the same instant (tests, replays).
        # Otherwise each chat gets a fresh clock: a long tick must not hand late
        # chats a claim whose lease was computed from the start of the tick.
        pinned_now = now
        if pinned_now is not None and pinned_now.tzinfo is None:
            pinned_now = pinned_now.replace(tzinfo=timezone.utc)

        async with self._session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            chats = await repo.list_chats_with_daily_summary_enabled()

        local_tz = _resolve_timezone(self._settings.bot_timezone)
        sent_count = 0
        for chat in chats:
            try:
                chat_now = pinned_now or datetime.now(timezone.utc)
                if await self._process_chat(chat=chat, now_utc=chat_now, local_tz=local_tz):
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

        # The planning function intentionally returns yesterday before today's
        # configured hour. A restart/late tick must not interpret that as
        # permission to start costly *new* LLM generation for yesterday.
        # Existing nonterminal runs keep their established recovery path.
        if is_stale_scheduled_window(scheduled_at=window_to, now=now_utc):
            async with self._session_factory() as session:
                existing = await SqlAlchemyActivityRepository(session).get_daily_summary_run(
                    chat_id=chat.telegram_chat_id,
                    summary_date=summary_date,
                    trigger="scheduled",
                )
            if existing is not None and existing.status in {"claimed", "generating", "generated", "send_failed"} and is_stale_scheduled_window(
                scheduled_at=window_to, now=now_utc,
                grace=_SCHEDULED_DELIVERY_MAX_AGE,
            ):
                logger.info("scheduled_skipped_stale_recovery", extra={
                    "chat_id": chat.telegram_chat_id,
                    "summary_date": summary_date.isoformat(), "trigger": "scheduled",
                    "status": existing.status,
                    "scheduled_at": window_to.isoformat(), "now": now_utc.isoformat(),
                    "reason": "outside_delivery_recovery_window",
                })
                return False
            if existing is None or existing.status in {"sent", "failed"}:
                logger.info(
                    "scheduled_skipped_stale",
                    extra={
                        "chat_id": chat.telegram_chat_id,
                        "summary_date": summary_date.isoformat(),
                        "trigger": "scheduled",
                        "timezone": str(local_tz),
                        "hour": chat_settings.daily_summary_hour,
                        "scheduled_at": window_to.isoformat(),
                        "now": now_utc.isoformat(),
                        "window_from": (window_to - timedelta(days=1)).isoformat(),
                        "window_to": window_to.isoformat(),
                        "reason": "outside_new_run_grace",
                    },
                )
                return False

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
            owner_denial_cache=self._owner_denial_cache,
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
