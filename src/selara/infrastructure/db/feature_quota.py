from __future__ import annotations

import hashlib
import logging

from sqlalchemy import case, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.application.feature_access import (
    AccessReason,
    AccessTier,
    FeatureAccessDecision,
    FeatureQuotaPolicy,
    FeatureUsageSummary,
)
from selara.infrastructure.db.models import (
    AiFeatureInvocationModel,
    AiFeatureQuotaUsageModel,
    ChatModel,
    LlmUsageLogModel,
    UserModel,
)

logger = logging.getLogger(__name__)


class SqlAlchemyFeatureQuotaRepository:
    """PostgreSQL-backed quota reservation and usage history.

    The transaction advisory lock serializes each chat/feature/calendar-period
    check. The usage event and logical AI invocation are inserted in the same
    transaction, so neither can commit without the other.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def reserve(
        self,
        *,
        policy: FeatureQuotaPolicy,
        chat_id: int,
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
    ) -> FeatureAccessDecision:
        async with self._session_factory() as session:
            async with session.begin():
                if session.bind is None or session.bind.dialect.name != "postgresql":
                    raise RuntimeError("Feature quota reservations require PostgreSQL advisory locks")
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": self._lock_key(
                        feature=policy.feature.value, chat_id=chat_id, period_start=period_start,
                    )},
                )
                existing = await session.scalar(
                    select(AiFeatureQuotaUsageModel).where(
                        AiFeatureQuotaUsageModel.idempotency_key == idempotency_key,
                    )
                )
                if existing is None and source_message_id is not None:
                    # A group -> supergroup migration changes the chat_id that
                    # participates in the key. Reuse a migrated event by its
                    # Telegram message identity so redelivery cannot reserve a
                    # second unit under the new chat id.
                    existing = await session.scalar(
                        select(AiFeatureQuotaUsageModel)
                        .where(
                            AiFeatureQuotaUsageModel.feature == policy.feature.value,
                            AiFeatureQuotaUsageModel.chat_id == chat_id,
                            AiFeatureQuotaUsageModel.source_message_id == source_message_id,
                        )
                        .order_by(AiFeatureQuotaUsageModel.id)
                        .limit(1)
                    )
                if existing is not None:
                    if existing.feature != policy.feature.value:
                        raise RuntimeError("Feature quota idempotency key was reused across features")
                    used = await self._count_used(
                        session,
                        feature=policy.feature.value,
                        chat_id=existing.chat_id or chat_id,
                        period_start=existing.period_start,
                    )
                    allowed = existing.status == "consumed"
                    existing_policy = FeatureQuotaPolicy(
                        policy.feature, existing.policy_key, existing.quota_limit, policy.period,
                    )
                    decision = self._decision(
                        allowed=allowed,
                        policy=existing_policy,
                        chat_id=existing.chat_id or chat_id,
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
                    session, feature=policy.feature.value, chat_id=chat_id, period_start=period_start,
                )
                if not owner_exempt and used >= policy.limit:
                    decision = self._decision(
                        allowed=False,
                        policy=policy,
                        chat_id=chat_id,
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
                    scope_type="chat",
                    scope_id=str(chat_id),
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
                    actor_user_id=actor_user_id,
                    invocation_id=invocation.id,
                    trigger=trigger,
                    source_message_id=source_message_id,
                    idempotency_key=idempotency_key,
                    period_start=period_start,
                    period_end=period_end,
                    policy_key=policy.policy_key,
                    quota_limit=policy.limit,
                    access_tier=(AccessTier.OWNER_INTERNAL if owner_exempt else AccessTier.FREE).value,
                    owner_exempt=owner_exempt,
                    status="consumed",
                )
                session.add(usage)
                await session.flush()
                current_used = None if owner_exempt else used + 1
                decision = self._decision(
                    allowed=True,
                    policy=policy,
                    chat_id=chat_id,
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
        owner_exempt: bool,
        period_start,
        period_end,
    ) -> FeatureUsageSummary:
        async with self._session_factory() as session:
            if session.bind is None or session.bind.dialect.name != "postgresql":
                raise RuntimeError("Feature quota summaries require PostgreSQL")
            used = await self._count_used(
                session, feature=policy.feature.value, chat_id=chat_id, period_start=period_start,
            )
        if owner_exempt:
            return FeatureUsageSummary(
                policy.feature, "chat", str(chat_id), AccessTier.OWNER_INTERNAL,
                None, None, None, period_start, period_end, period_end, True, True, policy.policy_key,
            )
        return FeatureUsageSummary(
            policy.feature,
            "chat",
            str(chat_id),
            AccessTier.FREE,
            policy.limit,
            used,
            max(0, policy.limit - used),
            period_start,
            period_end,
            period_end,
            False,
            False,
            policy.policy_key,
        )

    async def release_if_no_provider_attempts(self, *, invocation_id: int, reason: str) -> bool:
        async with self._session_factory() as session:
            async with session.begin():
                if session.bind is None or session.bind.dialect.name != "postgresql":
                    raise RuntimeError("Feature quota release requires PostgreSQL")
                usage = await session.scalar(
                    select(AiFeatureQuotaUsageModel)
                    .where(AiFeatureQuotaUsageModel.invocation_id == invocation_id)
                    .with_for_update()
                )
                if usage is None or usage.status != "consumed":
                    return False
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": self._lock_key(
                        feature=usage.feature, chat_id=usage.chat_id or 0, period_start=usage.period_start,
                    )},
                )
                provider_attempts = await session.scalar(
                    select(func.count(LlmUsageLogModel.id)).where(
                        LlmUsageLogModel.invocation_id == invocation_id,
                    )
                )
                if provider_attempts:
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
    async def _count_used(session, *, feature: str, chat_id: int, period_start) -> int:
        logical_request = case(
            (
                AiFeatureQuotaUsageModel.source_message_id.is_not(None),
                func.concat("telegram-message:", AiFeatureQuotaUsageModel.source_message_id),
            ),
            else_=func.concat("idempotency-key:", AiFeatureQuotaUsageModel.idempotency_key),
        )
        count = await session.scalar(
            select(func.count(func.distinct(logical_request))).where(
                AiFeatureQuotaUsageModel.feature == feature,
                AiFeatureQuotaUsageModel.chat_id == chat_id,
                AiFeatureQuotaUsageModel.period_start == period_start,
                AiFeatureQuotaUsageModel.owner_exempt.is_(False),
                AiFeatureQuotaUsageModel.status == "consumed",
            )
        )
        return int(count or 0)

    @staticmethod
    def _decision(
        *,
        allowed: bool,
        policy: FeatureQuotaPolicy,
        chat_id: int,
        owner_exempt: bool,
        used: int | None,
        period_start,
        period_end,
        invocation_id: int | None,
        usage_id: int | None,
        reused: bool,
        reason: AccessReason | None,
    ) -> FeatureAccessDecision:
        return FeatureAccessDecision(
            allowed=allowed,
            feature=policy.feature,
            scope_type="chat",
            scope_id=str(chat_id),
            access_tier=AccessTier.OWNER_INTERNAL if owner_exempt else AccessTier.FREE,
            quota_limit=None if owner_exempt else policy.limit,
            quota_used=None if owner_exempt else used,
            quota_remaining=None if owner_exempt else max(0, policy.limit - (used or 0)),
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
    def _lock_key(*, feature: str, chat_id: int, period_start) -> int:
        payload = f"{feature}\0{chat_id}\0{period_start.isoformat()}".encode("utf-8")
        return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big", signed=True)
