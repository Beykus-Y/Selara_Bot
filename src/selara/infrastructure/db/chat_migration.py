from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, func, literal, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from selara.domain.glossary import normalize_glossary_text
from selara.infrastructure.db.telegram_stars import entitlement_lock_key

from selara.infrastructure.db.models import (
    AdminRuntimeSettingsModel,
    AiPetEventModel,
    AiPetMemoryModel,
    AiPetMessageModel,
    AiPetModel,
    AiPetRelationshipModel,
    AiFeatureInvocationModel,
    AiFeatureQuotaUsageModel,
    AutoConfigSessionModel,
    ChatEntitlementModel,
    ChatActivityEventSyncStateModel,
    ChatMemberCountSnapshotModel,
    ChatModel,
    ChatSettingsModel,
    ChatTextAliasModel,
    ChatTextAliasSettingsModel,
    EconomyAccountModel,
    EconomyMarketListingModel,
    EconomyPrivateContextModel,
    LlmAdminActionModel,
    LlmArtifactModel,
    LlmChatGlossaryModel,
    LlmChatGlossaryHistoryModel,
    LlmContextMessageModel,
    LlmContextSummaryModel,
    LlmUsageLogModel,
    SelaraAiPurchaseIntentModel,
    DailySummaryRunModel,
    MessageArchiveModel,
    MarriageModel,
    PairModel,
    RelationshipProposalModel,
    UserChatActivityDailyModel,
    UserChatActivityMinuteModel,
    UserChatActivityModel,
    UserChatMessageEventModel,
    UserChatAnnouncementSubscriptionModel,
    UserChatBotRoleModel,
    UserChatModerationStateModel,
    UserChatRestStateModel,
    UserKarmaVoteModel,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatMigrationResult:
    old_chat_id: int
    new_chat_id: int
    migrated: bool
    skipped_account_conflicts: int = 0


async def migrate_chat_id(
    session: AsyncSession,
    *,
    old_chat_id: int,
    new_chat_id: int,
    new_chat_type: str | None = None,
    new_chat_title: str | None = None,
) -> ChatMigrationResult:
    if old_chat_id == new_chat_id:
        return ChatMigrationResult(old_chat_id=old_chat_id, new_chat_id=new_chat_id, migrated=False)

    await _ensure_target_chat(
        session,
        old_chat_id=old_chat_id,
        new_chat_id=new_chat_id,
        new_chat_type=new_chat_type,
        new_chat_title=new_chat_title,
    )

    await _migrate_selara_ai_purchases(
        session,
        old_chat_id=old_chat_id,
        new_chat_id=new_chat_id,
    )

    dialect = session.bind.dialect.name if session.bind else "unknown"
    if dialect == "postgresql":
        skipped_account_conflicts = await _migrate_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    else:
        skipped_account_conflicts = await _migrate_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)

    await session.flush()
    return ChatMigrationResult(
        old_chat_id=old_chat_id,
        new_chat_id=new_chat_id,
        migrated=True,
        skipped_account_conflicts=skipped_account_conflicts,
    )


async def _migrate_selara_ai_purchases(
    session: AsyncSession,
    *,
    old_chat_id: int,
    new_chat_id: int,
) -> None:
    """Retarget pending invoices and merge paid time without losing either balance."""
    await session.execute(
        update(SelaraAiPurchaseIntentModel)
        .where(SelaraAiPurchaseIntentModel.chat_id == old_chat_id)
        .values(chat_id=new_chat_id)
    )

    products = list(
        await session.scalars(
            select(ChatEntitlementModel.product_key).where(ChatEntitlementModel.chat_id == old_chat_id)
        )
    )
    for product_key in products:
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            lock_keys = sorted(
                (
                    entitlement_lock_key(chat_id=old_chat_id, product_key=product_key),
                    entitlement_lock_key(chat_id=new_chat_id, product_key=product_key),
                )
            )
            for lock_key in lock_keys:
                await session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})

        rows = list(
            await session.scalars(
                select(ChatEntitlementModel)
                .where(
                    ChatEntitlementModel.chat_id.in_((old_chat_id, new_chat_id)),
                    ChatEntitlementModel.product_key == product_key,
                )
                .order_by(ChatEntitlementModel.chat_id)
                .with_for_update()
            )
        )
        old_entitlement = next((row for row in rows if row.chat_id == old_chat_id), None)
        new_entitlement = next((row for row in rows if row.chat_id == new_chat_id), None)
        if old_entitlement is None:
            continue
        if new_entitlement is None:
            old_entitlement.chat_id = new_chat_id
            old_entitlement.updated_at = func.now()
            continue

        now = datetime.now(timezone.utc)
        old_until = _as_utc(old_entitlement.valid_until)
        new_until = _as_utc(new_entitlement.valid_until)
        active_rows = [row for row in rows if row.status == "active"]
        remaining_seconds = sum(
            max(0.0, (_as_utc(row.valid_until) - now).total_seconds())
            for row in active_rows
        )
        if active_rows:
            if remaining_seconds > 0:
                new_entitlement.valid_until = now + timedelta(seconds=remaining_seconds)
            else:
                new_entitlement.valid_until = max(_as_utc(row.valid_until) for row in active_rows)
            new_entitlement.status = "active"
            new_entitlement.valid_from = min(_as_utc(row.valid_from) for row in active_rows)
        else:
            # Revoked payment time is not an active balance and must not be
            # reactivated by merging it into an expired active entitlement.
            new_entitlement.valid_until = max(old_until, new_until)
            new_entitlement.valid_from = min(
                _as_utc(old_entitlement.valid_from),
                _as_utc(new_entitlement.valid_from),
            )
            new_entitlement.status = "revoked"
        new_entitlement.updated_at = func.now()
        await session.delete(old_entitlement)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _ensure_target_chat(
    session: AsyncSession,
    *,
    old_chat_id: int,
    new_chat_id: int,
    new_chat_type: str | None,
    new_chat_title: str | None,
) -> None:
    old_chat = await session.get(ChatModel, old_chat_id)
    new_chat = await session.get(ChatModel, new_chat_id)

    chat_type = new_chat_type or (new_chat.type if new_chat is not None else None) or (old_chat.type if old_chat is not None else None) or "supergroup"
    if new_chat_title is not None:
        chat_title = new_chat_title
    elif new_chat is not None and new_chat.title is not None:
        chat_title = new_chat.title
    elif old_chat is not None:
        chat_title = old_chat.title
    else:
        chat_title = None

    if new_chat is None:
        session.add(
            ChatModel(
                telegram_chat_id=new_chat_id,
                type=chat_type,
                title=chat_title,
                is_bot_member=old_chat.is_bot_member if old_chat is not None else True,
            )
        )
        return

    if chat_type and new_chat.type != chat_type:
        new_chat.type = chat_type
    if chat_title is not None and new_chat.title != chat_title:
        new_chat.title = chat_title


async def _migrate_postgresql(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> int:
    await _merge_activity_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_activity_daily_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_activity_minute_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_activity_events_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_announce_subscriptions_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_text_aliases_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_bot_roles_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_moderation_state_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_rest_state_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_activity_event_sync_state(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_chat_member_count_snapshot(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_chat_settings(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_chat_alias_settings(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_simple_chat_refs(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_llm_context_and_actions(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_llm_glossary_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_feature_quota_usage(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_ai_pets(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    return await _migrate_economy_scopes(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)


async def _migrate_generic(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> int:
    await _merge_activity_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_activity_daily_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_activity_minute_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_activity_events_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_announce_subscriptions_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_text_aliases_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_bot_roles_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_moderation_state_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_rest_state_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_activity_event_sync_state(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_chat_member_count_snapshot(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_chat_settings(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_chat_alias_settings(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_simple_chat_refs(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_llm_context_and_actions(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_llm_glossary_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_feature_quota_usage(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_ai_pets(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    return await _migrate_economy_scopes(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)


async def _move_feature_quota_usage(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    # `chat_id` is the current quota scope, while source_chat_id and the
    # idempotency key preserve the immutable origin identity of each request.
    await session.execute(
        update(AiFeatureQuotaUsageModel)
        .where(AiFeatureQuotaUsageModel.chat_id == old_chat_id)
        .values(chat_id=new_chat_id)
    )
    # The chat-scoped bucket follows the chat; user-scoped rows (personal quotas) only
    # record the chat as "where it happened" and keep paying from the user's bucket.
    await session.execute(
        update(AiFeatureQuotaUsageModel)
        .where(
            AiFeatureQuotaUsageModel.quota_scope_type == "chat",
            AiFeatureQuotaUsageModel.quota_scope_id == old_chat_id,
        )
        .values(quota_scope_id=new_chat_id)
    )


async def _move_ai_pets(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    # A pet whose name is already taken by an active pet in the target chat would
    # break uq_ai_pets_chat_name_active; it is put to sleep instead of renamed.
    taken_names = select(AiPetModel.name_norm).where(
        AiPetModel.current_chat_id == new_chat_id,
        AiPetModel.status == "active",
    )
    await session.execute(
        update(AiPetModel)
        .where(
            AiPetModel.current_chat_id == old_chat_id,
            AiPetModel.status == "active",
            AiPetModel.name_norm.in_(taken_names),
        )
        .values(status="dormant", dormant_reason="name_conflict")
    )
    await session.execute(
        update(AiPetModel).where(AiPetModel.current_chat_id == old_chat_id).values(current_chat_id=new_chat_id)
    )
    await session.execute(
        update(AiPetModel).where(AiPetModel.home_chat_id == old_chat_id).values(home_chat_id=new_chat_id)
    )

    # Relationship PK is (pet, chat, user): an existing row in the target chat wins.
    target = aliased(AiPetRelationshipModel)
    await session.execute(
        delete(AiPetRelationshipModel).where(
            AiPetRelationshipModel.chat_id == old_chat_id,
            exists(
                select(literal(1)).where(
                    target.chat_id == new_chat_id,
                    target.pet_id == AiPetRelationshipModel.pet_id,
                    target.user_id == AiPetRelationshipModel.user_id,
                )
            ),
        )
    )
    await session.execute(
        update(AiPetRelationshipModel)
        .where(AiPetRelationshipModel.chat_id == old_chat_id)
        .values(chat_id=new_chat_id)
    )
    await session.execute(
        update(AiPetEventModel).where(AiPetEventModel.chat_id == old_chat_id).values(chat_id=new_chat_id)
    )
    # Dialogue history and notes have no chat-keyed uniqueness: a plain move keeps the pet's memory of this chat.
    await session.execute(
        update(AiPetMessageModel).where(AiPetMessageModel.chat_id == old_chat_id).values(chat_id=new_chat_id)
    )
    await session.execute(
        update(AiPetMemoryModel).where(AiPetMemoryModel.chat_id == old_chat_id).values(chat_id=new_chat_id)
    )


async def _merge_activity_postgresql(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    source = (
        select(
            literal(new_chat_id).label("chat_id"),
            UserChatActivityModel.user_id,
            UserChatActivityModel.message_count,
            UserChatActivityModel.last_seen_at,
            UserChatActivityModel.display_name_override,
        )
        .where(UserChatActivityModel.chat_id == old_chat_id)
    )
    stmt = pg_insert(UserChatActivityModel).from_select(
        ["chat_id", "user_id", "message_count", "last_seen_at", "display_name_override"],
        source,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[UserChatActivityModel.chat_id, UserChatActivityModel.user_id],
        set_={
            "message_count": UserChatActivityModel.message_count + stmt.excluded.message_count,
            "last_seen_at": func.greatest(UserChatActivityModel.last_seen_at, stmt.excluded.last_seen_at),
            "display_name_override": func.coalesce(
                UserChatActivityModel.display_name_override,
                stmt.excluded.display_name_override,
            ),
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)
    await session.execute(delete(UserChatActivityModel).where(UserChatActivityModel.chat_id == old_chat_id))


async def _merge_activity_daily_postgresql(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    source = (
        select(
            literal(new_chat_id).label("chat_id"),
            UserChatActivityDailyModel.user_id,
            UserChatActivityDailyModel.activity_date,
            UserChatActivityDailyModel.message_count,
            UserChatActivityDailyModel.last_seen_at,
        )
        .where(UserChatActivityDailyModel.chat_id == old_chat_id)
    )
    stmt = pg_insert(UserChatActivityDailyModel).from_select(
        ["chat_id", "user_id", "activity_date", "message_count", "last_seen_at"],
        source,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            UserChatActivityDailyModel.chat_id,
            UserChatActivityDailyModel.user_id,
            UserChatActivityDailyModel.activity_date,
        ],
        set_={
            "message_count": UserChatActivityDailyModel.message_count + stmt.excluded.message_count,
            "last_seen_at": func.greatest(UserChatActivityDailyModel.last_seen_at, stmt.excluded.last_seen_at),
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)
    await session.execute(delete(UserChatActivityDailyModel).where(UserChatActivityDailyModel.chat_id == old_chat_id))


async def _merge_activity_minute_postgresql(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    source = (
        select(
            literal(new_chat_id).label("chat_id"),
            UserChatActivityMinuteModel.user_id,
            UserChatActivityMinuteModel.activity_minute,
            UserChatActivityMinuteModel.message_count,
            UserChatActivityMinuteModel.last_seen_at,
        )
        .where(UserChatActivityMinuteModel.chat_id == old_chat_id)
    )
    stmt = pg_insert(UserChatActivityMinuteModel).from_select(
        ["chat_id", "user_id", "activity_minute", "message_count", "last_seen_at"],
        source,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            UserChatActivityMinuteModel.chat_id,
            UserChatActivityMinuteModel.user_id,
            UserChatActivityMinuteModel.activity_minute,
        ],
        set_={
            "message_count": UserChatActivityMinuteModel.message_count + stmt.excluded.message_count,
            "last_seen_at": func.greatest(UserChatActivityMinuteModel.last_seen_at, stmt.excluded.last_seen_at),
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)
    await session.execute(delete(UserChatActivityMinuteModel).where(UserChatActivityMinuteModel.chat_id == old_chat_id))


async def _merge_activity_events_postgresql(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    source = (
        select(
            literal(new_chat_id).label("chat_id"),
            UserChatMessageEventModel.user_id,
            UserChatMessageEventModel.telegram_message_id,
            UserChatMessageEventModel.sent_at,
            UserChatMessageEventModel.is_synthetic,
            UserChatMessageEventModel.source_kind,
            UserChatMessageEventModel.source_bucket_at,
            UserChatMessageEventModel.source_seq,
            UserChatMessageEventModel.created_at,
        )
        .where(UserChatMessageEventModel.chat_id == old_chat_id)
    )
    stmt = pg_insert(UserChatMessageEventModel).from_select(
        [
            "chat_id",
            "user_id",
            "telegram_message_id",
            "sent_at",
            "is_synthetic",
            "source_kind",
            "source_bucket_at",
            "source_seq",
            "created_at",
        ],
        source,
    )
    stmt = stmt.on_conflict_do_nothing()
    await session.execute(stmt)
    await session.execute(delete(UserChatMessageEventModel).where(UserChatMessageEventModel.chat_id == old_chat_id))


async def _merge_announce_subscriptions_postgresql(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    source = (
        select(
            literal(new_chat_id).label("chat_id"),
            UserChatAnnouncementSubscriptionModel.user_id,
            UserChatAnnouncementSubscriptionModel.is_enabled,
        )
        .where(UserChatAnnouncementSubscriptionModel.chat_id == old_chat_id)
    )
    stmt = pg_insert(UserChatAnnouncementSubscriptionModel).from_select(
        ["chat_id", "user_id", "is_enabled"],
        source,
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=[
            UserChatAnnouncementSubscriptionModel.chat_id,
            UserChatAnnouncementSubscriptionModel.user_id,
        ]
    )
    await session.execute(stmt)
    await session.execute(
        delete(UserChatAnnouncementSubscriptionModel).where(UserChatAnnouncementSubscriptionModel.chat_id == old_chat_id)
    )


async def _merge_text_aliases_postgresql(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    source = (
        select(
            literal(new_chat_id).label("chat_id"),
            ChatTextAliasModel.command_key,
            ChatTextAliasModel.alias_text_norm,
            ChatTextAliasModel.source_trigger_norm,
            ChatTextAliasModel.created_by_user_id,
        )
        .where(ChatTextAliasModel.chat_id == old_chat_id)
    )
    stmt = pg_insert(ChatTextAliasModel).from_select(
        [
            "chat_id",
            "command_key",
            "alias_text_norm",
            "source_trigger_norm",
            "created_by_user_id",
        ],
        source,
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=[ChatTextAliasModel.chat_id, ChatTextAliasModel.alias_text_norm]
    )
    await session.execute(stmt)
    await session.execute(delete(ChatTextAliasModel).where(ChatTextAliasModel.chat_id == old_chat_id))


async def _merge_bot_roles_postgresql(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    source = (
        select(
            literal(new_chat_id).label("chat_id"),
            UserChatBotRoleModel.user_id,
            UserChatBotRoleModel.role,
            UserChatBotRoleModel.assigned_by_user_id,
        )
        .where(UserChatBotRoleModel.chat_id == old_chat_id)
    )
    stmt = pg_insert(UserChatBotRoleModel).from_select(
        ["chat_id", "user_id", "role", "assigned_by_user_id"],
        source,
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=[UserChatBotRoleModel.chat_id, UserChatBotRoleModel.user_id])
    await session.execute(stmt)
    await session.execute(delete(UserChatBotRoleModel).where(UserChatBotRoleModel.chat_id == old_chat_id))


async def _merge_moderation_state_postgresql(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    source = (
        select(
            literal(new_chat_id).label("chat_id"),
            UserChatModerationStateModel.user_id,
            UserChatModerationStateModel.pending_preds,
            UserChatModerationStateModel.warn_count,
            UserChatModerationStateModel.total_preds,
            UserChatModerationStateModel.total_warns,
            UserChatModerationStateModel.total_bans,
            UserChatModerationStateModel.is_banned,
            UserChatModerationStateModel.last_reason,
        )
        .where(UserChatModerationStateModel.chat_id == old_chat_id)
    )
    stmt = pg_insert(UserChatModerationStateModel).from_select(
        [
            "chat_id",
            "user_id",
            "pending_preds",
            "warn_count",
            "total_preds",
            "total_warns",
            "total_bans",
            "is_banned",
            "last_reason",
        ],
        source,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[UserChatModerationStateModel.chat_id, UserChatModerationStateModel.user_id],
        set_={
            "pending_preds": UserChatModerationStateModel.pending_preds + stmt.excluded.pending_preds,
            "warn_count": UserChatModerationStateModel.warn_count + stmt.excluded.warn_count,
            "total_preds": UserChatModerationStateModel.total_preds + stmt.excluded.total_preds,
            "total_warns": UserChatModerationStateModel.total_warns + stmt.excluded.total_warns,
            "total_bans": UserChatModerationStateModel.total_bans + stmt.excluded.total_bans,
            "is_banned": UserChatModerationStateModel.is_banned | stmt.excluded.is_banned,
            "last_reason": func.coalesce(UserChatModerationStateModel.last_reason, stmt.excluded.last_reason),
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)
    await session.execute(delete(UserChatModerationStateModel).where(UserChatModerationStateModel.chat_id == old_chat_id))


async def _merge_rest_state_postgresql(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    source = (
        select(
            literal(new_chat_id).label("chat_id"),
            UserChatRestStateModel.user_id,
            UserChatRestStateModel.expires_at,
            UserChatRestStateModel.granted_by_user_id,
        )
        .where(UserChatRestStateModel.chat_id == old_chat_id)
    )
    stmt = pg_insert(UserChatRestStateModel).from_select(
        ["chat_id", "user_id", "expires_at", "granted_by_user_id"],
        source,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[UserChatRestStateModel.chat_id, UserChatRestStateModel.user_id],
        set_={
            "expires_at": func.greatest(UserChatRestStateModel.expires_at, stmt.excluded.expires_at),
            "granted_by_user_id": func.coalesce(stmt.excluded.granted_by_user_id, UserChatRestStateModel.granted_by_user_id),
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)
    await session.execute(delete(UserChatRestStateModel).where(UserChatRestStateModel.chat_id == old_chat_id))


async def _merge_activity_generic(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    rows = (await session.execute(select(UserChatActivityModel).where(UserChatActivityModel.chat_id == old_chat_id))).scalars().all()
    for row in rows:
        current = await session.get(
            UserChatActivityModel,
            {"chat_id": new_chat_id, "user_id": row.user_id},
        )
        if current is None:
            row.chat_id = new_chat_id
            continue
        current.message_count += row.message_count
        current.last_seen_at = max(current.last_seen_at, row.last_seen_at)
        if current.display_name_override is None:
            current.display_name_override = row.display_name_override
        await session.delete(row)


async def _merge_activity_daily_generic(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    rows = (
        await session.execute(select(UserChatActivityDailyModel).where(UserChatActivityDailyModel.chat_id == old_chat_id))
    ).scalars().all()
    for row in rows:
        current = await session.get(
            UserChatActivityDailyModel,
            {"chat_id": new_chat_id, "user_id": row.user_id, "activity_date": row.activity_date},
        )
        if current is None:
            row.chat_id = new_chat_id
            continue
        current.message_count += row.message_count
        current.last_seen_at = max(current.last_seen_at, row.last_seen_at)
        await session.delete(row)


async def _merge_activity_minute_generic(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    rows = (
        await session.execute(select(UserChatActivityMinuteModel).where(UserChatActivityMinuteModel.chat_id == old_chat_id))
    ).scalars().all()
    for row in rows:
        current = await session.get(
            UserChatActivityMinuteModel,
            {"chat_id": new_chat_id, "user_id": row.user_id, "activity_minute": row.activity_minute},
        )
        if current is None:
            row.chat_id = new_chat_id
            continue
        current.message_count += row.message_count
        current.last_seen_at = max(current.last_seen_at, row.last_seen_at)
        await session.delete(row)


async def _merge_activity_events_generic(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    rows = (
        await session.execute(select(UserChatMessageEventModel).where(UserChatMessageEventModel.chat_id == old_chat_id))
    ).scalars().all()
    for row in rows:
        conflict_stmt = select(UserChatMessageEventModel.id).where(UserChatMessageEventModel.chat_id == new_chat_id)
        if row.telegram_message_id is not None:
            conflict_stmt = conflict_stmt.where(UserChatMessageEventModel.telegram_message_id == row.telegram_message_id)
        elif row.is_synthetic:
            conflict_stmt = conflict_stmt.where(
                UserChatMessageEventModel.user_id == row.user_id,
                UserChatMessageEventModel.source_kind == row.source_kind,
                UserChatMessageEventModel.source_bucket_at == row.source_bucket_at,
                UserChatMessageEventModel.source_seq == row.source_seq,
            )
        else:
            conflict_stmt = None

        if conflict_stmt is not None:
            existing = (await session.execute(conflict_stmt)).scalar_one_or_none()
            if existing is not None:
                await session.delete(row)
                continue

        row.chat_id = new_chat_id


async def _merge_activity_event_sync_state(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    old_row = await session.get(ChatActivityEventSyncStateModel, old_chat_id)
    new_row = await session.get(ChatActivityEventSyncStateModel, new_chat_id)

    if old_row is not None:
        await session.delete(old_row)

    if new_row is None:
        session.add(
            ChatActivityEventSyncStateModel(
                chat_id=new_chat_id,
                status="mismatch",
                legacy_total_messages=None,
                event_total_messages=None,
                last_checked_at=None,
                last_synced_at=None,
                last_error="chat_id_migrated",
            )
        )
        return

    new_row.status = "mismatch"
    new_row.legacy_total_messages = None
    new_row.event_total_messages = None
    new_row.last_checked_at = None
    new_row.last_synced_at = None
    new_row.last_error = "chat_id_migrated"


async def _merge_chat_member_count_snapshot(
    session: AsyncSession, *, old_chat_id: int, new_chat_id: int
) -> None:
    old_row = await session.get(ChatMemberCountSnapshotModel, old_chat_id)
    if old_row is None:
        return
    new_row = await session.get(ChatMemberCountSnapshotModel, new_chat_id)
    if new_row is None:
        old_row.chat_id = new_chat_id
        return

    if old_row.last_success_at is not None and (
        new_row.last_success_at is None or old_row.last_success_at > new_row.last_success_at
    ):
        new_row.member_count = old_row.member_count
        new_row.last_success_at = old_row.last_success_at
    if old_row.last_checked_at is not None and (
        new_row.last_checked_at is None or old_row.last_checked_at > new_row.last_checked_at
    ):
        new_row.last_checked_at = old_row.last_checked_at
    if old_row.last_error_at is not None and (
        new_row.last_error_at is None or old_row.last_error_at > new_row.last_error_at
    ):
        new_row.last_error_at = old_row.last_error_at
    await session.delete(old_row)


async def _merge_announce_subscriptions_generic(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    rows = (
        await session.execute(
            select(UserChatAnnouncementSubscriptionModel).where(UserChatAnnouncementSubscriptionModel.chat_id == old_chat_id)
        )
    ).scalars().all()
    for row in rows:
        current = await session.get(
            UserChatAnnouncementSubscriptionModel,
            {"chat_id": new_chat_id, "user_id": row.user_id},
        )
        if current is None:
            row.chat_id = new_chat_id
            continue
        await session.delete(row)


async def _merge_text_aliases_generic(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    rows = (await session.execute(select(ChatTextAliasModel).where(ChatTextAliasModel.chat_id == old_chat_id))).scalars().all()
    for row in rows:
        current_stmt = select(ChatTextAliasModel).where(
            ChatTextAliasModel.chat_id == new_chat_id,
            ChatTextAliasModel.alias_text_norm == row.alias_text_norm,
        )
        current = (await session.execute(current_stmt)).scalar_one_or_none()
        if current is None:
            row.chat_id = new_chat_id
            continue
        await session.delete(row)


async def _merge_bot_roles_generic(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    rows = (await session.execute(select(UserChatBotRoleModel).where(UserChatBotRoleModel.chat_id == old_chat_id))).scalars().all()
    for row in rows:
        current = await session.get(
            UserChatBotRoleModel,
            {"chat_id": new_chat_id, "user_id": row.user_id},
        )
        if current is None:
            row.chat_id = new_chat_id
            continue
        await session.delete(row)


async def _merge_moderation_state_generic(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    rows = (
        await session.execute(select(UserChatModerationStateModel).where(UserChatModerationStateModel.chat_id == old_chat_id))
    ).scalars().all()
    for row in rows:
        current = await session.get(
            UserChatModerationStateModel,
            {"chat_id": new_chat_id, "user_id": row.user_id},
        )
        if current is None:
            row.chat_id = new_chat_id
            continue

        current.pending_preds += row.pending_preds
        current.warn_count += row.warn_count
        current.total_preds += row.total_preds
        current.total_warns += row.total_warns
        current.total_bans += row.total_bans
        current.is_banned = bool(current.is_banned or row.is_banned)
        if current.last_reason is None:
            current.last_reason = row.last_reason
        await session.delete(row)


async def _merge_rest_state_generic(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    rows = (await session.execute(select(UserChatRestStateModel).where(UserChatRestStateModel.chat_id == old_chat_id))).scalars().all()
    for row in rows:
        current = await session.get(
            UserChatRestStateModel,
            {"chat_id": new_chat_id, "user_id": row.user_id},
        )
        if current is None:
            row.chat_id = new_chat_id
            continue

        if current.expires_at < row.expires_at:
            current.expires_at = row.expires_at
        if current.granted_by_user_id is None:
            current.granted_by_user_id = row.granted_by_user_id
        await session.delete(row)


async def _move_chat_settings(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    old_row = await session.get(ChatSettingsModel, old_chat_id)
    if old_row is None:
        return

    new_row = await session.get(ChatSettingsModel, new_chat_id)
    if new_row is None:
        old_row.chat_id = new_chat_id
        return

    for column in ChatSettingsModel.__table__.columns:
        if column.name in {"chat_id", "updated_at"}:
            continue
        setattr(new_row, column.name, getattr(old_row, column.name))
    await session.delete(old_row)


async def _move_chat_alias_settings(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    old_row = await session.get(ChatTextAliasSettingsModel, old_chat_id)
    if old_row is None:
        return

    new_row = await session.get(ChatTextAliasSettingsModel, new_chat_id)
    if new_row is None:
        old_row.chat_id = new_chat_id
        return

    new_row.mode = old_row.mode
    await session.delete(old_row)


async def _move_simple_chat_refs(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    await session.execute(update(UserKarmaVoteModel).where(UserKarmaVoteModel.chat_id == old_chat_id).values(chat_id=new_chat_id))
    await session.execute(
        update(RelationshipProposalModel)
        .where(RelationshipProposalModel.chat_id == old_chat_id)
        .values(chat_id=new_chat_id)
    )
    await session.execute(update(PairModel).where(PairModel.chat_id == old_chat_id).values(chat_id=new_chat_id))
    await session.execute(update(MarriageModel).where(MarriageModel.chat_id == old_chat_id).values(chat_id=new_chat_id))
    await session.execute(
        update(EconomyPrivateContextModel)
        .where(EconomyPrivateContextModel.chat_id == old_chat_id)
        .values(chat_id=new_chat_id)
    )
    await session.execute(
        update(AdminRuntimeSettingsModel)
        .where(AdminRuntimeSettingsModel.error_alert_chat_id == old_chat_id)
        .values(error_alert_chat_id=new_chat_id)
    )


async def _move_llm_context_and_actions(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    """#36: LlmContextMessageModel/LlmContextSummaryModel/LlmAdminActionModel
    have no unique constraint on chat_id, so a plain bulk UPDATE (same shape
    as _move_simple_chat_refs) is safe on both dialects -- this used to be
    entirely missing, silently orphaning all prior LLM context/audit history
    under the old chat_id on every group -> supergroup upgrade."""
    await session.execute(
        update(LlmContextMessageModel)
        .where(LlmContextMessageModel.chat_id == old_chat_id)
        .values(chat_id=new_chat_id)
    )
    await session.execute(
        update(LlmContextSummaryModel)
        .where(LlmContextSummaryModel.chat_id == old_chat_id)
        .values(chat_id=new_chat_id)
    )
    await session.execute(
        update(LlmAdminActionModel)
        .where(LlmAdminActionModel.chat_id == old_chat_id)
        .values(chat_id=new_chat_id)
    )

    # A draft needs a new selection/review after Telegram changes its destination ID.
    await session.execute(update(AutoConfigSessionModel).where(AutoConfigSessionModel.chat_id == old_chat_id)
        .values(chat_id=new_chat_id, state='closed', revision=AutoConfigSessionModel.revision + 1,
                history=[], candidates=[], baseline={}, draft={}, touched=[], lease_token=None, lease_until=None))

    # A Telegram group upgrade preserves the logical chat and its artifacts.
    await session.execute(
        update(LlmArtifactModel).where(LlmArtifactModel.chat_id == old_chat_id).values(chat_id=new_chat_id)
    )

    await _move_ai_accounting_records(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)


async def _move_ai_accounting_records(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    """Move archive, summary and accounting history, merging possible target duplicates."""
    archive_rows = (await session.execute(
        select(MessageArchiveModel).where(MessageArchiveModel.chat_id == old_chat_id)
    )).scalars().all()
    for source in archive_rows:
        target = (await session.execute(
            select(MessageArchiveModel).where(
                MessageArchiveModel.chat_id == new_chat_id,
                MessageArchiveModel.telegram_message_id == source.telegram_message_id,
                MessageArchiveModel.snapshot_hash == source.snapshot_hash,
            )
        )).scalar_one_or_none()
        if target is None:
            source.chat_id = new_chat_id
            continue
        await session.execute(
            update(LlmUsageLogModel)
            .where(LlmUsageLogModel.message_archive_id == source.id)
            .values(message_archive_id=target.id)
        )
        if target.transcript is None and source.transcript is not None:
            target.transcript = source.transcript
            target.transcribed_at = source.transcribed_at
        await session.delete(source)

    summary_rows = (await session.execute(
        select(DailySummaryRunModel).where(DailySummaryRunModel.chat_id == old_chat_id)
    )).scalars().all()
    status_rank = {"claimed": 0, "generating": 1, "failed": 2, "send_failed": 3, "generated": 4, "sent": 5}
    for source in summary_rows:
        target = (await session.execute(
            select(DailySummaryRunModel).where(
                DailySummaryRunModel.chat_id == new_chat_id,
                DailySummaryRunModel.summary_date == source.summary_date,
                DailySummaryRunModel.trigger == source.trigger,
            )
        )).scalar_one_or_none()
        if target is None:
            source.chat_id = new_chat_id
            continue
        await session.execute(
            update(LlmUsageLogModel)
            .where(LlmUsageLogModel.summary_run_id == source.id)
            .values(summary_run_id=target.id)
        )
        await session.execute(
            update(AiFeatureInvocationModel)
            .where(AiFeatureInvocationModel.summary_run_id == source.id)
            .values(summary_run_id=target.id)
        )
        target.pipeline_cost_usd += source.pipeline_cost_usd
        target.context_stt_cost_usd += source.context_stt_cost_usd
        target.pipeline_has_unknown_cost = target.pipeline_has_unknown_cost or source.pipeline_has_unknown_cost
        if target.generated_text is None:
            target.generated_text = source.generated_text
            target.topics_json = source.topics_json
            target.diagnostics_json = source.diagnostics_json
        if status_rank.get(source.status, 0) > status_rank.get(target.status, 0):
            target.status = source.status
            target.sent_at = target.sent_at or source.sent_at
        await session.delete(source)

    await session.execute(
        update(AiFeatureInvocationModel)
        .where(
            AiFeatureInvocationModel.chat_id == old_chat_id,
            AiFeatureInvocationModel.scope_type == "chat",
        )
        .values(chat_id=new_chat_id, scope_id=str(new_chat_id))
    )
    await session.execute(
        update(AiFeatureInvocationModel)
        .where(
            AiFeatureInvocationModel.chat_id == old_chat_id,
            AiFeatureInvocationModel.scope_type != "chat",
        )
        .values(chat_id=new_chat_id)
    )
    await session.execute(
        update(LlmUsageLogModel).where(LlmUsageLogModel.chat_id == old_chat_id).values(chat_id=new_chat_id)
    )


async def _merge_llm_glossary_postgresql(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    # At most 200 glossary entries; ORM movement preserves IDs, authors,
    # aliases and history on both dialects instead of copying three fields.
    await _merge_llm_glossary_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)


async def _merge_llm_glossary_generic(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> None:
    rows = (await session.execute(
        select(LlmChatGlossaryModel).where(LlmChatGlossaryModel.chat_id == old_chat_id)
    )).scalars().all()
    destination = list((await session.execute(
        select(LlmChatGlossaryModel).where(LlmChatGlossaryModel.chat_id == new_chat_id)
    )).scalars().all())
    occupied = {normalize_glossary_text(name) for row in destination
                for name in [row.term, *(a.alias for a in row.aliases)]}
    for row in rows:
        if normalize_glossary_text(row.term) in occupied:
            session.add(LlmChatGlossaryHistoryModel(
                chat_id=new_chat_id, term=row.term, previous_definition=row.definition,
                previous_aliases=[a.alias for a in row.aliases], changed_by_user_id=row.updated_by_user_id,
            ))
            await session.delete(row)
        else:
            row.chat_id = new_chat_id
            occupied.add(normalize_glossary_text(row.term))
            for alias in list(row.aliases):
                if alias.alias in occupied:
                    row.aliases.remove(alias)
                else:
                    alias.chat_id = new_chat_id
                    occupied.add(alias.alias)
    await session.execute(update(LlmChatGlossaryHistoryModel).where(
        LlmChatGlossaryHistoryModel.chat_id == old_chat_id,
    ).values(chat_id=new_chat_id))


async def _migrate_economy_scopes(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> int:
    old_scope = f"chat:{old_chat_id}"
    new_scope = f"chat:{new_chat_id}"

    conflicting = aliased(EconomyAccountModel)
    conflict_users_stmt = (
        select(EconomyAccountModel.user_id)
        .where(EconomyAccountModel.scope_id == old_scope)
        .where(
            exists(
                select(conflicting.id).where(
                    conflicting.scope_id == new_scope,
                    conflicting.user_id == EconomyAccountModel.user_id,
                )
            )
        )
    )
    conflict_user_ids = [int(user_id) for user_id in (await session.execute(conflict_users_stmt)).scalars().all()]

    if conflict_user_ids:
        logger.warning(
            "Skipping economy account scope migration for conflicted users",
            extra={
                "old_chat_id": old_chat_id,
                "new_chat_id": new_chat_id,
                "conflicted_user_count": len(conflict_user_ids),
            },
        )

    await session.execute(
        update(EconomyAccountModel)
        .where(EconomyAccountModel.scope_id == old_scope)
        .where(
            ~exists(
                select(conflicting.id).where(
                    conflicting.scope_id == new_scope,
                    conflicting.user_id == EconomyAccountModel.user_id,
                )
            )
        )
        .values(scope_id=new_scope, chat_id=new_chat_id)
    )
    await session.execute(
        update(EconomyAccountModel)
        .where(EconomyAccountModel.chat_id == old_chat_id)
        .where(EconomyAccountModel.scope_id != old_scope)
        .values(chat_id=new_chat_id)
    )

    await session.execute(
        update(EconomyMarketListingModel)
        .where(EconomyMarketListingModel.scope_id == old_scope)
        .values(scope_id=new_scope, chat_id=new_chat_id)
    )
    await session.execute(
        update(EconomyMarketListingModel)
        .where(EconomyMarketListingModel.chat_id == old_chat_id)
        .where(EconomyMarketListingModel.scope_id != old_scope)
        .values(chat_id=new_chat_id)
    )

    return len(conflict_user_ids)
