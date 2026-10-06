from __future__ import annotations

import hashlib
import logging
from dataclasses import replace
from decimal import Decimal

from sqlalchemy import case, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.feature_access import (
    AccessReason,
    AccessTier,
    FeatureAccessDecision,
    FeatureQuotaPolicy,
    FeatureUsageSummary,
    QuotaScope,
    QuotaScopeType,
)
from selara.application.usage_pricing import QuotaCost
from selara.infrastructure.db.models import (
    AiFeatureInvocationModel,
    AiFeatureQuotaUsageModel,
    ChatModel,
    LlmUsageLogModel,
    UserModel,
)
from selara.infrastructure.llm.features import AiFeature

logger = logging.getLogger(__name__)


def feature_quota_lock_key(*, pool_key: str, scope_type: str, scope_id: int, period_start) -> int:
    """Advisory lock key of one pool/scope/calendar-period bucket.

    Chat buckets keep the pre-scope payload so they still serialize against
    the previous release's reservations during a rolling deploy.
    """
    if str(scope_type) == QuotaScopeType.CHAT.value:
        payload = f"{pool_key}\0{scope_id}\0{period_start.isoformat()}"
    else:
        payload = f"{pool_key}\0{scope_type}\0{scope_id}\0{period_start.isoformat()}"
    return int.from_bytes(hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest(), "big", signed=True)


def quota_usage_lock_key(usage) -> int:
    """Lock key of the bucket one stored reservation counts against."""
    return feature_quota_lock_key(
        pool_key=usage.pool_key,
        scope_type=usage.quota_scope_type,
        scope_id=usage.quota_scope_id or usage.chat_id or 0,
        period_start=usage.period_start,
    )


def _as_number(value: Decimal) -> int | float:
    """Report whole unit totals as ints (all of today's limits) and fractions as floats."""
    return int(value) if value == value.to_integral_value() else float(value)


def feature_quota_idempotency_lock_key(*, idempotency_key: str) -> int:
    payload = f"quota-idempotency\0{idempotency_key}".encode("utf-8")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big", signed=True)


class SqlAlchemyFeatureQuotaRepository:
    """PostgreSQL-backed quota reservation and usage history.

    The transaction advisory lock serializes each pool/scope/calendar-period
    check (the scope is a chat or, for personal features, a user). The usage event and logical AI invocation are inserted in the same
    transaction, so neither can commit without the other.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def reserve(
        self,
        *,
        policy: FeatureQuotaPolicy,
        access_tier: AccessTier,
        chat_id: int,
        scope: QuotaScope,
        cost: QuotaCost,
        chat_type: str,
        chat_title: str | None,
        actor_user_id: int | None,
        actor_is_bot: bool,
        trigger: str,
        mode: str | None,
        source_message_id: int | None,
        summary_run_id: int | None,
        idempotency_key: str,
        owner_exempt: bool,
        period_start,
        period_end,
        model_profile: str | None = None,
    ) -> FeatureAccessDecision:
        async with self._session_factory() as session:
            async with session.begin():
                if session.bind is None or session.bind.dialect.name != "postgresql":
                    raise RuntimeError("Feature quota reservations require PostgreSQL advisory locks")
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": feature_quota_idempotency_lock_key(idempotency_key=idempotency_key)},
                )
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": self._lock_key(
                        pool_key=policy.pool,
                        scope_type=scope.scope_type.value,
                        scope_id=scope.scope_id,
                        period_start=period_start,
                    )},
                )
                existing = await session.scalar(
                    select(AiFeatureQuotaUsageModel).where(
                        AiFeatureQuotaUsageModel.idempotency_key == idempotency_key,
                    ).with_for_update()
                )
                if (
                    existing is None
                    and policy.feature == AiFeature.DAILY_SUMMARY
                    and trigger == "manual"
                    and summary_run_id is not None
                ):
                    recovered_rows = (
                        await session.scalars(
                            select(AiFeatureQuotaUsageModel)
                            .join(
                                AiFeatureInvocationModel,
                                AiFeatureInvocationModel.id == AiFeatureQuotaUsageModel.invocation_id,
                            )
                            .where(
                                AiFeatureQuotaUsageModel.feature == policy.feature.value,
                                AiFeatureQuotaUsageModel.trigger == "manual",
                                AiFeatureInvocationModel.summary_run_id == summary_run_id,
                            )
                            .order_by(AiFeatureQuotaUsageModel.id)
                            .limit(2)
                            .with_for_update(of=AiFeatureQuotaUsageModel)
                        )
                    ).all()
                    if len(recovered_rows) > 1:
                        logger.error(
                            "Multiple quota reservations match manual Daily Summary run chat_id=%s summary_run_id=%s",
                            chat_id, summary_run_id,
                        )
                        raise RuntimeError(
                            "Multiple quota reservations match one manual Daily Summary run"
                        )
                    if recovered_rows:
                        existing = recovered_rows[0]
                        logger.info(
                            "Manual Daily Summary reservation recovered by run identity chat_id=%s summary_run_id=%s usage_id=%s",
                            chat_id, summary_run_id, existing.id,
                        )
                if existing is not None:
                    if existing.feature != policy.feature.value:
                        raise RuntimeError("Feature quota idempotency key was reused across features")
                    if existing.chat_id not in (None, chat_id) or self._scope_mismatch(existing, scope):
                        logger.warning(
                            "Feature quota idempotency key reused in another chat or scope feature=%s old_chat_id=%s chat_id=%s",
                            policy.feature.value, existing.chat_id, chat_id,
                        )
                        return self._decision(
                            allowed=False,
                            policy=policy,
                            scope=scope,
                            access_tier=access_tier,
                            owner_exempt=False,
                            used=None,
                            period_start=period_start,
                            period_end=period_end,
                            invocation_id=existing.invocation_id,
                            usage_id=existing.id,
                            reused=True,
                            reason=AccessReason.DUPLICATE_REQUEST,
                        )

                    reacquire_invocation = None
                    if (
                        existing.status == "released"
                        and policy.feature == AiFeature.DAILY_SUMMARY
                        and trigger == "manual"
                        and summary_run_id is not None
                    ):
                        reacquire_invocation = await session.scalar(
                            select(AiFeatureInvocationModel)
                            .where(AiFeatureInvocationModel.id == existing.invocation_id)
                            .with_for_update()
                        )
                        if (
                            reacquire_invocation is None
                            or reacquire_invocation.summary_run_id != summary_run_id
                        ):
                            reacquire_invocation = None

                    if existing.status == "released" and reacquire_invocation is not None:
                        invocation = reacquire_invocation
                        provider_attempts = await session.scalar(
                            select(func.count(LlmUsageLogModel.id)).where(
                                LlmUsageLogModel.invocation_id == existing.invocation_id,
                            )
                        )
                        if (
                            invocation is None
                            or invocation.provider_attempt_started_at is not None
                            or provider_attempts
                        ):
                            logger.warning(
                                "Released feature quota cannot be reacquired after provider start feature=%s chat_id=%s invocation_id=%s",
                                policy.feature.value, chat_id, existing.invocation_id,
                            )
                            return self._decision(
                                allowed=False,
                                policy=policy,
                                scope=scope,
                                access_tier=AccessTier(existing.access_tier),
                                owner_exempt=False,
                                used=None,
                                period_start=period_start,
                                period_end=period_end,
                                invocation_id=existing.invocation_id,
                                usage_id=existing.id,
                                reused=True,
                                reason=AccessReason.DUPLICATE_REQUEST,
                            )

                        used = await self._count_used(
                            session,
                            pool_key=policy.pool,
                            scope=scope,
                            period_start=period_start,
                        )
                        if not owner_exempt and used + cost.units > policy.limit:
                            logger.info(
                                "Released feature quota reacquire denied feature=%s chat_id=%s used=%s limit=%s",
                                policy.feature.value, chat_id, used, policy.limit,
                            )
                            return self._decision(
                                allowed=False,
                                policy=policy,
                                scope=scope,
                                access_tier=access_tier,
                                owner_exempt=False,
                                used=used,
                                period_start=period_start,
                                period_end=period_end,
                                invocation_id=existing.invocation_id,
                                usage_id=existing.id,
                                reused=True,
                                reason=AccessReason.QUOTA_EXHAUSTED,
                            )

                        existing.chat_id = chat_id
                        existing.quota_scope_type = scope.scope_type.value
                        existing.quota_scope_id = scope.scope_id
                        existing.pool_key = policy.pool
                        existing.units = cost.units
                        existing.model_profile = model_profile
                        existing.period_start = period_start
                        existing.period_end = period_end
                        existing.policy_key = policy.policy_key
                        existing.quota_limit = policy.limit
                        existing.created_at = func.now()
                        existing.access_tier = access_tier.value
                        existing.owner_exempt = owner_exempt
                        existing.status = "consumed"
                        existing.release_reason = None
                        existing.released_at = None
                        invocation.chat_id = chat_id
                        invocation.scope_type = scope.scope_type.value
                        invocation.scope_id = str(scope.scope_id)
                        invocation.status = "running"
                        invocation.error_category = None
                        invocation.started_at = func.now()
                        invocation.completed_at = None
                        current_used = None if owner_exempt else used + cost.units
                        decision = self._decision(
                            allowed=True,
                            policy=policy,
                            scope=scope,
                            access_tier=access_tier,
                            owner_exempt=owner_exempt,
                            used=current_used,
                            period_start=period_start,
                            period_end=period_end,
                            invocation_id=existing.invocation_id,
                            usage_id=existing.id,
                            reused=True,
                            reason=None,
                        )
                        logger.info(
                            "Released feature quota reservation reacquired feature=%s chat_id=%s usage_id=%s invocation_id=%s",
                            policy.feature.value, chat_id, existing.id, existing.invocation_id,
                        )
                        return decision

                    existing_scope = self._row_scope(existing, fallback=scope, chat_id=chat_id)
                    used = await self._count_used(
                        session,
                        pool_key=existing.pool_key or policy.pool,
                        scope=existing_scope,
                        period_start=existing.period_start,
                    )
                    allowed = existing.status == "consumed"
                    existing_policy = FeatureQuotaPolicy(
                        policy.feature, existing.policy_key, existing.quota_limit, policy.period,
                        pool_key=existing.pool_key or policy.pool_key, unit=policy.unit,
                    )
                    decision = self._decision(
                        allowed=allowed,
                        policy=existing_policy,
                        scope=existing_scope,
                        access_tier=AccessTier(existing.access_tier),
                        owner_exempt=existing.owner_exempt,
                        used=used,
                        period_start=existing.period_start,
                        period_end=existing.period_end,
                        invocation_id=existing.invocation_id,
                        usage_id=existing.id,
                        reused=True,
                        reason=None if allowed else AccessReason.DUPLICATE_REQUEST,
                    )
                    logger.info(
                        "Feature quota idempotent reservation reused feature=%s chat_id=%s usage_id=%s",
                        policy.feature.value, chat_id, existing.id,
                    )
                    return decision

                used = await self._count_used(
                    session, pool_key=policy.pool, scope=scope, period_start=period_start,
                )
                if not owner_exempt and used + cost.units > policy.limit:
                    decision = self._decision(
                        allowed=False,
                        policy=policy,
                        scope=scope,
                        access_tier=access_tier,
                        owner_exempt=False,
                        used=used,
                        period_start=period_start,
                        period_end=period_end,
                        invocation_id=None,
                        usage_id=None,
                        reused=False,
                        reason=AccessReason.QUOTA_EXHAUSTED,
                    )
                    logger.info(
                        "Feature quota denied feature=%s chat_id=%s used=%s limit=%s period_start=%s",
                        policy.feature.value, chat_id, used, policy.limit, period_start.isoformat(),
                    )
                    return decision
                if not owner_exempt and policy.per_actor_limit is not None and actor_user_id is not None:
                    # Second count under the same bucket lock: one member cannot drain the shared pool.
                    actor_used = await self._count_used(
                        session, pool_key=policy.pool, scope=scope, period_start=period_start,
                        actor_user_id=actor_user_id,
                    )
                    if actor_used + cost.units > policy.per_actor_limit:
                        logger.info(
                            "Feature quota actor share denied feature=%s chat_id=%s actor_user_id=%s used=%s limit=%s",
                            policy.feature.value, chat_id, actor_user_id, actor_used, policy.per_actor_limit,
                        )
                        return self._decision(
                            allowed=False,
                            policy=replace(policy, limit=policy.per_actor_limit),
                            scope=scope,
                            access_tier=access_tier,
                            owner_exempt=False,
                            used=actor_used,
                            period_start=period_start,
                            period_end=period_end,
                            invocation_id=None,
                            usage_id=None,
                            reused=False,
                            reason=AccessReason.ACTOR_QUOTA_EXHAUSTED,
                        )

                await session.execute(
                    pg_insert(ChatModel)
                    .values(telegram_chat_id=chat_id, type=chat_type, title=chat_title)
                    .on_conflict_do_nothing(index_elements=[ChatModel.telegram_chat_id])
                )
                if actor_user_id is not None:
                    await session.execute(
                        pg_insert(UserModel)
                        .values(telegram_user_id=actor_user_id, is_bot=actor_is_bot)
                        .on_conflict_do_nothing(index_elements=[UserModel.telegram_user_id])
                    )

                invocation = AiFeatureInvocationModel(
                    feature=policy.feature.value,
                    scope_type=scope.scope_type.value,
                    scope_id=str(scope.scope_id),
                    chat_id=chat_id,
                    actor_user_id=actor_user_id,
                    trigger=trigger,
                    mode=mode,
                    status="running",
                    source_message_id=source_message_id,
                    summary_run_id=summary_run_id,
                )
                session.add(invocation)
                await session.flush()
                usage = AiFeatureQuotaUsageModel(
                    feature=policy.feature.value,
                    chat_id=chat_id,
                    quota_scope_type=scope.scope_type.value,
                    quota_scope_id=scope.scope_id,
                    pool_key=policy.pool,
                    units=cost.units,
                    model_profile=model_profile,
                    actor_user_id=actor_user_id,
                    invocation_id=invocation.id,
                    trigger=trigger,
                    source_chat_id=chat_id if source_message_id is not None else None,
                    source_message_id=source_message_id,
                    idempotency_key=idempotency_key,
                    period_start=period_start,
                    period_end=period_end,
                    policy_key=policy.policy_key,
                    quota_limit=policy.limit,
                    access_tier=access_tier.value,
                    owner_exempt=owner_exempt,
                    status="consumed",
                )
                session.add(usage)
                await session.flush()
                current_used = None if owner_exempt else used + cost.units
                decision = self._decision(
                    allowed=True,
                    policy=policy,
                    scope=scope,
                    access_tier=access_tier,
                    owner_exempt=owner_exempt,
                    used=current_used,
                    period_start=period_start,
                    period_end=period_end,
                    invocation_id=invocation.id,
                    usage_id=usage.id,
                    reused=False,
                    reason=None,
                )
            logger.info(
                "Feature quota granted feature=%s chat_id=%s actor_user_id=%s used=%s limit=%s owner_exempt=%s invocation_id=%s",
                policy.feature.value,
                chat_id,
                actor_user_id,
                decision.quota_used,
                decision.quota_limit,
                owner_exempt,
                decision.invocation_id,
            )
            if owner_exempt:
                logger.info(
                    "Feature quota owner exemption applied feature=%s chat_id=%s invocation_id=%s",
                    policy.feature.value, chat_id, decision.invocation_id,
                )
            return decision

    async def usage_summary(
        self,
        *,
        policy: FeatureQuotaPolicy,
        chat_id: int,
        scope: QuotaScope,
        owner_exempt: bool,
        period_start,
        period_end,
    ) -> FeatureUsageSummary:
        async with self._session_factory() as session:
            if session.bind is None or session.bind.dialect.name != "postgresql":
                raise RuntimeError("Feature quota summaries require PostgreSQL")
            used = await self._count_used(
                session, pool_key=policy.pool, scope=scope, period_start=period_start,
            )
        scope_type, scope_id = scope.scope_type.value, str(scope.scope_id)
        if owner_exempt:
            return FeatureUsageSummary(
                policy.feature, scope_type, scope_id, AccessTier.OWNER_INTERNAL,
                None, None, None, period_start, period_end, period_end, True, True, policy.policy_key,
            )
        return FeatureUsageSummary(
            policy.feature,
            scope_type,
            scope_id,
            AccessTier.FREE,
            policy.limit,
            _as_number(used),
            _as_number(max(Decimal(0), Decimal(policy.limit) - used)),
            period_start,
            period_end,
            period_end,
            False,
            False,
            policy.policy_key,
        )

    async def release_if_no_provider_attempts(self, *, invocation_id: int, reason: str) -> bool:
        """Release only before any provider start marker or persisted attempt."""
        async with self._session_factory() as session:
            async with session.begin():
                if session.bind is None or session.bind.dialect.name != "postgresql":
                    raise RuntimeError("Feature quota release requires PostgreSQL")
                usage = await session.scalar(
                    select(AiFeatureQuotaUsageModel)
                    .where(AiFeatureQuotaUsageModel.invocation_id == invocation_id)
                )
                if usage is None or usage.status != "consumed":
                    return False
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": feature_quota_idempotency_lock_key(idempotency_key=usage.idempotency_key)},
                )
                usage = await session.scalar(
                    select(AiFeatureQuotaUsageModel)
                    .where(AiFeatureQuotaUsageModel.invocation_id == invocation_id)
                )
                if usage is None or usage.status != "consumed":
                    return False
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": quota_usage_lock_key(usage)},
                )
                usage = await session.scalar(
                    select(AiFeatureQuotaUsageModel)
                    .where(AiFeatureQuotaUsageModel.invocation_id == invocation_id)
                    .with_for_update()
                )
                if usage is None or usage.status != "consumed":
                    return False
                provider_attempts = await session.scalar(
                    select(func.count(LlmUsageLogModel.id)).where(
                        LlmUsageLogModel.invocation_id == invocation_id,
                    )
                )
                invocation = await session.scalar(
                    select(AiFeatureInvocationModel).where(
                        AiFeatureInvocationModel.id == invocation_id,
                    )
                )
                if provider_attempts or (
                    invocation is not None and invocation.provider_attempt_started_at is not None
                ):
                    return False
                usage.status = "released"
                usage.release_reason = reason[:64]
                usage.released_at = func.now()
                feature = usage.feature
                chat_id = usage.chat_id
            logger.info(
                "Feature quota reservation released before provider attempt feature=%s chat_id=%s invocation_id=%s reason=%s",
                feature, chat_id, invocation_id, reason,
            )
            return True

    @staticmethod
    async def _count_used(
        session, *, pool_key: str, scope: QuotaScope, period_start, actor_user_id: int | None = None
    ) -> Decimal:
        """Units already spent in the pool/scope/period; a request counted once however often it was recorded."""
        logical_request = case(
            (
                AiFeatureQuotaUsageModel.source_message_id.is_not(None),
                func.concat(
                    "telegram-message:",
                    func.coalesce(AiFeatureQuotaUsageModel.source_chat_id, AiFeatureQuotaUsageModel.chat_id),
                    ":",
                    AiFeatureQuotaUsageModel.source_message_id,
                ),
            ),
            else_=func.concat("idempotency-key:", AiFeatureQuotaUsageModel.idempotency_key),
        )
        per_request = (
            select(
                logical_request.label("logical_request"),
                func.max(AiFeatureQuotaUsageModel.units).label("units"),
            )
            .where(
                AiFeatureQuotaUsageModel.pool_key == pool_key,
                AiFeatureQuotaUsageModel.quota_scope_type == scope.scope_type.value,
                AiFeatureQuotaUsageModel.quota_scope_id == scope.scope_id,
                AiFeatureQuotaUsageModel.period_start == period_start,
                AiFeatureQuotaUsageModel.owner_exempt.is_(False),
                AiFeatureQuotaUsageModel.status == "consumed",
                *(
                    (AiFeatureQuotaUsageModel.actor_user_id == actor_user_id,)
                    if actor_user_id is not None
                    else ()
                ),
            )
            .group_by(logical_request)
            .subquery()
        )
        total = await session.scalar(select(func.coalesce(func.sum(per_request.c.units), 0)))
        return Decimal(total or 0)

    @staticmethod
    def _scope_mismatch(existing: AiFeatureQuotaUsageModel, scope: QuotaScope) -> bool:
        if existing.quota_scope_type == "legacy_orphan":
            return False
        return (
            existing.quota_scope_type != scope.scope_type.value
            or existing.quota_scope_id != scope.scope_id
        )

    @staticmethod
    def _row_scope(existing: AiFeatureQuotaUsageModel, *, fallback: QuotaScope, chat_id: int) -> QuotaScope:
        if existing.quota_scope_type == QuotaScopeType.USER.value and existing.quota_scope_id is not None:
            return QuotaScope.user(existing.quota_scope_id)
        if existing.quota_scope_type == QuotaScopeType.CHAT.value and existing.quota_scope_id is not None:
            return QuotaScope.chat(existing.quota_scope_id)
        # legacy_orphan: the chat is gone, report the requested scope
        return fallback

    @staticmethod
    def _decision(
        *,
        allowed: bool,
        policy: FeatureQuotaPolicy,
        scope: QuotaScope,
        access_tier: AccessTier,
        owner_exempt: bool,
        used: Decimal | int | None,
        period_start,
        period_end,
        invocation_id: int | None,
        usage_id: int | None,
        reused: bool,
        reason: AccessReason | None,
    ) -> FeatureAccessDecision:
        used_units = None if used is None else Decimal(used)
        return FeatureAccessDecision(
            allowed=allowed,
            feature=policy.feature,
            scope_type=scope.scope_type.value,
            scope_id=str(scope.scope_id),
            access_tier=access_tier,
            quota_limit=None if owner_exempt else policy.limit,
            quota_used=None if owner_exempt or used_units is None else _as_number(used_units),
            quota_remaining=None
            if owner_exempt
            else _as_number(max(Decimal(0), Decimal(policy.limit) - (used_units or Decimal(0)))),
            period_start=period_start,
            period_end=period_end,
            reason=reason,
            owner_exempt=owner_exempt,
            invocation_id=invocation_id,
            quota_usage_id=usage_id,
            policy_key=policy.policy_key,
            reused=reused,
        )

    @staticmethod
    def _lock_key(*, pool_key: str, scope_type: str, scope_id: int, period_start) -> int:
        return feature_quota_lock_key(
            pool_key=pool_key, scope_type=scope_type, scope_id=scope_id, period_start=period_start,
        )
