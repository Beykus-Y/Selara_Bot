from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import delete, exists, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from selara.infrastructure.db.models import (
    ChatModel,
    ChatSettingsModel,
    ChatTextAliasModel,
    ChatTextAliasSettingsModel,
    EconomyAccountModel,
    EconomyMarketListingModel,
    EconomyPrivateContextModel,
    MarriageModel,
    PairModel,
    RelationshipProposalModel,
    UserChatActivityDailyModel,
    UserChatActivityMinuteModel,
    UserChatActivityModel,
    UserChatAnnouncementSubscriptionModel,
    UserChatBotRoleModel,
    UserChatModerationStateModel,
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
        session.add(ChatModel(telegram_chat_id=new_chat_id, type=chat_type, title=chat_title))
        return

    if chat_type and new_chat.type != chat_type:
        new_chat.type = chat_type
    if chat_title is not None and new_chat.title != chat_title:
        new_chat.title = chat_title


async def _migrate_postgresql(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> int:
    await _merge_activity_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_activity_daily_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_activity_minute_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_announce_subscriptions_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_text_aliases_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_bot_roles_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_moderation_state_postgresql(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_chat_settings(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_chat_alias_settings(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_simple_chat_refs(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    return await _migrate_economy_scopes(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)


async def _migrate_generic(session: AsyncSession, *, old_chat_id: int, new_chat_id: int) -> int:
    await _merge_activity_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_activity_daily_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_activity_minute_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_announce_subscriptions_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_text_aliases_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_bot_roles_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _merge_moderation_state_generic(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_chat_settings(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_chat_alias_settings(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    await _move_simple_chat_refs(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)
    return await _migrate_economy_scopes(session, old_chat_id=old_chat_id, new_chat_id=new_chat_id)


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
