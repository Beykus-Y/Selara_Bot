from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import case, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.infrastructure.db.feature_quota import (
    feature_quota_idempotency_lock_key,
    feature_quota_lock_key,
)
from selara.infrastructure.db.models import AiFeatureInvocationModel, AiFeatureQuotaUsageModel, LlmUsageLogModel
from selara.infrastructure.llm.client import LlmAccountingContext, LlmCallUsage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InvocationAggregate:
    provider_calls: int
    prompt_tokens: int
    completion_tokens: int
    known_cost_usd: Decimal
    has_unknown_cost: bool


@dataclass(frozen=True, slots=True)
class AccountingWindowAggregate:
    invocations: int
    provider_calls: int
    failed_invocations: int
    unknown_cost_calls: int
    known_cost_usd: Decimal
    average_known_cost_component_per_started_invocation_usd: Decimal | None


class AiAccountingService:
    """Persists provider costs in short, independent PostgreSQL transactions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_invocation(
        self,
        *,
        feature: str,
        trigger: str,
        chat_id: int | None,
        actor_user_id: int | None = None,
        mode: str | None = None,
        scope_type: str = "chat",
        scope_id: str | None = None,
        source_message_id: int | None = None,
        summary_run_id: int | None = None,
    ) -> int:
        async with self._session_factory() as session:
            row = AiFeatureInvocationModel(
                feature=feature, trigger=trigger, chat_id=chat_id, actor_user_id=actor_user_id,
                mode=mode, scope_type=scope_type, scope_id=scope_id,
                source_message_id=source_message_id, summary_run_id=summary_run_id,
                status="running",
            )
            session.add(row)
            await session.flush()
            invocation_id = row.id
            await session.commit()
            return invocation_id

    async def create_recovery_invocation_if_provider_started(self, *, invocation_id: int) -> int:
        """Keep an unreported attempt visible when a summary run is reclaimed.

        A provider-start marker is sticky for the lifetime of an invocation. If
        recovery reused that same invocation, later usage rows could hide an
        earlier marker-only attempt in run-level aggregation. Retain the old
        invocation for audit and give the recovered pipeline a fresh one.
        """
        async with self._session_factory() as session:
            async with session.begin():
                quota_usage = await session.scalar(
                    select(AiFeatureQuotaUsageModel).where(
                        AiFeatureQuotaUsageModel.invocation_id == invocation_id,
                    )
                )
                if quota_usage is not None:
                    if session.bind is None or session.bind.dialect.name != "postgresql":
                        raise RuntimeError("Quota-backed recovery requires PostgreSQL advisory locks")
                    await session.execute(
                        text("SELECT pg_advisory_xact_lock(:lock_key)"),
                        {"lock_key": feature_quota_idempotency_lock_key(
                            idempotency_key=quota_usage.idempotency_key,
                        )},
                    )
                    quota_usage = await session.scalar(
                        select(AiFeatureQuotaUsageModel).where(
                            AiFeatureQuotaUsageModel.invocation_id == invocation_id,
                        )
                    )
                    if quota_usage is not None:
                        await session.execute(
                            text("SELECT pg_advisory_xact_lock(:lock_key)"),
                            {"lock_key": feature_quota_lock_key(
                                feature=quota_usage.feature,
                                chat_id=quota_usage.chat_id or 0,
                                period_start=quota_usage.period_start,
                            )},
                        )
                        quota_usage = await session.scalar(
                            select(AiFeatureQuotaUsageModel).where(
                                AiFeatureQuotaUsageModel.invocation_id == invocation_id,
                            ).execution_options(populate_existing=True).with_for_update()
                        )

                previous = await session.scalar(
                    select(AiFeatureInvocationModel).where(
                        AiFeatureInvocationModel.id == invocation_id,
                    ).with_for_update()
                )
                if previous is None:
                    raise RuntimeError(f"AI invocation {invocation_id} does not exist")
                if (
                    previous.provider_attempt_started_at is None
                    or previous.summary_run_id is None
                    or (quota_usage is not None and quota_usage.status != "consumed")
                ):
                    return invocation_id

                summary_run_id = previous.summary_run_id
                recovery = AiFeatureInvocationModel(
                    feature=previous.feature,
                    scope_type=previous.scope_type,
                    scope_id=previous.scope_id,
                    chat_id=previous.chat_id,
                    actor_user_id=previous.actor_user_id,
                    trigger=previous.trigger,
                    mode=previous.mode,
                    status="running",
                    source_message_id=previous.source_message_id,
                    summary_run_id=previous.summary_run_id,
                )
                session.add(recovery)
                await session.flush()
                recovery_invocation_id = recovery.id

        logger.info(
            "AI recovery invocation created after provider start",
            extra={
                "summary_run_id": summary_run_id,
                "previous_invocation_id": invocation_id,
                "recovery_invocation_id": recovery_invocation_id,
            },
        )
        return recovery_invocation_id

    async def mark_provider_attempt_started(self, *, invocation_id: int) -> None:
        """Durably mark an attempt before the network call begins.

        Provider usage rows are deliberately best-effort after a response. This
        separate marker lets quota release distinguish a genuine pre-provider
        failure from a paid attempt whose accounting insert failed.
        """
        async with self._session_factory() as session:
            async with session.begin():
                quota_usage = await session.scalar(
                    select(AiFeatureQuotaUsageModel)
                    .where(AiFeatureQuotaUsageModel.invocation_id == invocation_id)
                )
                if quota_usage is not None:
                    if session.bind is None or session.bind.dialect.name != "postgresql":
                        raise RuntimeError("Quota-backed provider attempts require PostgreSQL")
                    await session.execute(
                        text("SELECT pg_advisory_xact_lock(:lock_key)"),
                        {"lock_key": feature_quota_idempotency_lock_key(
                            idempotency_key=quota_usage.idempotency_key,
                        )},
                    )
                    quota_usage = await session.scalar(
                        select(AiFeatureQuotaUsageModel)
                        .where(AiFeatureQuotaUsageModel.invocation_id == invocation_id)
                    )
                    if quota_usage is None:
                        raise RuntimeError(f"Quota reservation for AI invocation {invocation_id} disappeared")
                    await session.execute(
                        text("SELECT pg_advisory_xact_lock(:lock_key)"),
                        {"lock_key": feature_quota_lock_key(
                            feature=quota_usage.feature,
                            chat_id=quota_usage.chat_id or 0,
                            period_start=quota_usage.period_start,
                        )},
                    )
                    quota_usage = await session.scalar(
                        select(AiFeatureQuotaUsageModel)
                        .where(AiFeatureQuotaUsageModel.invocation_id == invocation_id)
                        .with_for_update()
                    )
                    if quota_usage is None:
                        raise RuntimeError(f"Quota reservation for AI invocation {invocation_id} disappeared")
                    if quota_usage.status != "consumed":
                        raise RuntimeError("Cannot start a provider attempt for released quota")

                invocation = await session.scalar(
                    select(AiFeatureInvocationModel)
                    .where(AiFeatureInvocationModel.id == invocation_id)
                    .with_for_update()
                )
                if invocation is None:
                    raise RuntimeError(f"AI invocation {invocation_id} does not exist")
                if invocation.provider_attempt_started_at is None:
                    invocation.provider_attempt_started_at = datetime.now(timezone.utc)

    async def record_provider_call(self, context: LlmAccountingContext, usage: LlmCallUsage) -> None:
        """Insert or finalize one attempt; retry validation updates the same call_id."""
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(LlmUsageLogModel).where(LlmUsageLogModel.call_id == usage.call_id)
            )
            fields = dict(
                call_id=usage.call_id,
                request_id=usage.request_id,
                invocation_id=context.invocation_id,
                chat_id=context.chat_id,
                feature=context.feature,
                stage=context.stage,
                model=usage.model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                estimated_cost_usd=usage.estimated_cost_usd,
                pricing_status=usage.pricing_status,
                status=usage.status,
                attempt_number=usage.attempt_number,
                error_category=usage.error_category,
                created_at=usage.recorded_at,
            )
            if existing is None:
                session.add(LlmUsageLogModel(**fields))
            else:
                for name, value in fields.items():
                    setattr(existing, name, value)
            await session.commit()
        if usage.pricing_status == "unknown":
            logger.warning(
                "AI invocation has unknown provider cost invocation_id=%s feature=%s stage=%s model=%s",
                context.invocation_id, context.feature, context.stage, usage.model,
            )

    async def finish_invocation(
        self, *, invocation_id: int, status: str, error_category: str | None = None
    ) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(AiFeatureInvocationModel)
                .where(AiFeatureInvocationModel.id == invocation_id)
                .values(status=status, error_category=error_category, completed_at=func.now())
            )
            await session.commit()

    async def finish_invocation_outcome(
        self, *, invocation_id: int, status: str, error_category: str | None = None
    ) -> None:
        """Finalize even during cancellation, preserving partial provider spend."""
        task = asyncio.create_task(self._finish_invocation_outcome(
            invocation_id=invocation_id, status=status, error_category=error_category,
        ))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except Exception:
                logger.exception("Could not finalize AI invocation after cancellation id=%s", invocation_id)
            raise

    async def _finish_invocation_outcome(
        self, *, invocation_id: int, status: str, error_category: str | None
    ) -> None:
        if status == "failed":
            try:
                aggregate = await self.aggregate_invocation(invocation_id=invocation_id)
                if aggregate.provider_calls:
                    status = "partial"
            except Exception:
                # If accounting is temporarily unavailable, don't claim there
                # was no provider spend merely because the read failed.
                logger.exception("Could not inspect AI invocation before finalization id=%s", invocation_id)
                status = "partial"
                error_category = error_category or "accounting_aggregate_unavailable"
        await self.finish_invocation(
            invocation_id=invocation_id, status=status, error_category=error_category,
        )

    async def aggregate_invocation(self, *, invocation_id: int) -> InvocationAggregate:
        async with self._session_factory() as session:
            result = await session.execute(
                select(
                    func.count(LlmUsageLogModel.id),
                    func.coalesce(func.sum(LlmUsageLogModel.prompt_tokens), 0),
                    func.coalesce(func.sum(LlmUsageLogModel.completion_tokens), 0),
                    func.coalesce(func.sum(LlmUsageLogModel.estimated_cost_usd), 0),
                    func.max(case((
                        (LlmUsageLogModel.pricing_status == "unknown") | (LlmUsageLogModel.status == "failed"), 1
                    ), else_=0)),
                ).where(
                    LlmUsageLogModel.invocation_id == invocation_id,
                )
            )
            calls, prompt, completion, cost, has_unknown = result.one()
            provider_started_at = await session.scalar(
                select(AiFeatureInvocationModel.provider_attempt_started_at).where(
                    AiFeatureInvocationModel.id == invocation_id,
                )
            )
            marker_only_attempt = provider_started_at is not None and int(calls) == 0
            return InvocationAggregate(
                int(calls) + int(marker_only_attempt),
                int(prompt),
                int(completion),
                Decimal(cost),
                bool(has_unknown) or marker_only_attempt,
            )

    async def aggregate_summary_run(self, *, summary_run_id: int) -> InvocationAggregate:
        """Aggregate all provider accounting linked to one stable summary run.

        Recovery can create another logical invocation after a worker dies. All
        calls and marker-only attempts still belong to the same DailySummaryRun.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(
                    func.count(LlmUsageLogModel.id),
                    func.coalesce(func.sum(LlmUsageLogModel.prompt_tokens), 0),
                    func.coalesce(func.sum(LlmUsageLogModel.completion_tokens), 0),
                    func.coalesce(func.sum(LlmUsageLogModel.estimated_cost_usd), 0),
                    func.max(case((
                        (LlmUsageLogModel.pricing_status == "unknown") | (LlmUsageLogModel.status == "failed"), 1
                    ), else_=0)),
                )
                .join(
                    AiFeatureInvocationModel,
                    AiFeatureInvocationModel.id == LlmUsageLogModel.invocation_id,
                )
                .where(AiFeatureInvocationModel.summary_run_id == summary_run_id)
            )
            calls, prompt, completion, cost, has_unknown = result.one()
            has_usage = select(LlmUsageLogModel.id).where(
                LlmUsageLogModel.invocation_id == AiFeatureInvocationModel.id,
            ).exists()
            marker_only = await session.scalar(
                select(func.count(AiFeatureInvocationModel.id)).where(
                    AiFeatureInvocationModel.summary_run_id == summary_run_id,
                    AiFeatureInvocationModel.provider_attempt_started_at.is_not(None),
                    ~has_usage,
                )
            )
            marker_only_count = int(marker_only or 0)
            return InvocationAggregate(
                int(calls) + marker_only_count,
                int(prompt),
                int(completion),
                Decimal(cost),
                bool(has_unknown) or marker_only_count > 0,
            )

    async def aggregate_window(self, *, window_from: datetime, window_to: datetime) -> AccountingWindowAggregate:
        """Summarize in-window invocations and their known spend component.

        The average divides known cost by every invocation started in the window,
        including invocations with unknown-priced calls. It is not a complete
        average cost when ``unknown_cost_calls`` is nonzero. ``failed_invocations``
        counts both terminal ``failed`` and ``partial`` outcomes because partial
        invocations represent unsuccessful feature outcomes after provider spend.
        """
        async with self._session_factory() as session:
            invocation_result = await session.execute(
                select(
                    func.count(AiFeatureInvocationModel.id),
                    func.count(case((
                        AiFeatureInvocationModel.status.in_(("failed", "partial")),
                        AiFeatureInvocationModel.id,
                    ))),
                ).where(
                    AiFeatureInvocationModel.started_at >= window_from,
                    AiFeatureInvocationModel.started_at < window_to,
                )
            )
            invocation_count, failed_count = invocation_result.one()
            usage_result = await session.execute(
                select(
                    func.count(LlmUsageLogModel.id),
                    func.count(case((
                        (LlmUsageLogModel.pricing_status == "unknown") | (LlmUsageLogModel.status == "failed"),
                        LlmUsageLogModel.id,
                    ))),
                    func.coalesce(func.sum(LlmUsageLogModel.estimated_cost_usd), 0),
                ).select_from(LlmUsageLogModel)
                .join(
                    AiFeatureInvocationModel,
                    LlmUsageLogModel.invocation_id == AiFeatureInvocationModel.id,
                )
                .where(
                    AiFeatureInvocationModel.started_at >= window_from,
                    AiFeatureInvocationModel.started_at < window_to,
                )
            )
            calls, unknown_calls, known_cost = usage_result.one()
            known_cost = Decimal(known_cost)
            has_usage = select(LlmUsageLogModel.id).where(
                LlmUsageLogModel.invocation_id == AiFeatureInvocationModel.id,
            ).exists()
            marker_only_count = await session.scalar(
                select(func.count(AiFeatureInvocationModel.id)).where(
                    AiFeatureInvocationModel.started_at >= window_from,
                    AiFeatureInvocationModel.started_at < window_to,
                    AiFeatureInvocationModel.provider_attempt_started_at.is_not(None),
                    ~has_usage,
                )
            )
            calls = int(calls) + int(marker_only_count or 0)
            unknown_calls = int(unknown_calls) + int(marker_only_count or 0)
            average = known_cost / invocation_count if invocation_count else None
            return AccountingWindowAggregate(
                int(invocation_count), int(calls), int(failed_count), int(unknown_calls), known_cost, average
            )

    async def known_cost_by_feature(self, *, window_from: datetime, window_to: datetime) -> dict[str, Decimal]:
        async with self._session_factory() as session:
            rows = await session.execute(
                select(LlmUsageLogModel.feature, func.coalesce(func.sum(LlmUsageLogModel.estimated_cost_usd), 0))
                .where(LlmUsageLogModel.created_at >= window_from, LlmUsageLogModel.created_at < window_to)
                .group_by(LlmUsageLogModel.feature)
            )
            return {feature: Decimal(cost) for feature, cost in rows.all()}

    async def report_provider_attempt(self, context: LlmAccountingContext, usage: LlmCallUsage) -> None:
        await self.record_provider_call(context, usage)
