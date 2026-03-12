from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from selara.application.use_cases.leaderboard_scoring import compute_hybrid_score, sort_leaderboard_items
from selara.core.chat_settings import ChatSettings
from selara.core.trigger_templates import validate_template_variables
from selara.core.roles import (
    BOT_PERMISSIONS,
    SYSTEM_ROLE_BY_TEMPLATE_KEY,
    SYSTEM_ROLE_TEMPLATES,
    normalize_assigned_role_code,
    normalize_role_code,
    normalize_role_title,
    resolve_role_template_key,
)
from selara.core.text_aliases import ALIAS_MODE_DEFAULT, ALIAS_MODE_VALUES
from selara.domain.entities import (
    ActivityStats,
    AchievementAward,
    BotRole,
    ChatAuditLogEntry,
    ChatActivitySummary,
    ChatCommandAccessRule,
    ChatRoleDefinition,
    UserChatAward,
    UserChatProfile,
    ChatTrigger,
    CustomSocialAction,
    ChatTextAlias,
    ChatTextAliasUpsertResult,
    ChatSnapshot,
    FamilyBundle,
    FamilyGraph,
    FamilyGraphEdge,
    GraphRelationType,
    GraphRelationship,
    IrisImportState,
    InlinePrivateMessage,
    LeaderboardItem,
    LeaderboardMode,
    LeaderboardPeriod,
    PairState,
    RelationshipActionCode,
    RelationshipKind,
    RelationshipState,
    MarriageState,
    ModerationAction,
    ModerationResult,
    ModerationState,
    RelationshipProposal,
    TextAliasMode,
    UserChatOverview,
    UserSnapshot,
)
from selara.domain.economy_entities import (
    ChatAuction,
    ChatBoost,
    EconomyAccount,
    EconomyScope,
    FarmState,
    InventoryItem,
    MarketListing,
    MarketTrade,
    PlotState,
)
from selara.domain.value_objects import display_name_from_parts
from selara.infrastructure.db.models import (
    ChatAuditLogModel,
    ChatAchievementStatsModel,
    ChatAuctionModel,
    ChatGlobalBoostModel,
    ChatCommandAccessRuleModel,
    ChatCustomSocialActionModel,
    ChatModel,
    ChatRoleDefinitionModel,
    ChatSettingsModel,
    ChatTriggerModel,
    ChatTextAliasModel,
    ChatTextAliasSettingsModel,
    EconomyAccountModel,
    EconomyFarmModel,
    EconomyInventoryModel,
    EconomyLedgerModel,
    EconomyMarketListingModel,
    EconomyMarketTradeModel,
    EconomyPlotModel,
    EconomyPrivateContextModel,
    EconomyTransferDailyModel,
    GlobalAchievementStatsModel,
    InlinePrivateMessageModel,
    MarriageModel,
    PairModel,
    RelationshipGraphModel,
    RelationshipActionUsageModel,
    RelationshipProposalModel,
    UserChatActivityDailyModel,
    UserChatActivityMinuteModel,
    UserChatActivityModel,
    UserChatAchievementModel,
    UserChatAnnouncementSubscriptionModel,
    UserChatAwardModel,
    UserChatBotRoleModel,
    UserChatModerationStateModel,
    UserChatIrisImportHistoryModel,
    UserChatIrisImportStateModel,
    UserChatProfileModel,
    UserGlobalAchievementModel,
    UserKarmaVoteModel,
    UserModel,
)
from selara.infrastructure.db.achievement_metrics import (
    adjust_chat_active_members_count,
    compute_holders_percent,
    increment_global_users_base_count,
    set_chat_active_members_count,
    set_global_users_base_count,
)


def _normalize_free_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _normalize_title_prefix(value: str | None) -> str | None:
    normalized = " ".join((value or "").strip().split())
    if not normalized:
        return None
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1].strip()
    return normalized[:72] or None


def _normalize_profile_description(value: str | None) -> str | None:
    normalized = " ".join((value or "").strip().split())
    return normalized[:280] or None


def _normalize_award_title(value: str) -> str:
    normalized = " ".join((value or "").strip().split())
    if not normalized:
        raise ValueError("Пустое название награды.")
    return normalized[:160]


def _coerce_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _latest_datetime(left: datetime, right: datetime) -> datetime:
    normalized_left = _coerce_utc_datetime(left)
    normalized_right = _coerce_utc_datetime(right)
    return max(normalized_left, normalized_right)


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _coerce_utc_datetime(value).isoformat()


def _distribute_activity(total: int, days: Sequence[date]) -> dict[date, int]:
    if total <= 0 or not days:
        return {}
    base, remainder = divmod(total, len(days))
    values: dict[date, int] = {}
    for index, activity_date in enumerate(days):
        values[activity_date] = base + (1 if index < remainder else 0)
    return values


def _build_synthetic_activity_daily_rows(
    *,
    imported_at: datetime,
    last_seen_at: datetime,
    activity_1d: int,
    activity_7d: int,
    activity_30d: int,
) -> list[tuple[date, int, datetime]]:
    today = _coerce_utc_datetime(imported_at).date()
    normalized_last_seen = _coerce_utc_datetime(last_seen_at)
    values: dict[date, int] = {}
    values.update(_distribute_activity(activity_30d - activity_7d, [today - timedelta(days=offset) for offset in range(7, 30)]))
    values.update(_distribute_activity(activity_7d - activity_1d, [today - timedelta(days=offset) for offset in range(1, 7)]))
    values[today] = max(0, int(activity_1d))

    rows: list[tuple[date, int, datetime]] = []
    for activity_date, message_count in sorted(values.items()):
        if message_count <= 0:
            continue
        if activity_date == today:
            row_last_seen = normalized_last_seen
        else:
            row_last_seen = datetime.combine(activity_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12)
        rows.append((activity_date, message_count, row_last_seen))
    return rows


def _build_synthetic_activity_minute_rows(
    *,
    daily_rows: Sequence[tuple[date, int, datetime]],
) -> list[tuple[datetime, int, datetime]]:
    values: dict[datetime, tuple[int, datetime]] = {}
    for _activity_date, message_count, row_last_seen in daily_rows:
        if message_count <= 0:
            continue
        normalized_last_seen = _coerce_utc_datetime(row_last_seen)
        minute_bucket = normalized_last_seen.replace(second=0, microsecond=0)
        existing = values.get(minute_bucket)
        if existing is None:
            values[minute_bucket] = (int(message_count), normalized_last_seen)
            continue
        previous_count, previous_last_seen = existing
        values[minute_bucket] = (
            previous_count + int(message_count),
            _latest_datetime(previous_last_seen, normalized_last_seen),
        )

    return [
        (activity_minute, message_count, last_seen_at)
        for activity_minute, (message_count, last_seen_at) in sorted(values.items())
    ]


class SqlAlchemyActivityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_activity(
        self,
        *,
        chat: ChatSnapshot,
        user: UserSnapshot,
        event_at: datetime,
    ) -> ActivityStats:
        # Keep entity upserts in a stable order to reduce PostgreSQL deadlock risk.
        await self._upsert_chat(chat)
        await self._upsert_user(user)
        existing_activity = await self._session.get(
            UserChatActivityModel,
            {"chat_id": chat.telegram_chat_id, "user_id": user.telegram_user_id},
        )
        previous_is_active = bool(existing_activity.is_active_member) if existing_activity is not None else False

        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"
        if dialect == "postgresql":
            insert_stmt = pg_insert(UserChatActivityModel).values(
                chat_id=chat.telegram_chat_id,
                user_id=user.telegram_user_id,
                message_count=1,
                is_active_member=True,
                last_seen_at=event_at,
            )
            upsert_stmt = insert_stmt.on_conflict_do_update(
                index_elements=[UserChatActivityModel.chat_id, UserChatActivityModel.user_id],
                set_={
                    "message_count": UserChatActivityModel.message_count + 1,
                    "is_active_member": True,
                    "last_seen_at": func.greatest(UserChatActivityModel.last_seen_at, insert_stmt.excluded.last_seen_at),
                    "updated_at": func.now(),
                },
            )
            await self._session.execute(upsert_stmt)
        else:
            activity = await self._session.get(
                UserChatActivityModel,
                {"chat_id": chat.telegram_chat_id, "user_id": user.telegram_user_id},
            )
            if activity is None:
                self._session.add(
                    UserChatActivityModel(
                        chat_id=chat.telegram_chat_id,
                        user_id=user.telegram_user_id,
                        message_count=1,
                        is_active_member=True,
                        last_seen_at=event_at,
                    )
                )
            else:
                activity.message_count += 1
                activity.is_active_member = True
                activity.last_seen_at = _latest_datetime(activity.last_seen_at, event_at)
                activity.updated_at = datetime.now(timezone.utc)

        await self._upsert_activity_daily(chat_id=chat.telegram_chat_id, user_id=user.telegram_user_id, event_at=event_at)
        await self._upsert_activity_minute(chat_id=chat.telegram_chat_id, user_id=user.telegram_user_id, event_at=event_at)
        await self._session.flush()
        if existing_activity is None or not previous_is_active:
            await adjust_chat_active_members_count(self._session, chat_id=chat.telegram_chat_id, delta=1)

        stats = await self.get_user_stats(chat_id=chat.telegram_chat_id, user_id=user.telegram_user_id)
        if stats is None:
            raise RuntimeError("Failed to load user stats after upsert")
        return stats

    async def set_chat_member_active(
        self,
        *,
        chat: ChatSnapshot,
        user: UserSnapshot,
        is_active: bool,
        event_at: datetime,
    ) -> None:
        await self._upsert_chat(chat)
        await self._upsert_user(user)
        existing_row = await self._session.get(
            UserChatActivityModel,
            {"chat_id": chat.telegram_chat_id, "user_id": user.telegram_user_id},
        )
        previous_is_active = bool(existing_row.is_active_member) if existing_row is not None else False

        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"
        if dialect == "postgresql":
            insert_stmt = pg_insert(UserChatActivityModel).values(
                chat_id=chat.telegram_chat_id,
                user_id=user.telegram_user_id,
                message_count=0,
                is_active_member=is_active,
                last_seen_at=event_at,
            )
            upsert_stmt = insert_stmt.on_conflict_do_update(
                index_elements=[UserChatActivityModel.chat_id, UserChatActivityModel.user_id],
                set_={
                    "is_active_member": is_active,
                    "last_seen_at": func.greatest(UserChatActivityModel.last_seen_at, insert_stmt.excluded.last_seen_at),
                    "updated_at": func.now(),
                },
            )
            await self._session.execute(upsert_stmt)
            await self._session.flush()
            delta = int(is_active) - int(previous_is_active)
            if delta != 0:
                await adjust_chat_active_members_count(self._session, chat_id=chat.telegram_chat_id, delta=delta)
            return

        row = await self._session.get(
            UserChatActivityModel,
            {"chat_id": chat.telegram_chat_id, "user_id": user.telegram_user_id},
        )
        if row is None:
            self._session.add(
                UserChatActivityModel(
                    chat_id=chat.telegram_chat_id,
                    user_id=user.telegram_user_id,
                    message_count=0,
                    is_active_member=is_active,
                    last_seen_at=event_at,
                )
            )
        else:
            row.is_active_member = is_active
            row.last_seen_at = _latest_datetime(row.last_seen_at, event_at)
            row.updated_at = datetime.now(timezone.utc)

        await self._session.flush()
        delta = int(is_active) - int(previous_is_active)
        if delta != 0:
            await adjust_chat_active_members_count(self._session, chat_id=chat.telegram_chat_id, delta=delta)

    async def get_user_stats(self, *, chat_id: int, user_id: int) -> ActivityStats | None:
        stmt = (
            select(UserChatActivityModel, UserModel)
            .join(UserModel, UserModel.telegram_user_id == UserChatActivityModel.user_id)
            .where(UserChatActivityModel.chat_id == chat_id, UserChatActivityModel.user_id == user_id)
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None

        activity, user = row
        return self._to_stats(activity, user)

    async def get_user_message_streak_days(self, *, chat_id: int, user_id: int) -> int:
        stmt = (
            select(UserChatActivityDailyModel.activity_date)
            .where(
                UserChatActivityDailyModel.chat_id == chat_id,
                UserChatActivityDailyModel.user_id == user_id,
                UserChatActivityDailyModel.message_count > 0,
            )
            .order_by(UserChatActivityDailyModel.activity_date.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        if not rows:
            return 0

        streak = 0
        expected_day = rows[0]
        for current_day in rows:
            if current_day != expected_day:
                break
            streak += 1
            expected_day = expected_day - timedelta(days=1)
        return streak

    async def get_user_message_count_for_day(self, *, chat_id: int, user_id: int, activity_date: date) -> int:
        stmt = select(UserChatActivityDailyModel.message_count).where(
            UserChatActivityDailyModel.chat_id == chat_id,
            UserChatActivityDailyModel.user_id == user_id,
            UserChatActivityDailyModel.activity_date == activity_date,
        )
        return int((await self._session.execute(stmt)).scalar_one_or_none() or 0)

    async def count_total_achievements(self, *, user_id: int, chat_id: int | None = None) -> int:
        chat_count_stmt = select(func.count()).select_from(UserChatAchievementModel).where(UserChatAchievementModel.user_id == user_id)
        if chat_id is not None:
            chat_count_stmt = chat_count_stmt.where(UserChatAchievementModel.chat_id == chat_id)
        global_count_stmt = select(func.count()).select_from(UserGlobalAchievementModel).where(UserGlobalAchievementModel.user_id == user_id)
        chat_count = (await self._session.execute(chat_count_stmt)).scalar_one()
        global_count = (await self._session.execute(global_count_stmt)).scalar_one()
        return int(chat_count or 0) + int(global_count or 0)

    async def count_owned_pets(self, *, user_id: int, chat_id: int | None = None) -> int:
        stmt = (
            select(func.count(RelationshipGraphModel.id))
            .where(
                RelationshipGraphModel.relation_type == "pet",
                RelationshipGraphModel.user_a == user_id,
            )
        )
        if chat_id is not None:
            stmt = stmt.where(RelationshipGraphModel.chat_id == chat_id)
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def count_pet_owners(self, *, user_id: int, chat_id: int | None = None) -> int:
        stmt = (
            select(func.count(RelationshipGraphModel.id))
            .where(
                RelationshipGraphModel.relation_type == "pet",
                RelationshipGraphModel.user_b == user_id,
            )
        )
        if chat_id is not None:
            stmt = stmt.where(RelationshipGraphModel.chat_id == chat_id)
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def list_user_chat_achievements(self, *, chat_id: int, user_id: int) -> list[AchievementAward]:
        stmt = (
            select(UserChatAchievementModel)
            .where(
                UserChatAchievementModel.chat_id == chat_id,
                UserChatAchievementModel.user_id == user_id,
            )
            .order_by(UserChatAchievementModel.awarded_at.desc(), UserChatAchievementModel.id.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            AchievementAward(
                achievement_id=row.achievement_id,
                scope="chat",
                awarded_at=row.awarded_at,
                award_reason=row.award_reason,
                meta_json=row.meta_json,
            )
            for row in rows
        ]

    async def list_user_global_achievements(self, *, user_id: int) -> list[AchievementAward]:
        stmt = (
            select(UserGlobalAchievementModel)
            .where(UserGlobalAchievementModel.user_id == user_id)
            .order_by(UserGlobalAchievementModel.awarded_at.desc(), UserGlobalAchievementModel.id.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            AchievementAward(
                achievement_id=row.achievement_id,
                scope="global",
                awarded_at=row.awarded_at,
                award_reason=row.award_reason,
                meta_json=row.meta_json,
            )
            for row in rows
        ]

    async def get_chat_achievement_stats_map(self, *, chat_id: int) -> dict[str, tuple[int, float]]:
        stmt = select(ChatAchievementStatsModel).where(ChatAchievementStatsModel.chat_id == chat_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        current_base_count = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(UserChatActivityModel).where(
                        UserChatActivityModel.chat_id == chat_id,
                        UserChatActivityModel.is_active_member.is_(True),
                    )
                )
            ).scalar_one()
            or 0
        )
        return {
            row.achievement_id: (
                int(row.holders_count),
                float(
                    compute_holders_percent(
                        holders_count=int(row.holders_count or 0),
                        base_count=current_base_count or int(row.active_members_base_count or 0),
                    )
                ),
            )
            for row in rows
        }

    async def get_global_achievement_stats_map(self) -> dict[str, tuple[int, float]]:
        current_base_count = int((await self._session.execute(select(func.count()).select_from(UserModel))).scalar_one() or 0)
        rows = (await self._session.execute(select(GlobalAchievementStatsModel))).scalars().all()
        return {
            row.achievement_id: (
                int(row.holders_count),
                float(
                    compute_holders_percent(
                        holders_count=int(row.holders_count or 0),
                        base_count=current_base_count or int(row.global_base_count or 0),
                    )
                ),
            )
            for row in rows
        }

    async def rebuild_chat_achievement_state(self, *, chat_id: int) -> None:
        active_count_stmt = select(func.count()).select_from(UserChatActivityModel).where(
            UserChatActivityModel.chat_id == chat_id,
            UserChatActivityModel.is_active_member.is_(True),
        )
        active_count = int((await self._session.execute(active_count_stmt)).scalar_one() or 0)
        await set_chat_active_members_count(self._session, chat_id=chat_id, active_members_count=active_count)

        rows = (
            await self._session.execute(
                select(
                    UserChatAchievementModel.achievement_id,
                    func.count(UserChatAchievementModel.id),
                )
                .where(UserChatAchievementModel.chat_id == chat_id)
                .group_by(UserChatAchievementModel.achievement_id)
            )
        ).all()
        await self._session.execute(delete(ChatAchievementStatsModel).where(ChatAchievementStatsModel.chat_id == chat_id))
        now = datetime.now(timezone.utc)
        for achievement_id, holders_count in rows:
            self._session.add(
                ChatAchievementStatsModel(
                    chat_id=chat_id,
                    achievement_id=achievement_id,
                    holders_count=int(holders_count or 0),
                    active_members_base_count=active_count,
                    holders_percent=compute_holders_percent(
                        holders_count=int(holders_count or 0),
                        base_count=active_count,
                    ),
                    updated_at=now,
                )
            )
        await self._session.flush()

    async def rebuild_global_achievement_state(self) -> None:
        users_count_stmt = select(func.count()).select_from(UserModel)
        base_count = int((await self._session.execute(users_count_stmt)).scalar_one() or 0)
        await set_global_users_base_count(self._session, base_count=base_count)

        rows = (
            await self._session.execute(
                select(
                    UserGlobalAchievementModel.achievement_id,
                    func.count(UserGlobalAchievementModel.id),
                )
                .group_by(UserGlobalAchievementModel.achievement_id)
            )
        ).all()
        await self._session.execute(delete(GlobalAchievementStatsModel))
        now = datetime.now(timezone.utc)
        for achievement_id, holders_count in rows:
            self._session.add(
                GlobalAchievementStatsModel(
                    achievement_id=achievement_id,
                    holders_count=int(holders_count or 0),
                    global_base_count=base_count,
                    holders_percent=compute_holders_percent(
                        holders_count=int(holders_count or 0),
                        base_count=base_count,
                    ),
                    updated_at=now,
                )
            )
        await self._session.flush()

    async def get_user_activity_daily_series(self, *, chat_id: int, user_id: int, days: int) -> list[tuple[date, int]]:
        normalized_days = max(1, int(days))
        today = datetime.now(timezone.utc).date()
        start_date = today - timedelta(days=normalized_days - 1)

        stmt = (
            select(
                UserChatActivityDailyModel.activity_date,
                UserChatActivityDailyModel.message_count,
            )
            .where(
                UserChatActivityDailyModel.chat_id == chat_id,
                UserChatActivityDailyModel.user_id == user_id,
                UserChatActivityDailyModel.activity_date >= start_date,
            )
            .order_by(UserChatActivityDailyModel.activity_date.asc())
        )
        rows = (await self._session.execute(stmt)).all()
        counts_by_day = {activity_date: int(message_count) for activity_date, message_count in rows}

        values: list[tuple[date, int]] = []
        for offset in range(normalized_days):
            day = start_date + timedelta(days=offset)
            values.append((day, int(counts_by_day.get(day, 0))))
        return values

    async def get_chat_activity_summary(self, *, chat_id: int) -> ChatActivitySummary:
        stmt = select(
            func.count(UserChatActivityModel.user_id),
            func.coalesce(func.sum(UserChatActivityModel.message_count), 0),
            func.max(UserChatActivityModel.last_seen_at),
        ).where(UserChatActivityModel.chat_id == chat_id)
        participants_count, total_messages, last_activity_at = (await self._session.execute(stmt)).one()
        return ChatActivitySummary(
            chat_id=chat_id,
            participants_count=int(participants_count or 0),
            total_messages=int(total_messages or 0),
            last_activity_at=last_activity_at,
        )

    async def get_top(self, *, chat_id: int, limit: int) -> list[ActivityStats]:
        stmt = (
            select(UserChatActivityModel, UserModel)
            .join(UserModel, UserModel.telegram_user_id == UserChatActivityModel.user_id)
            .where(UserChatActivityModel.chat_id == chat_id)
            .order_by(
                UserChatActivityModel.message_count.desc(),
                UserChatActivityModel.last_seen_at.desc(),
                UserChatActivityModel.user_id.asc(),
            )
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [self._to_stats(activity, user) for activity, user in rows]

    async def get_last_seen(self, *, chat_id: int, user_id: int) -> datetime | None:
        stmt = select(UserChatActivityModel.last_seen_at).where(
            UserChatActivityModel.chat_id == chat_id,
            UserChatActivityModel.user_id == user_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_chat_settings(self, *, chat_id: int) -> ChatSettings | None:
        row = await self._session.get(ChatSettingsModel, chat_id)
        if row is None:
            return None

        return ChatSettings(
            top_limit_default=int(row.top_limit_default),
            top_limit_max=int(row.top_limit_max),
            vote_daily_limit=int(row.vote_daily_limit),
            leaderboard_hybrid_buttons_enabled=bool(row.leaderboard_hybrid_buttons_enabled),
            leaderboard_hybrid_karma_weight=float(row.leaderboard_hybrid_karma_weight),
            leaderboard_hybrid_activity_weight=float(row.leaderboard_hybrid_activity_weight),
            leaderboard_7d_days=int(row.leaderboard_7d_days),
            leaderboard_week_start_weekday=int(row.leaderboard_week_start_weekday),
            leaderboard_week_start_hour=int(row.leaderboard_week_start_hour),
            mafia_night_seconds=int(row.mafia_night_seconds),
            mafia_day_seconds=int(row.mafia_day_seconds),
            mafia_vote_seconds=int(row.mafia_vote_seconds),
            mafia_reveal_eliminated_role=bool(row.mafia_reveal_eliminated_role),
            text_commands_enabled=bool(row.text_commands_enabled),
            text_commands_locale=row.text_commands_locale,
            actions_18_enabled=bool(row.actions_18_enabled),
            smart_triggers_enabled=bool(row.smart_triggers_enabled),
            welcome_enabled=bool(row.welcome_enabled),
            welcome_text=row.welcome_text,
            welcome_button_text=row.welcome_button_text,
            welcome_button_url=row.welcome_button_url,
            goodbye_enabled=bool(row.goodbye_enabled),
            goodbye_text=row.goodbye_text,
            welcome_cleanup_service_messages=bool(row.welcome_cleanup_service_messages),
            entry_captcha_enabled=bool(row.entry_captcha_enabled),
            entry_captcha_timeout_seconds=int(row.entry_captcha_timeout_seconds),
            entry_captcha_kick_on_fail=bool(row.entry_captcha_kick_on_fail),
            custom_rp_enabled=bool(row.custom_rp_enabled),
            family_tree_enabled=bool(row.family_tree_enabled),
            titles_enabled=bool(row.titles_enabled),
            title_price=int(row.title_price),
            craft_enabled=bool(row.craft_enabled),
            auctions_enabled=bool(row.auctions_enabled),
            auction_duration_minutes=int(row.auction_duration_minutes),
            auction_min_increment=int(row.auction_min_increment),
            economy_enabled=bool(row.economy_enabled),
            economy_mode=row.economy_mode,
            economy_tap_cooldown_seconds=int(row.economy_tap_cooldown_seconds),
            economy_daily_base_reward=int(row.economy_daily_base_reward),
            economy_daily_streak_cap=int(row.economy_daily_streak_cap),
            economy_lottery_ticket_price=int(row.economy_lottery_ticket_price),
            economy_lottery_paid_daily_limit=int(row.economy_lottery_paid_daily_limit),
            economy_transfer_daily_limit=int(row.economy_transfer_daily_limit),
            economy_transfer_tax_percent=int(row.economy_transfer_tax_percent),
            economy_market_fee_percent=int(row.economy_market_fee_percent),
            economy_negative_event_chance_percent=int(row.economy_negative_event_chance_percent),
            economy_negative_event_loss_percent=int(row.economy_negative_event_loss_percent),
            cleanup_economy_commands=bool(row.cleanup_economy_commands),
        )

    async def upsert_chat_settings(
        self,
        *,
        chat: ChatSnapshot,
        values: dict[str, object],
    ) -> ChatSettings:
        await self._upsert_chat(chat)

        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"
        if dialect == "postgresql":
            stmt = pg_insert(ChatSettingsModel).values(chat_id=chat.telegram_chat_id, **values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[ChatSettingsModel.chat_id],
                set_={**values, "updated_at": func.now()},
            )
            await self._session.execute(stmt)
        else:
            row = await self._session.get(ChatSettingsModel, chat.telegram_chat_id)
            if row is None:
                row = ChatSettingsModel(chat_id=chat.telegram_chat_id, **values)
                self._session.add(row)
            else:
                for key, value in values.items():
                    setattr(row, key, value)
                row.updated_at = datetime.now(timezone.utc)

        await self._session.flush()
        settings = await self.get_chat_settings(chat_id=chat.telegram_chat_id)
        if settings is None:
            raise RuntimeError("Failed to load chat settings after upsert")
        return settings

    async def get_chat_alias_mode(self, *, chat_id: int) -> TextAliasMode:
        row = await self._session.get(ChatTextAliasSettingsModel, chat_id)
        if row is None:
            return ALIAS_MODE_DEFAULT

        raw_mode = str(row.mode).strip().lower()
        if raw_mode not in ALIAS_MODE_VALUES:
            return ALIAS_MODE_DEFAULT
        return raw_mode  # type: ignore[return-value]

    async def set_chat_alias_mode(self, *, chat: ChatSnapshot, mode: TextAliasMode) -> TextAliasMode:
        raw_mode = str(mode).strip().lower()
        if raw_mode not in ALIAS_MODE_VALUES:
            raise ValueError("Unsupported alias mode")

        await self._upsert_chat(chat)
        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"
        if dialect == "postgresql":
            stmt = pg_insert(ChatTextAliasSettingsModel).values(chat_id=chat.telegram_chat_id, mode=raw_mode)
            stmt = stmt.on_conflict_do_update(
                index_elements=[ChatTextAliasSettingsModel.chat_id],
                set_={"mode": raw_mode, "updated_at": func.now()},
            )
            await self._session.execute(stmt)
        else:
            row = await self._session.get(ChatTextAliasSettingsModel, chat.telegram_chat_id)
            if row is None:
                row = ChatTextAliasSettingsModel(chat_id=chat.telegram_chat_id, mode=raw_mode)
                self._session.add(row)
            else:
                row.mode = raw_mode
                row.updated_at = datetime.now(timezone.utc)

        await self._session.flush()
        return raw_mode  # type: ignore[return-value]

    async def list_chat_aliases(self, *, chat_id: int) -> list[ChatTextAlias]:
        stmt = (
            select(ChatTextAliasModel)
            .where(ChatTextAliasModel.chat_id == chat_id)
            .order_by(ChatTextAliasModel.alias_text_norm.asc(), ChatTextAliasModel.id.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_chat_text_alias(row) for row in rows]

    async def upsert_chat_alias(
        self,
        *,
        chat: ChatSnapshot,
        command_key: str,
        source_trigger_norm: str,
        alias_text_norm: str,
        actor_user_id: int | None,
        force: bool,
    ) -> ChatTextAliasUpsertResult:
        await self._upsert_chat(chat)
        if actor_user_id is not None:
            await self._upsert_user(
                UserSnapshot(
                    telegram_user_id=actor_user_id,
                    username=None,
                    first_name=None,
                    last_name=None,
                    is_bot=False,
                )
            )

        stmt = select(ChatTextAliasModel).where(
            ChatTextAliasModel.chat_id == chat.telegram_chat_id,
            ChatTextAliasModel.alias_text_norm == alias_text_norm,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = ChatTextAliasModel(
                chat_id=chat.telegram_chat_id,
                command_key=command_key,
                alias_text_norm=alias_text_norm,
                source_trigger_norm=source_trigger_norm,
                created_by_user_id=actor_user_id,
            )
            self._session.add(row)
            await self._session.flush()
            return ChatTextAliasUpsertResult(
                alias=self._to_chat_text_alias(row),
                conflict_alias=None,
                created=True,
                reassigned=False,
            )

        if row.command_key != command_key:
            if not force:
                return ChatTextAliasUpsertResult(
                    alias=None,
                    conflict_alias=self._to_chat_text_alias(row),
                    created=False,
                    reassigned=False,
                )
            row.command_key = command_key
            row.source_trigger_norm = source_trigger_norm
            row.created_by_user_id = actor_user_id
            row.updated_at = datetime.now(timezone.utc)
            await self._session.flush()
            return ChatTextAliasUpsertResult(
                alias=self._to_chat_text_alias(row),
                conflict_alias=None,
                created=False,
                reassigned=True,
            )

        row.source_trigger_norm = source_trigger_norm
        row.created_by_user_id = actor_user_id
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return ChatTextAliasUpsertResult(
            alias=self._to_chat_text_alias(row),
            conflict_alias=None,
            created=False,
            reassigned=False,
        )

    async def remove_chat_alias(self, *, chat_id: int, alias_text_norm: str) -> bool:
        stmt = select(ChatTextAliasModel).where(
            ChatTextAliasModel.chat_id == chat_id,
            ChatTextAliasModel.alias_text_norm == alias_text_norm,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def list_chat_triggers(self, *, chat_id: int) -> list[ChatTrigger]:
        stmt = (
            select(ChatTriggerModel)
            .where(ChatTriggerModel.chat_id == chat_id)
            .order_by(ChatTriggerModel.keyword_norm.asc(), ChatTriggerModel.id.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_chat_trigger(row) for row in rows]

    async def get_chat_trigger(self, *, trigger_id: int) -> ChatTrigger | None:
        row = await self._session.get(ChatTriggerModel, trigger_id)
        if row is None:
            return None
        return self._to_chat_trigger(row)

    async def upsert_chat_trigger(
        self,
        *,
        chat: ChatSnapshot,
        trigger_id: int | None,
        keyword: str,
        match_type: str,
        response_text: str | None,
        media_file_id: str | None,
        media_type: str | None,
        actor_user_id: int | None,
    ) -> ChatTrigger:
        normalized_keyword = _normalize_free_text(keyword)
        if not normalized_keyword:
            raise ValueError("Ключ триггера не должен быть пустым.")
        if match_type not in {"exact", "contains", "starts_with"}:
            raise ValueError("match_type должен быть exact/contains/starts_with.")
        normalized_response = (response_text or "").strip() or None
        normalized_media_file_id = (media_file_id or "").strip() or None
        normalized_media_type = (media_type or "").strip().lower() or None
        if normalized_response is None and normalized_media_file_id is None:
            raise ValueError("Нужно указать текст ответа или media_file_id.")
        validate_template_variables(normalized_response)

        await self._upsert_chat(chat)
        if actor_user_id is not None:
            await self._upsert_user(
                UserSnapshot(
                    telegram_user_id=actor_user_id,
                    username=None,
                    first_name=None,
                    last_name=None,
                    is_bot=False,
                )
            )

        row: ChatTriggerModel | None = None
        if trigger_id is not None:
            candidate = await self._session.get(ChatTriggerModel, trigger_id)
            if candidate is not None and int(candidate.chat_id) == chat.telegram_chat_id:
                row = candidate

        if row is None:
            stmt = select(ChatTriggerModel).where(
                ChatTriggerModel.chat_id == chat.telegram_chat_id,
                ChatTriggerModel.keyword_norm == normalized_keyword,
                ChatTriggerModel.match_type == match_type,
            )
            row = (await self._session.execute(stmt)).scalar_one_or_none()

        if row is None:
            row = ChatTriggerModel(
                chat_id=chat.telegram_chat_id,
                keyword=keyword.strip(),
                keyword_norm=normalized_keyword,
                match_type=match_type,
                response_text=normalized_response,
                media_file_id=normalized_media_file_id,
                media_type=normalized_media_type,
                created_by_user_id=actor_user_id,
            )
            self._session.add(row)
        else:
            row.keyword = keyword.strip()
            row.keyword_norm = normalized_keyword
            row.match_type = match_type
            row.response_text = normalized_response
            row.media_file_id = normalized_media_file_id
            row.media_type = normalized_media_type
            row.created_by_user_id = actor_user_id
            row.updated_at = datetime.now(timezone.utc)

        await self._session.flush()
        return self._to_chat_trigger(row)

    async def remove_chat_trigger(self, *, chat_id: int, trigger_id: int) -> bool:
        row = await self._session.get(ChatTriggerModel, trigger_id)
        if row is None or int(row.chat_id) != chat_id:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def list_custom_social_actions(self, *, chat_id: int) -> list[CustomSocialAction]:
        stmt = (
            select(ChatCustomSocialActionModel)
            .where(ChatCustomSocialActionModel.chat_id == chat_id)
            .order_by(ChatCustomSocialActionModel.trigger_text_norm.asc(), ChatCustomSocialActionModel.id.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_custom_social_action(row) for row in rows]

    async def get_custom_social_action(self, *, chat_id: int, trigger_text_norm: str) -> CustomSocialAction | None:
        normalized_trigger = _normalize_free_text(trigger_text_norm)
        if not normalized_trigger:
            return None
        stmt = select(ChatCustomSocialActionModel).where(
            ChatCustomSocialActionModel.chat_id == chat_id,
            ChatCustomSocialActionModel.trigger_text_norm == normalized_trigger,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._to_custom_social_action(row) if row is not None else None

    async def upsert_custom_social_action(
        self,
        *,
        chat: ChatSnapshot,
        trigger_text: str,
        response_template: str,
        actor_user_id: int | None,
    ) -> CustomSocialAction:
        normalized_trigger = _normalize_free_text(trigger_text)
        normalized_template = (response_template or "").strip()
        if not normalized_trigger:
            raise ValueError("Триггер действия не должен быть пустым.")
        if not normalized_template:
            raise ValueError("Шаблон действия не должен быть пустым.")
        validate_template_variables(normalized_template)

        await self._upsert_chat(chat)
        if actor_user_id is not None:
            await self._upsert_user(
                UserSnapshot(
                    telegram_user_id=actor_user_id,
                    username=None,
                    first_name=None,
                    last_name=None,
                    is_bot=False,
                )
            )

        stmt = select(ChatCustomSocialActionModel).where(
            ChatCustomSocialActionModel.chat_id == chat.telegram_chat_id,
            ChatCustomSocialActionModel.trigger_text_norm == normalized_trigger,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = ChatCustomSocialActionModel(
                chat_id=chat.telegram_chat_id,
                trigger_text=trigger_text.strip(),
                trigger_text_norm=normalized_trigger,
                response_template=normalized_template,
                created_by_user_id=actor_user_id,
            )
            self._session.add(row)
        else:
            row.trigger_text = trigger_text.strip()
            row.trigger_text_norm = normalized_trigger
            row.response_template = normalized_template
            row.created_by_user_id = actor_user_id
            row.updated_at = datetime.now(timezone.utc)

        await self._session.flush()
        return self._to_custom_social_action(row)

    async def remove_custom_social_action(self, *, chat_id: int, trigger_text_norm: str) -> bool:
        normalized_trigger = _normalize_free_text(trigger_text_norm)
        if not normalized_trigger:
            return False
        stmt = select(ChatCustomSocialActionModel).where(
            ChatCustomSocialActionModel.chat_id == chat_id,
            ChatCustomSocialActionModel.trigger_text_norm == normalized_trigger,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def upsert_graph_relationship(
        self,
        *,
        chat: ChatSnapshot,
        user_a: UserSnapshot,
        user_b: UserSnapshot,
        relation_type: GraphRelationType,
        actor_user_id: int | None,
    ) -> GraphRelationship:
        if relation_type not in {"spouse", "parent", "child", "pet"}:
            raise ValueError("Некорректный тип связи.")

        normalized_type = relation_type
        left = user_a
        right = user_b
        if normalized_type == "child":
            normalized_type = "parent"
            left, right = user_b, user_a
        if normalized_type == "spouse" and left.telegram_user_id > right.telegram_user_id:
            left, right = right, left
        if normalized_type == "parent":
            error = await self.validate_parent_link(
                chat_id=chat.telegram_chat_id,
                actor_user_id=left.telegram_user_id,
                target_user_id=right.telegram_user_id,
            )
            if error:
                raise ValueError(error)

        await self._upsert_chat(chat)
        await self._upsert_user(left)
        await self._upsert_user(right)
        if actor_user_id is not None:
            await self._upsert_user(
                UserSnapshot(
                    telegram_user_id=actor_user_id,
                    username=None,
                    first_name=None,
                    last_name=None,
                    is_bot=False,
                )
            )

        stmt = select(RelationshipGraphModel).where(
            RelationshipGraphModel.chat_id == chat.telegram_chat_id,
            RelationshipGraphModel.user_a == left.telegram_user_id,
            RelationshipGraphModel.user_b == right.telegram_user_id,
            RelationshipGraphModel.relation_type == normalized_type,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = RelationshipGraphModel(
                chat_id=chat.telegram_chat_id,
                user_a=left.telegram_user_id,
                user_b=right.telegram_user_id,
                relation_type=normalized_type,
                created_by_user_id=actor_user_id,
            )
            self._session.add(row)
        else:
            row.created_by_user_id = actor_user_id
            row.updated_at = datetime.now(timezone.utc)

        await self._session.flush()
        return self._to_graph_relationship(row)

    async def remove_graph_relationship(
        self,
        *,
        chat_id: int,
        user_a: int,
        user_b: int,
        relation_type: GraphRelationType,
    ) -> bool:
        normalized_type = relation_type
        left = int(user_a)
        right = int(user_b)
        if normalized_type == "child":
            normalized_type = "parent"
            left, right = right, left
        if normalized_type == "spouse" and left > right:
            left, right = right, left

        stmt = select(RelationshipGraphModel).where(
            RelationshipGraphModel.chat_id == chat_id,
            RelationshipGraphModel.user_a == left,
            RelationshipGraphModel.user_b == right,
            RelationshipGraphModel.relation_type == normalized_type,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def list_graph_relationships(self, *, chat_id: int, user_id: int | None = None) -> list[GraphRelationship]:
        stmt = select(RelationshipGraphModel).where(RelationshipGraphModel.chat_id == chat_id)
        if user_id is not None:
            stmt = stmt.where(
                or_(
                    RelationshipGraphModel.user_a == user_id,
                    RelationshipGraphModel.user_b == user_id,
                )
            )
        stmt = stmt.order_by(RelationshipGraphModel.created_at.asc(), RelationshipGraphModel.id.asc())
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_graph_relationship(row) for row in rows]

    async def validate_parent_link(
        self,
        *,
        chat_id: int,
        actor_user_id: int,
        target_user_id: int,
    ) -> str | None:
        if int(actor_user_id) == int(target_user_id):
            return "Нельзя усыновить самого себя."

        parent_rows = (
            await self._session.execute(
                select(RelationshipGraphModel.id, RelationshipGraphModel.user_a)
                .where(
                    RelationshipGraphModel.chat_id == chat_id,
                    RelationshipGraphModel.relation_type == "parent",
                    RelationshipGraphModel.user_b == target_user_id,
                )
                .with_for_update()
            )
        ).all()
        current_parents = {int(parent_id) for _row_id, parent_id in parent_rows}
        if actor_user_id in current_parents:
            return "Эта семейная связь уже существует."
        if len(parent_rows) >= 2:
            return "У ребёнка уже есть два родителя."

        descendants = (
            select(RelationshipGraphModel.user_b.label("user_id"))
            .where(
                RelationshipGraphModel.chat_id == chat_id,
                RelationshipGraphModel.relation_type == "parent",
                RelationshipGraphModel.user_a == target_user_id,
            )
            .cte(name="family_descendants", recursive=True)
        )
        descendants_step = aliased(RelationshipGraphModel)
        descendants = descendants.union_all(
            select(descendants_step.user_b.label("user_id")).where(
                descendants_step.chat_id == chat_id,
                descendants_step.relation_type == "parent",
                descendants_step.user_a == descendants.c.user_id,
            )
        )
        cycle_found = (
            await self._session.execute(
                select(descendants.c.user_id).where(descendants.c.user_id == actor_user_id).limit(1)
            )
        ).scalar_one_or_none()
        if cycle_found is not None:
            return "Нельзя замкнуть семейное древо в цикл."
        return None

    async def list_family_bundle(self, *, chat_id: int, user_id: int) -> FamilyBundle:
        relations = await self.list_graph_relationships(chat_id=chat_id)
        parent_edges = [item for item in relations if item.relation_type == "parent"]
        pet_edges = [item for item in relations if item.relation_type == "pet"]
        parents = sorted({item.user_a for item in parent_edges if item.user_b == user_id})
        children = sorted({item.user_b for item in parent_edges if item.user_a == user_id})
        pets = sorted({item.user_b for item in pet_edges if item.user_a == user_id})
        grandparents = sorted({item.user_a for item in parent_edges if item.user_b in parents})
        siblings = sorted(
            {
                item.user_b
                for item in parent_edges
                if item.user_a in parents and item.user_b != user_id
            }
        )

        marriage = await self.get_active_marriage(user_id=user_id, chat_id=chat_id)
        spouse_user_id = None
        if marriage is not None:
            spouse_user_id = (
                marriage.user_high_id if marriage.user_low_id == user_id else marriage.user_low_id
            )

        parent_marriages_stmt = select(MarriageModel).where(
            MarriageModel.chat_id == chat_id,
            MarriageModel.is_active.is_(True),
            or_(
                MarriageModel.user_low_id.in_(parents or [-1]),
                MarriageModel.user_high_id.in_(parents or [-1]),
            ),
        )
        parent_marriages = (await self._session.execute(parent_marriages_stmt)).scalars().all()
        step_parents: set[int] = set()
        for row in parent_marriages:
            low_id = int(row.user_low_id)
            high_id = int(row.user_high_id)
            if low_id in parents and high_id not in parents and high_id != user_id:
                step_parents.add(high_id)
            if high_id in parents and low_id not in parents and low_id != user_id:
                step_parents.add(low_id)

        return FamilyBundle(
            subject_user_id=user_id,
            spouse_user_id=spouse_user_id,
            parents=tuple(parents),
            grandparents=tuple(grandparents),
            step_parents=tuple(sorted(step_parents)),
            siblings=tuple(siblings),
            children=tuple(children),
            pets=tuple(pets),
        )

    async def list_family_graph(self, *, chat_id: int, user_id: int) -> FamilyGraph:
        bundle = await self.list_family_bundle(chat_id=chat_id, user_id=user_id)
        relations = await self.list_graph_relationships(chat_id=chat_id)
        parent_edges = [item for item in relations if item.relation_type == "parent"]
        pet_edges = [item for item in relations if item.relation_type == "pet"]

        node_ids: set[int] = {
            user_id,
            *bundle.parents,
            *bundle.grandparents,
            *bundle.step_parents,
            *bundle.siblings,
            *bundle.children,
            *bundle.pets,
        }
        if bundle.spouse_user_id is not None:
            node_ids.add(bundle.spouse_user_id)

        edges: list[FamilyGraphEdge] = []
        for edge in parent_edges:
            if edge.user_a in node_ids and edge.user_b in node_ids:
                edges.append(
                    FamilyGraphEdge(
                        source_user_id=edge.user_a,
                        target_user_id=edge.user_b,
                        relation_type="parent",
                        label="parent",
                    )
                )
        for edge in pet_edges:
            if edge.user_a in node_ids and edge.user_b in node_ids:
                edges.append(
                    FamilyGraphEdge(
                        source_user_id=edge.user_a,
                        target_user_id=edge.user_b,
                        relation_type="pet",
                        label="pet",
                    )
                )
        if bundle.spouse_user_id is not None:
            edges.append(
                FamilyGraphEdge(
                    source_user_id=user_id,
                    target_user_id=bundle.spouse_user_id,
                    relation_type="spouse",
                    label="spouse",
                )
            )
        for step_parent_id in bundle.step_parents:
            edges.append(
                FamilyGraphEdge(
                    source_user_id=step_parent_id,
                    target_user_id=user_id,
                    relation_type="step_parent",
                    label="step-parent",
                    is_direct=False,
                )
            )
        for sibling_id in bundle.siblings:
            edges.append(
                FamilyGraphEdge(
                    source_user_id=user_id,
                    target_user_id=sibling_id,
                    relation_type="sibling",
                    label="sibling",
                    is_direct=False,
                )
            )

        return FamilyGraph(
            focus_user_id=user_id,
            node_user_ids=tuple(sorted(node_ids)),
            edges=tuple(edges),
        )

    async def add_audit_log(
        self,
        *,
        chat: ChatSnapshot,
        action_code: str,
        description: str,
        actor_user_id: int | None = None,
        target_user_id: int | None = None,
        meta_json: dict | None = None,
    ) -> ChatAuditLogEntry:
        await self._upsert_chat(chat)
        if actor_user_id is not None:
            await self._upsert_user(
                UserSnapshot(
                    telegram_user_id=actor_user_id,
                    username=None,
                    first_name=None,
                    last_name=None,
                    is_bot=False,
                )
            )
        if target_user_id is not None:
            await self._upsert_user(
                UserSnapshot(
                    telegram_user_id=target_user_id,
                    username=None,
                    first_name=None,
                    last_name=None,
                    is_bot=False,
                )
            )

        row = ChatAuditLogModel(
            chat_id=chat.telegram_chat_id,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            action_code=_normalize_free_text(action_code).replace(" ", "_")[:64] or "event",
            description=(description or "").strip()[:2000],
            meta_json=meta_json or None,
        )
        self._session.add(row)
        await self._session.flush()
        return self._to_chat_audit_log(row)

    async def list_audit_logs(self, *, chat_id: int, limit: int = 100) -> list[ChatAuditLogEntry]:
        normalized_limit = max(1, min(int(limit), 500))
        stmt = (
            select(ChatAuditLogModel)
            .where(ChatAuditLogModel.chat_id == chat_id)
            .order_by(ChatAuditLogModel.created_at.desc(), ChatAuditLogModel.id.desc())
            .limit(normalized_limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_chat_audit_log(row) for row in rows]

    async def record_vote(
        self,
        *,
        chat: ChatSnapshot,
        voter: UserSnapshot,
        target: UserSnapshot,
        vote_value: int,
        event_at: datetime,
    ) -> None:
        await self._upsert_chat(chat)
        await self._upsert_user(voter)
        await self._upsert_user(target)

        self._session.add(
            UserKarmaVoteModel(
                chat_id=chat.telegram_chat_id,
                voter_user_id=voter.telegram_user_id,
                target_user_id=target.telegram_user_id,
                vote_value=vote_value,
                created_at=event_at,
            )
        )
        await self._session.flush()

    async def count_votes_by_voter_since(self, *, chat_id: int, voter_user_id: int, since: datetime) -> int:
        stmt = select(func.count(UserKarmaVoteModel.id)).where(
            UserKarmaVoteModel.chat_id == chat_id,
            UserKarmaVoteModel.voter_user_id == voter_user_id,
            UserKarmaVoteModel.created_at >= since,
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def get_karma_value(
        self,
        *,
        chat_id: int,
        user_id: int,
        period: LeaderboardPeriod,
        since: datetime | None = None,
    ) -> int:
        stmt = select(func.coalesce(func.sum(UserKarmaVoteModel.vote_value), 0)).where(
            UserKarmaVoteModel.chat_id == chat_id,
            UserKarmaVoteModel.target_user_id == user_id,
        )
        if period != "all" and since is not None:
            stmt = stmt.where(UserKarmaVoteModel.created_at >= since)

        karma_value = int((await self._session.execute(stmt)).scalar_one() or 0)
        if period == "all":
            karma_value += await self._get_iris_karma_base(chat_id=chat_id, user_id=user_id)
        return karma_value

    async def get_representation_stats(
        self,
        *,
        chat_id: int,
        user_id: int,
        since: datetime | None = None,
    ) -> tuple[int, int, datetime | None]:
        if since is None:
            activity_stmt = select(UserChatActivityModel.message_count, UserChatActivityModel.last_seen_at).where(
                UserChatActivityModel.chat_id == chat_id,
                UserChatActivityModel.user_id == user_id,
            )
            row = (await self._session.execute(activity_stmt)).one_or_none()
            activity_value = int(row[0]) if row is not None else 0
            last_seen_at = row[1] if row is not None else None
        else:
            activity_stmt = select(
                func.coalesce(func.sum(UserChatActivityMinuteModel.message_count), 0),
                func.max(UserChatActivityMinuteModel.last_seen_at),
            ).where(
                UserChatActivityMinuteModel.chat_id == chat_id,
                UserChatActivityMinuteModel.user_id == user_id,
                UserChatActivityMinuteModel.activity_minute >= since,
            )
            row = (await self._session.execute(activity_stmt)).one()
            activity_value = int(row[0] or 0)
            last_seen_at = row[1]

        karma_stmt = select(func.coalesce(func.sum(UserKarmaVoteModel.vote_value), 0)).where(
            UserKarmaVoteModel.chat_id == chat_id,
            UserKarmaVoteModel.target_user_id == user_id,
        )
        if since is not None:
            karma_stmt = karma_stmt.where(UserKarmaVoteModel.created_at >= since)

        karma_value = int((await self._session.execute(karma_stmt)).scalar_one() or 0)
        if since is None:
            karma_value += await self._get_iris_karma_base(chat_id=chat_id, user_id=user_id)
        return activity_value, karma_value, last_seen_at

    async def get_leaderboard(
        self,
        *,
        chat_id: int,
        mode: LeaderboardMode,
        period: LeaderboardPeriod,
        since: datetime | None,
        limit: int,
        karma_weight: float,
        activity_weight: float,
    ) -> list[LeaderboardItem]:
        activity_rows = await self._get_activity_aggregate(chat_id=chat_id, period=period, since=since)
        karma_rows = await self._get_karma_aggregate(chat_id=chat_id, period=period, since=since)

        all_user_ids = set(activity_rows.keys()) | set(karma_rows.keys())
        if not all_user_ids:
            return []

        users = await self._get_users_by_ids(tuple(all_user_ids))
        display_overrides = await self._get_chat_display_overrides(chat_id=chat_id, user_ids=tuple(all_user_ids))

        max_activity = max((value[0] for value in activity_rows.values()), default=0)
        karma_values = [value for value in karma_rows.values()]
        min_karma = min(karma_values, default=0)
        max_karma = max(karma_values, default=0)

        items: list[LeaderboardItem] = []
        for user_id in all_user_ids:
            activity_value, last_seen_at = activity_rows.get(user_id, (0, None))
            karma_value = karma_rows.get(user_id, 0)

            if mode == "activity":
                hybrid_score = float(activity_value)
            elif mode == "karma":
                hybrid_score = float(karma_value)
            else:
                hybrid_score = compute_hybrid_score(
                    activity_value=activity_value,
                    karma_value=karma_value,
                    max_activity=max_activity,
                    min_karma=min_karma,
                    max_karma=max_karma,
                    karma_weight=karma_weight,
                    activity_weight=activity_weight,
                )

            user = users.get(user_id)
            items.append(
                LeaderboardItem(
                    user_id=user_id,
                    username=user.username if user else None,
                    first_name=user.first_name if user else None,
                    last_name=user.last_name if user else None,
                    activity_value=activity_value,
                    karma_value=karma_value,
                    hybrid_score=hybrid_score,
                    last_seen_at=last_seen_at,
                    chat_display_name=display_overrides.get(user_id),
                )
            )

        sorted_items = sort_leaderboard_items(items, mode=mode)
        return sorted_items[:limit]

    async def set_announcement_subscription(
        self,
        *,
        chat: ChatSnapshot,
        user: UserSnapshot,
        enabled: bool,
    ) -> None:
        await self._upsert_chat(chat)
        await self._upsert_user(user)

        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"
        if dialect == "postgresql":
            stmt = pg_insert(UserChatAnnouncementSubscriptionModel).values(
                chat_id=chat.telegram_chat_id,
                user_id=user.telegram_user_id,
                is_enabled=enabled,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    UserChatAnnouncementSubscriptionModel.chat_id,
                    UserChatAnnouncementSubscriptionModel.user_id,
                ],
                set_={"is_enabled": enabled, "updated_at": func.now()},
            )
            await self._session.execute(stmt)
            await self._session.flush()
            return

        row = await self._session.get(
            UserChatAnnouncementSubscriptionModel,
            {"chat_id": chat.telegram_chat_id, "user_id": user.telegram_user_id},
        )
        if row is None:
            self._session.add(
                UserChatAnnouncementSubscriptionModel(
                    chat_id=chat.telegram_chat_id,
                    user_id=user.telegram_user_id,
                    is_enabled=enabled,
                )
            )
        else:
            row.is_enabled = enabled
            row.updated_at = datetime.now(timezone.utc)

        await self._session.flush()

    async def get_announcement_recipients(self, *, chat_id: int) -> list[UserSnapshot]:
        prefs = UserChatAnnouncementSubscriptionModel
        activity = UserChatActivityModel
        users = UserModel

        stmt = (
            select(users, activity.display_name_override, activity.title_prefix)
            .join(activity, activity.user_id == users.telegram_user_id)
            .outerjoin(
                prefs,
                and_(
                    prefs.chat_id == activity.chat_id,
                    prefs.user_id == activity.user_id,
                ),
            )
            .where(
                activity.chat_id == chat_id,
                users.is_bot.is_(False),
                func.coalesce(prefs.is_enabled, True).is_(True),
            )
            .order_by(
                activity.last_seen_at.desc(),
                activity.message_count.desc(),
                users.telegram_user_id.asc(),
            )
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            self._to_user_snapshot(
                user,
                chat_display_name=display_name_override,
                title_prefix=title_prefix,
            )
            for user, display_name_override, title_prefix in rows
        ]

    async def set_chat_display_name(
        self,
        *,
        chat: ChatSnapshot,
        user: UserSnapshot,
        display_name: str | None,
    ) -> None:
        await self._upsert_chat(chat)
        await self._upsert_user(user)

        normalized = (display_name or "").strip() or None
        now = datetime.now(timezone.utc)
        row = await self._session.get(
            UserChatActivityModel,
            {"chat_id": chat.telegram_chat_id, "user_id": user.telegram_user_id},
        )
        if row is None:
            row = UserChatActivityModel(
                chat_id=chat.telegram_chat_id,
                user_id=user.telegram_user_id,
                message_count=0,
                last_seen_at=now,
                display_name_override=normalized,
                title_prefix=None,
            )
            self._session.add(row)
        else:
            row.display_name_override = normalized
            row.updated_at = now

        await self._session.flush()

    async def get_chat_display_name(self, *, chat_id: int, user_id: int) -> str | None:
        stmt = (
            select(
                UserModel.telegram_user_id,
                UserModel.username,
                UserModel.first_name,
                UserModel.last_name,
                UserChatActivityModel.display_name_override,
                UserChatActivityModel.title_prefix,
            )
            .join(UserChatActivityModel, UserChatActivityModel.user_id == UserModel.telegram_user_id)
            .where(
                UserChatActivityModel.chat_id == chat_id,
                UserChatActivityModel.user_id == user_id,
            )
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None
        resolved_user_id, username, first_name, last_name, display_name_override, title_prefix = row
        return self._compose_chat_display_name(
            user_id=int(resolved_user_id),
            username=username,
            first_name=first_name,
            last_name=last_name,
            chat_display_name=display_name_override,
            title_prefix=title_prefix,
        )

    async def get_chat_title_prefix(self, *, chat_id: int, user_id: int) -> str | None:
        stmt = select(UserChatActivityModel.title_prefix).where(
            UserChatActivityModel.chat_id == chat_id,
            UserChatActivityModel.user_id == user_id,
        )
        value = (await self._session.execute(stmt)).scalar_one_or_none()
        return _normalize_title_prefix(value)

    async def set_chat_title_prefix(
        self,
        *,
        chat: ChatSnapshot,
        user: UserSnapshot,
        title_prefix: str | None,
    ) -> str | None:
        await self._upsert_chat(chat)
        await self._upsert_user(user)

        normalized = _normalize_title_prefix(title_prefix)
        now = datetime.now(timezone.utc)
        row = await self._session.get(
            UserChatActivityModel,
            {"chat_id": chat.telegram_chat_id, "user_id": user.telegram_user_id},
        )
        if row is None:
            row = UserChatActivityModel(
                chat_id=chat.telegram_chat_id,
                user_id=user.telegram_user_id,
                message_count=0,
                last_seen_at=now,
                display_name_override=None,
                title_prefix=normalized,
            )
            self._session.add(row)
        else:
            row.title_prefix = normalized
            row.updated_at = now

        await self._session.flush()
        return normalized

    async def get_user_chat_profile(self, *, chat_id: int, user_id: int) -> UserChatProfile | None:
        row = await self._session.get(UserChatProfileModel, {"chat_id": chat_id, "user_id": user_id})
        if row is None:
            return None
        return self._to_user_chat_profile(row)

    async def set_user_chat_profile_description(
        self,
        *,
        chat: ChatSnapshot,
        user: UserSnapshot,
        description: str | None,
    ) -> UserChatProfile | None:
        await self._upsert_chat(chat)
        await self._upsert_user(user)

        normalized = _normalize_profile_description(description)
        row = await self._session.get(
            UserChatProfileModel,
            {"chat_id": chat.telegram_chat_id, "user_id": user.telegram_user_id},
        )
        if row is None:
            row = UserChatProfileModel(
                chat_id=chat.telegram_chat_id,
                user_id=user.telegram_user_id,
                description_text=normalized,
                avatar_frame_code=None,
                emoji_status_code=None,
            )
            self._session.add(row)
        else:
            row.description_text = normalized
            row.updated_at = datetime.now(timezone.utc)

        await self._session.flush()
        return self._to_user_chat_profile(row)

    async def add_user_chat_award(
        self,
        *,
        chat: ChatSnapshot,
        target: UserSnapshot,
        title: str,
        granted_by_user_id: int | None,
        created_at: datetime,
    ) -> UserChatAward:
        await self._upsert_chat(chat)
        await self._upsert_user(target)
        if granted_by_user_id is not None:
            await self._upsert_user(
                UserSnapshot(
                    telegram_user_id=granted_by_user_id,
                    username=None,
                    first_name=None,
                    last_name=None,
                    is_bot=False,
                )
            )

        row = UserChatAwardModel(
            chat_id=chat.telegram_chat_id,
            user_id=target.telegram_user_id,
            title=_normalize_award_title(title),
            granted_by_user_id=granted_by_user_id,
            created_at=created_at,
        )
        self._session.add(row)
        await self._session.flush()
        return self._to_user_chat_award(row)

    async def list_user_chat_awards(self, *, chat_id: int, user_id: int, limit: int = 10) -> list[UserChatAward]:
        stmt = (
            select(UserChatAwardModel)
            .where(
                UserChatAwardModel.chat_id == chat_id,
                UserChatAwardModel.user_id == user_id,
            )
            .order_by(UserChatAwardModel.created_at.desc(), UserChatAwardModel.id.desc())
            .limit(max(1, int(limit)))
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_user_chat_award(row) for row in rows]

    async def remove_user_chat_award(self, *, chat_id: int, award_id: int) -> bool:
        row = await self._session.get(UserChatAwardModel, award_id)
        if row is None or int(row.chat_id) != chat_id:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def get_user_chat_iris_import_state(self, *, chat_id: int, user_id: int) -> IrisImportState | None:
        row = await self._session.get(
            UserChatIrisImportStateModel,
            {"chat_id": chat_id, "user_id": user_id},
        )
        if row is None:
            return None
        return self._to_iris_import_state(row)

    async def apply_user_chat_iris_import(
        self,
        *,
        chat: ChatSnapshot,
        target: UserSnapshot,
        imported_by_user_id: int | None,
        source_bot_username: str,
        source_target_username: str,
        imported_at: datetime,
        profile_text: str,
        awards_text: str,
        karma_base_all_time: int,
        first_seen_at: datetime,
        last_seen_at: datetime,
        activity_1d: int,
        activity_7d: int,
        activity_30d: int,
        activity_all: int,
        awards: list[tuple[str, datetime]],
    ) -> IrisImportState:
        if not (0 <= int(activity_1d) <= int(activity_7d) <= int(activity_30d) <= int(activity_all)):
            raise ValueError("Активность Iris не проходит базовую проверку.")

        normalized_source_bot_username = (source_bot_username or "").strip().lstrip("@").lower()
        normalized_source_target_username = (source_target_username or "").strip().lstrip("@").lower()
        if not normalized_source_bot_username:
            raise ValueError("Не указан username исходного бота.")
        if not normalized_source_target_username:
            raise ValueError("Не указан username целевого пользователя Iris.")

        normalized_imported_at = _coerce_utc_datetime(imported_at)
        normalized_first_seen_at = _coerce_utc_datetime(first_seen_at)
        normalized_last_seen_at = _latest_datetime(last_seen_at, normalized_first_seen_at)
        normalized_awards = [
            (_normalize_award_title(title), _coerce_utc_datetime(created_at))
            for title, created_at in awards
        ]

        await self._upsert_chat(chat)
        await self._upsert_user(target)
        if imported_by_user_id is not None:
            await self._upsert_user(
                UserSnapshot(
                    telegram_user_id=imported_by_user_id,
                    username=None,
                    first_name=None,
                    last_name=None,
                    is_bot=False,
                )
            )

        existing_state = await self._session.get(
            UserChatIrisImportStateModel,
            {"chat_id": chat.telegram_chat_id, "user_id": target.telegram_user_id},
        )
        if existing_state is not None:
            raise ValueError("Профиль Iris уже был перенесён для этого пользователя.")

        existing_activity = await self._session.get(
            UserChatActivityModel,
            {"chat_id": chat.telegram_chat_id, "user_id": target.telegram_user_id},
        )
        previous_was_active = bool(existing_activity.is_active_member) if existing_activity is not None else False

        daily_stmt = (
            select(UserChatActivityDailyModel)
            .where(
                UserChatActivityDailyModel.chat_id == chat.telegram_chat_id,
                UserChatActivityDailyModel.user_id == target.telegram_user_id,
            )
            .order_by(UserChatActivityDailyModel.activity_date.asc())
        )
        existing_daily_rows = (await self._session.execute(daily_stmt)).scalars().all()

        minute_stmt = (
            select(UserChatActivityMinuteModel)
            .where(
                UserChatActivityMinuteModel.chat_id == chat.telegram_chat_id,
                UserChatActivityMinuteModel.user_id == target.telegram_user_id,
            )
            .order_by(UserChatActivityMinuteModel.activity_minute.asc())
        )
        existing_minute_rows = (await self._session.execute(minute_stmt)).scalars().all()

        awards_stmt = (
            select(UserChatAwardModel)
            .where(
                UserChatAwardModel.chat_id == chat.telegram_chat_id,
                UserChatAwardModel.user_id == target.telegram_user_id,
            )
            .order_by(UserChatAwardModel.created_at.desc(), UserChatAwardModel.id.desc())
        )
        existing_awards = (await self._session.execute(awards_stmt)).scalars().all()

        archived_snapshot_json = {
            "activity_row": (
                {
                    "message_count": int(existing_activity.message_count),
                    "is_active_member": bool(existing_activity.is_active_member),
                    "created_at": _serialize_datetime(existing_activity.created_at),
                    "last_seen_at": _serialize_datetime(existing_activity.last_seen_at),
                    "display_name_override": existing_activity.display_name_override,
                    "title_prefix": existing_activity.title_prefix,
                }
                if existing_activity is not None
                else None
            ),
            "daily_rows": [
                {
                    "activity_date": row.activity_date.isoformat(),
                    "message_count": int(row.message_count),
                    "last_seen_at": _serialize_datetime(row.last_seen_at),
                }
                for row in existing_daily_rows
            ],
            "minute_rows_summary": {
                "count": len(existing_minute_rows),
                "first_activity_minute": (
                    _serialize_datetime(existing_minute_rows[0].activity_minute) if existing_minute_rows else None
                ),
                "last_activity_minute": (
                    _serialize_datetime(existing_minute_rows[-1].activity_minute) if existing_minute_rows else None
                ),
                "total_messages": sum(int(row.message_count) for row in existing_minute_rows),
            },
            "awards": [
                {
                    "id": int(row.id),
                    "title": row.title,
                    "granted_by_user_id": int(row.granted_by_user_id) if row.granted_by_user_id is not None else None,
                    "created_at": _serialize_datetime(row.created_at),
                }
                for row in existing_awards
            ],
        }

        synthetic_daily_rows = _build_synthetic_activity_daily_rows(
            imported_at=normalized_imported_at,
            last_seen_at=normalized_last_seen_at,
            activity_1d=int(activity_1d),
            activity_7d=int(activity_7d),
            activity_30d=int(activity_30d),
        )
        synthetic_minute_rows = _build_synthetic_activity_minute_rows(daily_rows=synthetic_daily_rows)
        imported_snapshot_json = {
            "source_bot_username": normalized_source_bot_username,
            "source_target_username": normalized_source_target_username,
            "imported_by_user_id": int(imported_by_user_id) if imported_by_user_id is not None else None,
            "karma_base_all_time": int(karma_base_all_time),
            "first_seen_at": _serialize_datetime(normalized_first_seen_at),
            "last_seen_at": _serialize_datetime(normalized_last_seen_at),
            "activity": {
                "1d": int(activity_1d),
                "7d": int(activity_7d),
                "30d": int(activity_30d),
                "all": int(activity_all),
            },
            "daily_rows": [
                {
                    "activity_date": activity_date.isoformat(),
                    "message_count": int(message_count),
                    "last_seen_at": _serialize_datetime(row_last_seen),
                }
                for activity_date, message_count, row_last_seen in synthetic_daily_rows
            ],
            "awards": [
                {
                    "title": title,
                    "created_at": _serialize_datetime(created_at),
                }
                for title, created_at in normalized_awards
            ],
        }

        state_row = UserChatIrisImportStateModel(
            chat_id=chat.telegram_chat_id,
            user_id=target.telegram_user_id,
            imported_at=normalized_imported_at,
            imported_by_user_id=imported_by_user_id,
            source_bot_username=normalized_source_bot_username,
            source_target_username=normalized_source_target_username,
            karma_base_all_time=int(karma_base_all_time),
        )
        self._session.add(state_row)
        self._session.add(
            UserChatIrisImportHistoryModel(
                chat_id=chat.telegram_chat_id,
                user_id=target.telegram_user_id,
                imported_at=normalized_imported_at,
                archived_snapshot_json=archived_snapshot_json,
                imported_snapshot_json=imported_snapshot_json,
                raw_profile_text=profile_text,
                raw_awards_text=awards_text,
            )
        )

        if existing_activity is None:
            self._session.add(
                UserChatActivityModel(
                    chat_id=chat.telegram_chat_id,
                    user_id=target.telegram_user_id,
                    message_count=int(activity_all),
                    is_active_member=True,
                    last_seen_at=normalized_last_seen_at,
                    display_name_override=None,
                    title_prefix=None,
                    created_at=normalized_first_seen_at,
                    updated_at=normalized_imported_at,
                )
            )
        else:
            existing_activity.message_count = int(activity_all)
            existing_activity.is_active_member = True
            existing_activity.created_at = normalized_first_seen_at
            existing_activity.last_seen_at = normalized_last_seen_at
            existing_activity.updated_at = normalized_imported_at

        await self._session.execute(
            delete(UserChatActivityDailyModel).where(
                UserChatActivityDailyModel.chat_id == chat.telegram_chat_id,
                UserChatActivityDailyModel.user_id == target.telegram_user_id,
            )
        )
        await self._session.execute(
            delete(UserChatActivityMinuteModel).where(
                UserChatActivityMinuteModel.chat_id == chat.telegram_chat_id,
                UserChatActivityMinuteModel.user_id == target.telegram_user_id,
            )
        )

        for activity_date, message_count, row_last_seen in synthetic_daily_rows:
            self._session.add(
                UserChatActivityDailyModel(
                    chat_id=chat.telegram_chat_id,
                    user_id=target.telegram_user_id,
                    activity_date=activity_date,
                    message_count=message_count,
                    last_seen_at=row_last_seen,
                )
            )

        for activity_minute, message_count, row_last_seen in synthetic_minute_rows:
            self._session.add(
                UserChatActivityMinuteModel(
                    chat_id=chat.telegram_chat_id,
                    user_id=target.telegram_user_id,
                    activity_minute=activity_minute,
                    message_count=message_count,
                    last_seen_at=row_last_seen,
                )
            )

        for title, created_at in normalized_awards:
            self._session.add(
                UserChatAwardModel(
                    chat_id=chat.telegram_chat_id,
                    user_id=target.telegram_user_id,
                    title=title,
                    granted_by_user_id=None,
                    created_at=created_at,
                )
            )

        await self._session.flush()

        if existing_activity is None or not previous_was_active:
            await adjust_chat_active_members_count(self._session, chat_id=chat.telegram_chat_id, delta=1)

        return self._to_iris_import_state(state_row)

    @staticmethod
    def _relationship_chat_match(column, chat_id: int | None):
        return column.is_(None) if chat_id is None else column == chat_id

    async def get_active_pair(self, *, user_id: int, chat_id: int | None = None) -> PairState | None:
        stmt = select(PairModel).where(or_(PairModel.user_low_id == user_id, PairModel.user_high_id == user_id))
        if chat_id is not None:
            stmt = stmt.where(PairModel.chat_id == chat_id)
        stmt = stmt.limit(1)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return self._to_pair_state(row)

    async def get_active_marriage(self, *, user_id: int, chat_id: int | None = None) -> MarriageState | None:
        stmt = select(MarriageModel).where(
            or_(MarriageModel.user_low_id == user_id, MarriageModel.user_high_id == user_id),
            MarriageModel.is_active.is_(True),
        )
        if chat_id is not None:
            stmt = stmt.where(MarriageModel.chat_id == chat_id)
        stmt = stmt.limit(1)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return self._to_marriage_state(row)

    async def list_active_marriages(self, *, chat_id: int) -> list[MarriageState]:
        stmt = (
            select(MarriageModel)
            .where(MarriageModel.chat_id == chat_id, MarriageModel.is_active.is_(True))
            .order_by(MarriageModel.married_at.asc(), MarriageModel.id.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_marriage_state(row) for row in rows]

    async def get_active_relationship(self, *, user_id: int, chat_id: int | None = None) -> RelationshipState | None:
        marriage = await self.get_active_marriage(user_id=user_id, chat_id=chat_id)
        if marriage is not None:
            return self._relationship_state_from_marriage(marriage)
        pair = await self.get_active_pair(user_id=user_id, chat_id=chat_id)
        if pair is not None:
            return self._relationship_state_from_pair(pair)
        return None

    async def create_marriage_proposal(
        self,
        *,
        chat: ChatSnapshot,
        proposer: UserSnapshot,
        target: UserSnapshot,
        kind: RelationshipKind,
        expires_at: datetime | None,
        event_at: datetime,
    ) -> tuple[RelationshipProposal | None, str | None]:
        if proposer.telegram_user_id == target.telegram_user_id:
            return None, "Нельзя отправить предложение самому себе."
        if target.is_bot:
            return None, "Нельзя отправить предложение боту."
        if kind not in {"pair", "marriage"}:
            return None, "Некорректный тип предложения."

        await self._upsert_chat(chat)
        await self._upsert_user(proposer)
        await self._upsert_user(target)

        proposer_marriage = await self.get_active_marriage(user_id=proposer.telegram_user_id, chat_id=chat.telegram_chat_id)
        if proposer_marriage is not None:
            return None, "У вас уже есть активный брак в этой группе."
        target_marriage = await self.get_active_marriage(user_id=target.telegram_user_id, chat_id=chat.telegram_chat_id)
        if target_marriage is not None:
            return None, "У выбранного пользователя уже есть активный брак в этой группе."

        proposer_pair = await self.get_active_pair(user_id=proposer.telegram_user_id, chat_id=chat.telegram_chat_id)
        target_pair = await self.get_active_pair(user_id=target.telegram_user_id, chat_id=chat.telegram_chat_id)
        if kind == "pair":
            if proposer_pair is not None:
                return None, "У вас уже есть активные отношения в этой группе."
            if target_pair is not None:
                return None, "У выбранного пользователя уже есть активные отношения в этой группе."
        else:
            if proposer_pair is not None:
                partner_id = self._partner_id_for_pair(proposer_pair.user_low_id, proposer_pair.user_high_id, proposer.telegram_user_id)
                if partner_id != target.telegram_user_id:
                    return None, "Сначала завершите текущие отношения в этой группе."
            if target_pair is not None:
                partner_id = self._partner_id_for_pair(target_pair.user_low_id, target_pair.user_high_id, target.telegram_user_id)
                if partner_id != proposer.telegram_user_id:
                    return None, "У выбранного пользователя уже есть другие отношения в этой группе."

        user_low_id, user_high_id = self._sorted_user_pair(proposer.telegram_user_id, target.telegram_user_id)
        pending_stmt = (
            select(RelationshipProposalModel)
            .where(
                self._relationship_chat_match(RelationshipProposalModel.chat_id, chat.telegram_chat_id),
                RelationshipProposalModel.user_low_id == user_low_id,
                RelationshipProposalModel.user_high_id == user_high_id,
                RelationshipProposalModel.kind == kind,
                RelationshipProposalModel.status == "pending",
            )
            .order_by(RelationshipProposalModel.created_at.desc())
            .limit(1)
        )
        pending_row = (await self._session.execute(pending_stmt)).scalar_one_or_none()
        if pending_row is not None:
            if pending_row.expires_at is not None and pending_row.expires_at <= event_at:
                pending_row.status = "expired"
                pending_row.responded_at = event_at
            else:
                return self._to_relationship_proposal(pending_row), "Для этой пары уже есть активное предложение."

        row = RelationshipProposalModel(
            proposer_user_id=proposer.telegram_user_id,
            target_user_id=target.telegram_user_id,
            user_low_id=user_low_id,
            user_high_id=user_high_id,
            chat_id=chat.telegram_chat_id,
            kind=kind,
            status="pending",
            created_at=event_at,
            expires_at=expires_at,
            responded_at=None,
        )
        self._session.add(row)
        await self._session.flush()
        return self._to_relationship_proposal(row), None

    async def respond_relationship_proposal(
        self,
        *,
        proposal_id: int,
        actor_user_id: int,
        accept: bool,
        event_at: datetime,
    ) -> tuple[RelationshipProposal | None, RelationshipState | None, str | None]:
        row = await self._session.get(RelationshipProposalModel, proposal_id)
        if row is None:
            return None, None, "Предложение не найдено."
        if row.status != "pending":
            return self._to_relationship_proposal(row), None, "Это предложение уже обработано."
        if row.expires_at is not None and row.expires_at <= event_at:
            row.status = "expired"
            row.responded_at = event_at
            await self._session.flush()
            return self._to_relationship_proposal(row), None, "Срок предложения истёк."

        if actor_user_id not in {int(row.proposer_user_id), int(row.target_user_id)}:
            return self._to_relationship_proposal(row), None, "Вы не участник этого предложения."

        if actor_user_id == int(row.proposer_user_id):
            if accept:
                return self._to_relationship_proposal(row), None, "Принять предложение может только тот, кому его отправили."
            row.status = "cancelled"
            row.responded_at = event_at
            await self._session.flush()
            return self._to_relationship_proposal(row), None, None

        if not accept:
            row.status = "rejected"
            row.responded_at = event_at
            await self._session.flush()
            return self._to_relationship_proposal(row), None, None

        kind = str(row.kind).strip().lower()
        if kind == "pair":
            state, error = await self._accept_pair_proposal(row=row, event_at=event_at)
        else:
            state, error = await self._accept_marriage_proposal(row=row, event_at=event_at)
        if error is not None:
            row.status = "cancelled"
            row.responded_at = event_at
            await self._session.flush()
            return self._to_relationship_proposal(row), None, error

        row.status = "accepted"
        row.responded_at = event_at
        await self._session.flush()
        return self._to_relationship_proposal(row), state, None

    async def respond_marriage_proposal(
        self,
        *,
        proposal_id: int,
        actor_user_id: int,
        accept: bool,
        event_at: datetime,
    ) -> tuple[RelationshipProposal | None, MarriageState | None, str | None]:
        proposal, relationship, error = await self.respond_relationship_proposal(
            proposal_id=proposal_id,
            actor_user_id=actor_user_id,
            accept=accept,
            event_at=event_at,
        )
        if relationship is None or relationship.kind != "marriage":
            return proposal, None, error
        return proposal, self._marriage_state_from_relationship(relationship), error

    async def touch_pair_affection(
        self,
        *,
        pair_id: int,
        actor_user_id: int,
        affection_delta: int,
        event_at: datetime,
    ) -> PairState | None:
        row = await self._session.get(PairModel, pair_id)
        if row is None:
            return None

        row.affection_points = max(0, int(row.affection_points) + max(0, int(affection_delta)))
        row.last_affection_by_user_id = actor_user_id
        row.last_affection_at = event_at
        row.updated_at = event_at
        await self._session.flush()
        return self._to_pair_state(row)

    async def touch_marriage_affection(
        self,
        *,
        marriage_id: int,
        actor_user_id: int,
        affection_delta: int,
        event_at: datetime,
    ) -> MarriageState | None:
        row = await self._session.get(MarriageModel, marriage_id)
        if row is None:
            return None

        row.affection_points = max(0, int(row.affection_points) + max(0, int(affection_delta)))
        row.last_affection_by_user_id = actor_user_id
        row.last_affection_at = event_at
        row.updated_at = event_at
        await self._session.flush()
        return self._to_marriage_state(row)

    async def touch_relationship_affection(
        self,
        *,
        relationship: RelationshipState,
        actor_user_id: int,
        affection_delta: int,
        event_at: datetime,
    ) -> RelationshipState | None:
        if relationship.kind == "pair":
            pair = await self.touch_pair_affection(
                pair_id=relationship.id,
                actor_user_id=actor_user_id,
                affection_delta=affection_delta,
                event_at=event_at,
            )
            return self._relationship_state_from_pair(pair) if pair is not None else None
        marriage = await self.touch_marriage_affection(
            marriage_id=relationship.id,
            actor_user_id=actor_user_id,
            affection_delta=affection_delta,
            event_at=event_at,
        )
        return self._relationship_state_from_marriage(marriage) if marriage is not None else None

    async def get_relationship_action_last_used_at(
        self,
        *,
        relationship: RelationshipState,
        actor_user_id: int,
        action_code: RelationshipActionCode,
    ) -> datetime | None:
        row = await self._session.get(
            RelationshipActionUsageModel,
            {
                "relationship_kind": relationship.kind,
                "relationship_id": relationship.id,
                "actor_user_id": actor_user_id,
                "action_code": action_code,
            },
        )
        if row is None:
            return None
        return row.last_used_at

    async def set_relationship_action_last_used_at(
        self,
        *,
        relationship: RelationshipState,
        actor_user_id: int,
        action_code: RelationshipActionCode,
        used_at: datetime,
    ) -> datetime:
        row = await self._session.get(
            RelationshipActionUsageModel,
            {
                "relationship_kind": relationship.kind,
                "relationship_id": relationship.id,
                "actor_user_id": actor_user_id,
                "action_code": action_code,
            },
        )
        if row is None:
            row = RelationshipActionUsageModel(
                relationship_kind=relationship.kind,
                relationship_id=relationship.id,
                actor_user_id=actor_user_id,
                action_code=action_code,
                last_used_at=used_at,
            )
            self._session.add(row)
        else:
            row.last_used_at = used_at
            row.updated_at = used_at
        await self._session.flush()
        return row.last_used_at

    async def dissolve_pair(self, *, user_id: int, chat_id: int | None = None) -> PairState | None:
        stmt = select(PairModel).where(or_(PairModel.user_low_id == user_id, PairModel.user_high_id == user_id))
        if chat_id is not None:
            stmt = stmt.where(PairModel.chat_id == chat_id)
        stmt = stmt.limit(1)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None

        pair_state = self._to_pair_state(row)
        await self._session.execute(
            RelationshipActionUsageModel.__table__.delete().where(
                RelationshipActionUsageModel.relationship_kind == "pair",
                RelationshipActionUsageModel.relationship_id == row.id,
            )
        )
        await self._session.delete(row)
        await self._session.flush()
        return pair_state

    async def dissolve_marriage(self, *, user_id: int, chat_id: int | None = None) -> MarriageState | None:
        stmt = select(MarriageModel).where(
            or_(MarriageModel.user_low_id == user_id, MarriageModel.user_high_id == user_id),
            MarriageModel.is_active.is_(True),
        )
        if chat_id is not None:
            stmt = stmt.where(MarriageModel.chat_id == chat_id)
        stmt = stmt.limit(1)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None

        await self._session.execute(
            RelationshipActionUsageModel.__table__.delete().where(
                RelationshipActionUsageModel.relationship_kind == "marriage",
                RelationshipActionUsageModel.relationship_id == row.id,
            )
        )
        ended_at = datetime.now(timezone.utc)
        row.is_active = False
        row.ended_at = ended_at
        row.ended_by_user_id = user_id
        row.ended_reason = "initiated_by_user"
        row.updated_at = ended_at
        await self._session.flush()
        return self._to_marriage_state(row)

    async def ensure_chat_role_templates(self, *, chat: ChatSnapshot | None = None, chat_id: int | None = None) -> None:
        if chat is not None:
            await self._upsert_chat(chat)
            effective_chat_id = int(chat.telegram_chat_id)
        elif chat_id is not None:
            effective_chat_id = int(chat_id)
        else:
            raise ValueError("chat or chat_id is required")

        stmt = select(ChatRoleDefinitionModel.role_code).where(ChatRoleDefinitionModel.chat_id == effective_chat_id)
        existing_raw = (await self._session.execute(stmt)).scalars().all()
        existing_codes: set[str] = set()
        for code in existing_raw:
            normalized = normalize_assigned_role_code(str(code)) or normalize_role_code(str(code))
            if normalized:
                existing_codes.add(normalized)

        added = False
        for template in SYSTEM_ROLE_TEMPLATES:
            if template.role_code in existing_codes:
                continue
            self._session.add(
                ChatRoleDefinitionModel(
                    chat_id=effective_chat_id,
                    role_code=template.role_code,
                    title_ru=template.title_ru,
                    rank=template.rank,
                    permissions=list(template.permissions),
                    is_system=template.is_system,
                    template_key=template.template_key,
                )
            )
            added = True
        if added:
            await self._session.flush()

    async def list_chat_role_definitions(self, *, chat_id: int) -> list[ChatRoleDefinition]:
        await self.ensure_chat_role_templates(chat_id=chat_id)
        stmt = select(ChatRoleDefinitionModel).where(ChatRoleDefinitionModel.chat_id == chat_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        items = [self._to_chat_role_definition(row) for row in rows]
        items.sort(key=lambda item: (-item.rank, item.role_code))
        return items

    async def get_chat_role_definition(self, *, chat_id: int, role_code: str) -> ChatRoleDefinition | None:
        await self.ensure_chat_role_templates(chat_id=chat_id)
        normalized = normalize_assigned_role_code(role_code) or normalize_role_code(role_code)
        if not normalized:
            return None
        row = await self._session.get(ChatRoleDefinitionModel, {"chat_id": chat_id, "role_code": normalized})
        if row is None:
            return None
        return self._to_chat_role_definition(row)

    async def resolve_chat_role_definition(self, *, chat_id: int, token: str) -> ChatRoleDefinition | None:
        normalized_title = normalize_role_title(token)
        if not normalized_title:
            return None

        template_key = resolve_role_template_key(normalized_title)
        if template_key is not None:
            template = SYSTEM_ROLE_BY_TEMPLATE_KEY.get(template_key)
            if template is not None:
                system_role = await self.get_chat_role_definition(chat_id=chat_id, role_code=template.role_code)
                if system_role is not None:
                    return system_role

        by_code = await self.get_chat_role_definition(chat_id=chat_id, role_code=normalized_title)
        if by_code is not None:
            return by_code

        lowered = normalized_title.lower()
        roles = await self.list_chat_role_definitions(chat_id=chat_id)
        for role in roles:
            if normalize_role_title(role.title_ru).lower() == lowered:
                return role

        if normalized_title.lstrip("-").isdigit():
            target_rank = int(normalized_title)
            ranked_matches = [role for role in roles if int(role.rank) == target_rank]
            if len(ranked_matches) == 1:
                return ranked_matches[0]
        return None

    async def get_effective_role_definition(self, *, chat_id: int, user_id: int) -> ChatRoleDefinition:
        await self.ensure_chat_role_templates(chat_id=chat_id)
        assigned_role = await self.get_bot_role(chat_id=chat_id, user_id=user_id)
        if assigned_role is not None:
            definition = await self.get_chat_role_definition(chat_id=chat_id, role_code=assigned_role)
            if definition is not None:
                return definition

        participant = await self.get_chat_role_definition(chat_id=chat_id, role_code="participant")
        if participant is not None:
            return participant

        template = SYSTEM_ROLE_BY_TEMPLATE_KEY["participant"]
        return ChatRoleDefinition(
            chat_id=chat_id,
            role_code=template.role_code,
            title_ru=template.title_ru,
            rank=template.rank,
            permissions=tuple(sorted(template.permissions)),
            is_system=True,
            template_key=template.template_key,
            updated_at=None,
        )

    async def create_custom_role_from_template(
        self,
        *,
        chat: ChatSnapshot,
        title_ru: str,
        template_token: str,
        rank: int | None = None,
        role_code: str | None = None,
    ) -> ChatRoleDefinition:
        await self._upsert_chat(chat)
        await self.ensure_chat_role_templates(chat_id=chat.telegram_chat_id)

        normalized_title = normalize_role_title(title_ru)
        if not normalized_title:
            raise ValueError("Название роли не должно быть пустым.")
        if len(normalized_title) > 128:
            raise ValueError("Название роли слишком длинное (максимум 128 символов).")

        template_role = await self.resolve_chat_role_definition(chat_id=chat.telegram_chat_id, token=template_token)
        if template_role is None:
            raise ValueError("Неизвестный шаблон роли.")

        requested_code = normalize_role_code(role_code or normalized_title)
        if not requested_code:
            raise ValueError("Не удалось сформировать код роли.")
        if requested_code in {template.role_code for template in SYSTEM_ROLE_TEMPLATES}:
            raise ValueError("Код роли занят системной ролью.")

        final_code = requested_code
        suffix = 2
        while await self._session.get(
            ChatRoleDefinitionModel,
            {"chat_id": chat.telegram_chat_id, "role_code": final_code},
        ) is not None:
            base = requested_code[:56] if len(requested_code) > 56 else requested_code
            final_code = f"{base}_{suffix}"
            suffix += 1

        role_rank = int(rank) if rank is not None else int(template_role.rank)
        row = ChatRoleDefinitionModel(
            chat_id=chat.telegram_chat_id,
            role_code=final_code,
            title_ru=normalized_title,
            rank=role_rank,
            permissions=list(template_role.permissions),
            is_system=False,
            template_key=template_role.template_key,
        )
        self._session.add(row)
        await self._session.flush()
        return self._to_chat_role_definition(row)

    async def update_custom_role(
        self,
        *,
        chat_id: int,
        role_token: str,
        title_ru: str | None = None,
        rank: int | None = None,
        permissions: Sequence[str] | None = None,
    ) -> ChatRoleDefinition:
        role = await self.resolve_chat_role_definition(chat_id=chat_id, token=role_token)
        if role is None:
            raise ValueError("Роль не найдена.")
        if role.is_system:
            raise ValueError("Системную роль нельзя редактировать.")

        row = await self._session.get(
            ChatRoleDefinitionModel,
            {"chat_id": chat_id, "role_code": role.role_code},
        )
        if row is None:
            raise ValueError("Роль не найдена.")

        if title_ru is not None:
            normalized_title = normalize_role_title(title_ru)
            if not normalized_title:
                raise ValueError("Название роли не должно быть пустым.")
            if len(normalized_title) > 128:
                raise ValueError("Название роли слишком длинное (максимум 128 символов).")
            row.title_ru = normalized_title

        if rank is not None:
            row.rank = int(rank)

        if permissions is not None:
            normalized_permissions = sorted({str(item).strip().lower() for item in permissions if str(item).strip()})
            unknown = [perm for perm in normalized_permissions if perm not in BOT_PERMISSIONS]
            if unknown:
                raise ValueError(f"Неизвестные права: {', '.join(unknown)}")
            row.permissions = normalized_permissions

        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return self._to_chat_role_definition(row)

    async def delete_custom_role(self, *, chat_id: int, role_token: str) -> bool:
        role = await self.resolve_chat_role_definition(chat_id=chat_id, token=role_token)
        if role is None:
            return False
        if role.is_system:
            raise ValueError("Системную роль нельзя удалить.")

        row = await self._session.get(
            ChatRoleDefinitionModel,
            {"chat_id": chat_id, "role_code": role.role_code},
        )
        if row is None:
            return False

        await self._session.execute(
            UserChatBotRoleModel.__table__.delete().where(
                UserChatBotRoleModel.chat_id == chat_id,
                UserChatBotRoleModel.role == role.role_code,
            )
        )
        await self._session.execute(
            ChatCommandAccessRuleModel.__table__.update()
            .where(
                ChatCommandAccessRuleModel.chat_id == chat_id,
                ChatCommandAccessRuleModel.min_role_code == role.role_code,
            )
            .values(min_role_code="participant", updated_at=datetime.now(timezone.utc))
        )
        await self._session.delete(row)
        await self._session.flush()
        return True

    @staticmethod
    def _normalize_command_key(command_key: str) -> str:
        value = normalize_role_code(command_key.replace("-", "_"))
        return value[:64]

    async def get_command_access_rule(self, *, chat_id: int, command_key: str) -> ChatCommandAccessRule | None:
        normalized_key = self._normalize_command_key(command_key)
        if not normalized_key:
            return None
        row = await self._session.get(
            ChatCommandAccessRuleModel,
            {"chat_id": chat_id, "command_key": normalized_key},
        )
        if row is None:
            return None
        return self._to_chat_command_access_rule(row)

    async def list_command_access_rules(self, *, chat_id: int) -> list[ChatCommandAccessRule]:
        stmt = (
            select(ChatCommandAccessRuleModel)
            .where(ChatCommandAccessRuleModel.chat_id == chat_id)
            .order_by(ChatCommandAccessRuleModel.command_key.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_chat_command_access_rule(row) for row in rows]

    async def upsert_command_access_rule(
        self,
        *,
        chat: ChatSnapshot,
        command_key: str,
        min_role_token: str,
        updated_by_user_id: int | None,
    ) -> ChatCommandAccessRule:
        await self._upsert_chat(chat)
        await self.ensure_chat_role_templates(chat_id=chat.telegram_chat_id)
        normalized_key = self._normalize_command_key(command_key)
        if not normalized_key:
            raise ValueError("Некорректное имя команды.")

        role = await self.resolve_chat_role_definition(chat_id=chat.telegram_chat_id, token=min_role_token)
        if role is None:
            raise ValueError("Неизвестная роль.")

        row = await self._session.get(
            ChatCommandAccessRuleModel,
            {"chat_id": chat.telegram_chat_id, "command_key": normalized_key},
        )
        if row is None:
            row = ChatCommandAccessRuleModel(
                chat_id=chat.telegram_chat_id,
                command_key=normalized_key,
                min_role_code=role.role_code,
                updated_by_user_id=updated_by_user_id,
            )
            self._session.add(row)
        else:
            row.min_role_code = role.role_code
            row.updated_by_user_id = updated_by_user_id
            row.updated_at = datetime.now(timezone.utc)

        await self._session.flush()
        return self._to_chat_command_access_rule(row)

    async def remove_command_access_rule(self, *, chat_id: int, command_key: str) -> bool:
        normalized_key = self._normalize_command_key(command_key)
        if not normalized_key:
            return False
        row = await self._session.get(
            ChatCommandAccessRuleModel,
            {"chat_id": chat_id, "command_key": normalized_key},
        )
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def bootstrap_chat_owner_role(self, *, chat: ChatSnapshot, user: UserSnapshot) -> tuple[BotRole | None, bool]:
        await self._upsert_chat(chat)
        await self._upsert_user(user)
        await self.ensure_chat_role_templates(chat=chat)

        owner_rows = (
            await self._session.execute(
                select(UserChatBotRoleModel.user_id, UserChatBotRoleModel.role).where(
                    UserChatBotRoleModel.chat_id == chat.telegram_chat_id
                )
            )
        ).all()
        owner_exists = any(
            (normalize_assigned_role_code(raw_role) or normalize_role_code(raw_role)) == "owner"
            for _user_id, raw_role in owner_rows
        )

        actor_row = await self._session.get(
            UserChatBotRoleModel,
            {"chat_id": chat.telegram_chat_id, "user_id": user.telegram_user_id},
        )

        if not owner_exists:
            if actor_row is None:
                self._session.add(
                    UserChatBotRoleModel(
                        chat_id=chat.telegram_chat_id,
                        user_id=user.telegram_user_id,
                        role="owner",
                        assigned_by_user_id=user.telegram_user_id,
                    )
                )
            else:
                actor_row.role = "owner"
                actor_row.assigned_by_user_id = user.telegram_user_id
                actor_row.updated_at = datetime.now(timezone.utc)
            await self._session.flush()
            return "owner", True

        if actor_row is None:
            return None, False

        normalized_actor_role = normalize_assigned_role_code(actor_row.role) or normalize_role_code(actor_row.role)
        if not normalized_actor_role:
            return None, False
        if normalized_actor_role != actor_row.role:
            actor_row.role = normalized_actor_role
            actor_row.updated_at = datetime.now(timezone.utc)
            await self._session.flush()
        return normalized_actor_role, False

    async def get_bot_role(self, *, chat_id: int, user_id: int) -> BotRole | None:
        row = await self._session.get(UserChatBotRoleModel, {"chat_id": chat_id, "user_id": user_id})
        if row is None:
            return None
        normalized_role = normalize_assigned_role_code(row.role) or normalize_role_code(row.role)
        if not normalized_role:
            return None
        if normalized_role != row.role:
            row.role = normalized_role
            row.updated_at = datetime.now(timezone.utc)
            await self._session.flush()
        return normalized_role

    async def set_bot_role(
        self,
        *,
        chat: ChatSnapshot,
        target: UserSnapshot,
        role: BotRole,
        assigned_by_user_id: int | None,
    ) -> None:
        await self._upsert_chat(chat)
        await self._upsert_user(target)
        await self.ensure_chat_role_templates(chat=chat)

        role_definition = await self.resolve_chat_role_definition(chat_id=chat.telegram_chat_id, token=role)
        if role_definition is None:
            raise ValueError("Неизвестная роль.")

        row = await self._session.get(
            UserChatBotRoleModel,
            {"chat_id": chat.telegram_chat_id, "user_id": target.telegram_user_id},
        )
        if row is None:
            self._session.add(
                UserChatBotRoleModel(
                    chat_id=chat.telegram_chat_id,
                    user_id=target.telegram_user_id,
                    role=role_definition.role_code,
                    assigned_by_user_id=assigned_by_user_id,
                )
            )
        else:
            row.role = role_definition.role_code
            row.assigned_by_user_id = assigned_by_user_id
            row.updated_at = datetime.now(timezone.utc)

        await self._session.flush()

    async def remove_bot_role(self, *, chat_id: int, user_id: int) -> bool:
        row = await self._session.get(UserChatBotRoleModel, {"chat_id": chat_id, "user_id": user_id})
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def list_bot_roles(self, *, chat_id: int) -> list[tuple[UserSnapshot, BotRole]]:
        role_definitions = await self.list_chat_role_definitions(chat_id=chat_id)
        role_ranks = {item.role_code: item.rank for item in role_definitions}
        stmt = (
            select(UserChatBotRoleModel, UserModel)
            .join(UserModel, UserModel.telegram_user_id == UserChatBotRoleModel.user_id)
            .where(UserChatBotRoleModel.chat_id == chat_id)
        )
        rows = (await self._session.execute(stmt)).all()
        display_overrides = await self._get_chat_display_overrides(
            chat_id=chat_id,
            user_ids=tuple(int(role_row.user_id) for role_row, _ in rows),
        )

        items: list[tuple[UserSnapshot, BotRole]] = []
        for role_row, user_row in rows:
            normalized_role = normalize_assigned_role_code(role_row.role) or normalize_role_code(role_row.role)
            if not normalized_role:
                continue
            items.append(
                (
                    self._to_user_snapshot(
                        user_row,
                        chat_display_name=display_overrides.get(int(role_row.user_id)),
                    ),
                    normalized_role,
                )
            )

        items.sort(key=lambda item: (-role_ranks.get(item[1], -10_000), item[0].telegram_user_id))
        return items

    async def _list_user_role_based_chats(
        self,
        *,
        user_id: int,
        required_permissions: set[str],
    ) -> list[UserChatOverview]:
        stmt = (
            select(
                ChatModel.telegram_chat_id,
                ChatModel.type,
                ChatModel.title,
                UserChatBotRoleModel.role,
                UserChatActivityModel.message_count,
                UserChatActivityModel.last_seen_at,
            )
            .join(ChatModel, ChatModel.telegram_chat_id == UserChatBotRoleModel.chat_id)
            .outerjoin(
                UserChatActivityModel,
                and_(
                    UserChatActivityModel.chat_id == UserChatBotRoleModel.chat_id,
                    UserChatActivityModel.user_id == user_id,
                ),
            )
            .where(
                UserChatBotRoleModel.user_id == user_id,
                ChatModel.type.in_(["group", "supergroup"]),
            )
        )
        rows = (await self._session.execute(stmt)).all()
        chat_ids = sorted({int(chat_id) for chat_id, *_ in rows})

        role_map: dict[tuple[int, str], ChatRoleDefinition] = {}
        for chat_id in chat_ids:
            for role in await self.list_chat_role_definitions(chat_id=chat_id):
                role_map[(chat_id, role.role_code)] = role

        items: list[tuple[int, UserChatOverview]] = []
        for chat_id, chat_type, chat_title, role_code_raw, message_count, last_seen_at in rows:
            normalized_role_code = normalize_assigned_role_code(role_code_raw) or normalize_role_code(role_code_raw)
            if not normalized_role_code:
                continue
            role_def = role_map.get((int(chat_id), normalized_role_code))
            if role_def is None:
                continue
            permissions = set(role_def.permissions)
            if not required_permissions.intersection(permissions):
                continue
            items.append(
                (
                    int(role_def.rank),
                    UserChatOverview(
                        chat_id=int(chat_id),
                        chat_type=str(chat_type),
                        chat_title=chat_title,
                        bot_role=normalized_role_code,
                        message_count=int(message_count) if message_count is not None else None,
                        last_seen_at=last_seen_at,
                    ),
                )
            )

        items.sort(
            key=lambda item: (
                -item[0],
                (item[1].chat_title or "").lower(),
                item[1].chat_id,
            )
        )
        return [item[1] for item in items]

    async def list_user_admin_chats(self, *, user_id: int) -> list[UserChatOverview]:
        return await self._list_user_role_based_chats(
            user_id=user_id,
            required_permissions={"manage_settings", "manage_command_access", "manage_roles", "manage_role_templates"},
        )

    async def list_user_manageable_game_chats(self, *, user_id: int) -> list[UserChatOverview]:
        return await self._list_user_role_based_chats(
            user_id=user_id,
            required_permissions={"manage_games"},
        )

    async def list_user_activity_chats(self, *, user_id: int, limit: int = 50) -> list[UserChatOverview]:
        normalized_limit = max(1, min(int(limit), 200))
        stmt = (
            select(
                UserChatActivityModel.chat_id,
                ChatModel.type,
                ChatModel.title,
                UserChatActivityModel.message_count,
                UserChatActivityModel.last_seen_at,
                UserChatBotRoleModel.role,
            )
            .join(ChatModel, ChatModel.telegram_chat_id == UserChatActivityModel.chat_id)
            .outerjoin(
                UserChatBotRoleModel,
                and_(
                    UserChatBotRoleModel.chat_id == UserChatActivityModel.chat_id,
                    UserChatBotRoleModel.user_id == user_id,
                ),
            )
            .where(
                UserChatActivityModel.user_id == user_id,
                ChatModel.type.in_(["group", "supergroup"]),
            )
            .order_by(
                UserChatActivityModel.last_seen_at.desc(),
                UserChatActivityModel.chat_id.desc(),
            )
            .limit(normalized_limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            UserChatOverview(
                chat_id=int(chat_id),
                chat_type=str(chat_type),
                chat_title=chat_title,
                bot_role=(normalize_assigned_role_code(role) or normalize_role_code(role)) if role is not None else None,
                message_count=int(message_count),
                last_seen_at=last_seen_at,
            )
            for chat_id, chat_type, chat_title, message_count, last_seen_at, role in rows
        ]

    async def find_chat_user_by_username(self, *, chat_id: int, username: str) -> UserSnapshot | None:
        lowered = username.lstrip("@").strip().lower()
        if not lowered:
            return None

        stmt = (
            select(UserModel, UserChatActivityModel.display_name_override, UserChatActivityModel.title_prefix)
            .join(UserChatActivityModel, UserChatActivityModel.user_id == UserModel.telegram_user_id)
            .where(
                UserChatActivityModel.chat_id == chat_id,
                func.lower(UserModel.username) == lowered,
            )
            .order_by(UserChatActivityModel.last_seen_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None
        user, display_name_override, title_prefix = row
        return self._to_user_snapshot(user, chat_display_name=display_name_override, title_prefix=title_prefix)

    async def find_shared_group_user_by_username(self, *, sender_user_id: int, username: str) -> UserSnapshot | None:
        lowered = username.lstrip("@").strip().lower()
        if not lowered:
            return None

        sender_activity = aliased(UserChatActivityModel)
        target_activity = aliased(UserChatActivityModel)
        stmt = (
            select(UserModel, target_activity.display_name_override, target_activity.title_prefix)
            .join(target_activity, target_activity.user_id == UserModel.telegram_user_id)
            .join(
                sender_activity,
                and_(
                    sender_activity.chat_id == target_activity.chat_id,
                    sender_activity.user_id == sender_user_id,
                ),
            )
            .join(ChatModel, ChatModel.telegram_chat_id == target_activity.chat_id)
            .where(
                ChatModel.type.in_(["group", "supergroup"]),
                func.lower(UserModel.username) == lowered,
                UserModel.telegram_user_id != sender_user_id,
                UserModel.is_bot.is_(False),
            )
            .order_by(target_activity.last_seen_at.desc(), target_activity.chat_id.asc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None
        user, display_name_override, title_prefix = row
        return self._to_user_snapshot(user, chat_display_name=display_name_override, title_prefix=title_prefix)

    async def create_inline_private_message(
        self,
        *,
        id: str,
        chat_id: int | None,
        chat_instance: str | None,
        sender_id: int,
        receiver_ids: list[int],
        receiver_usernames: list[str],
        text: str,
        created_at: datetime,
    ) -> InlinePrivateMessage:
        sender = UserSnapshot(
            telegram_user_id=int(sender_id),
            username=None,
            first_name=None,
            last_name=None,
            is_bot=False,
        )
        await self._upsert_user(sender)

        normalized_receivers: list[int] = []
        seen_receivers: set[int] = set()
        for raw_receiver_id in receiver_ids:
            receiver_id = int(raw_receiver_id)
            if receiver_id in seen_receivers:
                continue
            seen_receivers.add(receiver_id)
            normalized_receivers.append(receiver_id)
            await self._upsert_user(
                UserSnapshot(
                    telegram_user_id=receiver_id,
                    username=None,
                    first_name=None,
                    last_name=None,
                    is_bot=False,
                )
            )

        normalized_chat_instance = (chat_instance or "").strip() or None
        normalized_receiver_usernames: list[str] = []
        seen_receiver_usernames: set[str] = set()
        for raw_username in receiver_usernames:
            normalized_username = str(raw_username).lstrip("@").strip().lower()
            if not normalized_username or normalized_username in seen_receiver_usernames:
                continue
            seen_receiver_usernames.add(normalized_username)
            normalized_receiver_usernames.append(normalized_username)

        row = InlinePrivateMessageModel(
            id=str(id).strip(),
            chat_id=int(chat_id) if chat_id is not None else None,
            chat_instance=normalized_chat_instance,
            sender_id=int(sender_id),
            receiver_ids=normalized_receivers,
            receiver_usernames=normalized_receiver_usernames,
            text=text,
            created_at=created_at,
        )
        self._session.add(row)
        await self._session.flush()
        return self._to_inline_private_message(row)

    async def get_inline_private_message(self, *, id: str) -> InlinePrivateMessage | None:
        row = await self._session.get(InlinePrivateMessageModel, str(id).strip())
        if row is None:
            return None
        return self._to_inline_private_message(row)

    async def set_inline_private_message_context(
        self,
        *,
        id: str,
        chat_id: int | None,
        chat_instance: str | None,
    ) -> bool:
        row = await self._session.get(InlinePrivateMessageModel, str(id).strip())
        if row is None:
            return False

        normalized_chat_instance = (chat_instance or "").strip() or None
        changed = False

        if chat_id is not None:
            candidate_chat_id = int(chat_id)
            exists_stmt = select(ChatModel.telegram_chat_id).where(ChatModel.telegram_chat_id == candidate_chat_id).limit(1)
            chat_exists = (await self._session.execute(exists_stmt)).scalar_one_or_none() is not None
            if chat_exists and row.chat_id != candidate_chat_id:
                row.chat_id = candidate_chat_id
                changed = True

        if normalized_chat_instance is not None and row.chat_instance != normalized_chat_instance:
            row.chat_instance = normalized_chat_instance
            changed = True

        if changed:
            await self._session.flush()
        return changed

    async def list_recent_inline_private_receivers(self, *, sender_user_id: int, limit: int = 10) -> list[UserSnapshot]:
        normalized_limit = max(1, min(int(limit), 50))
        scan_limit = max(normalized_limit * 20, 100)

        stmt = (
            select(InlinePrivateMessageModel.receiver_ids)
            .where(InlinePrivateMessageModel.sender_id == int(sender_user_id))
            .order_by(InlinePrivateMessageModel.created_at.desc())
            .limit(scan_limit)
        )
        rows = (await self._session.execute(stmt)).all()

        ordered_receiver_ids: list[int] = []
        seen_receiver_ids: set[int] = set()
        for (receiver_ids_raw,) in rows:
            values = receiver_ids_raw if isinstance(receiver_ids_raw, list) else []
            for value in values:
                try:
                    receiver_id = int(value)
                except (TypeError, ValueError):
                    continue

                if receiver_id == int(sender_user_id) or receiver_id in seen_receiver_ids:
                    continue
                seen_receiver_ids.add(receiver_id)
                ordered_receiver_ids.append(receiver_id)
                if len(ordered_receiver_ids) >= normalized_limit:
                    break
            if len(ordered_receiver_ids) >= normalized_limit:
                break

        if not ordered_receiver_ids:
            return []

        users_by_id = await self._get_users_by_ids(ordered_receiver_ids)
        display_overrides = await self._get_latest_group_display_overrides(user_ids=ordered_receiver_ids)

        items: list[UserSnapshot] = []
        for receiver_id in ordered_receiver_ids:
            user = users_by_id.get(receiver_id)
            if user is None:
                continue
            items.append(self._to_user_snapshot(user, chat_display_name=display_overrides.get(receiver_id)))
        return items

    async def list_recent_inline_private_receiver_usernames(self, *, sender_user_id: int, limit: int = 10) -> list[str]:
        normalized_limit = max(1, min(int(limit), 50))
        scan_limit = max(normalized_limit * 20, 100)

        stmt = (
            select(InlinePrivateMessageModel.receiver_usernames)
            .where(InlinePrivateMessageModel.sender_id == int(sender_user_id))
            .order_by(InlinePrivateMessageModel.created_at.desc())
            .limit(scan_limit)
        )
        rows = (await self._session.execute(stmt)).all()

        values: list[str] = []
        seen: set[str] = set()
        for (usernames_raw,) in rows:
            usernames = usernames_raw if isinstance(usernames_raw, list) else []
            for raw in usernames:
                username = str(raw).lstrip("@").strip().lower()
                if not username or username in seen:
                    continue
                seen.add(username)
                values.append(username)
                if len(values) >= normalized_limit:
                    break
            if len(values) >= normalized_limit:
                break
        return values

    async def get_user_snapshot(self, *, user_id: int) -> UserSnapshot | None:
        row = await self._session.get(UserModel, user_id)
        if row is None:
            return None
        return self._to_user_snapshot(row)

    async def apply_moderation_action(
        self,
        *,
        chat: ChatSnapshot,
        actor: UserSnapshot,
        target: UserSnapshot,
        action: ModerationAction,
        reason: str | None = None,
        amount: int = 1,
    ) -> ModerationResult:
        await self._upsert_chat(chat)
        await self._upsert_user(actor)
        await self._upsert_user(target)

        row = await self._session.get(
            UserChatModerationStateModel,
            {"chat_id": chat.telegram_chat_id, "user_id": target.telegram_user_id},
        )
        if row is None:
            row = UserChatModerationStateModel(
                chat_id=chat.telegram_chat_id,
                user_id=target.telegram_user_id,
            )
            self._session.add(row)
            await self._session.flush()

        normalized_amount = max(1, int(amount))
        auto_warns_added = 0
        auto_ban_triggered = False

        if action == "pred":
            row.pending_preds += normalized_amount
            row.total_preds += normalized_amount

            auto_warns_added = int(row.pending_preds // 3)
            if auto_warns_added > 0:
                row.pending_preds = int(row.pending_preds % 3)
                row.warn_count += auto_warns_added
                row.total_warns += auto_warns_added

        elif action == "warn":
            row.warn_count += normalized_amount
            row.total_warns += normalized_amount

        elif action == "unwarn":
            row.warn_count = max(0, int(row.warn_count) - normalized_amount)

        elif action == "ban":
            if not row.is_banned:
                row.total_bans += 1
            row.is_banned = True
            row.pending_preds = 0
            row.warn_count = 0

        elif action == "unban":
            row.is_banned = False

        if action in {"pred", "warn"} and row.warn_count >= 3:
            row.pending_preds = 0
            row.warn_count = 0
            if not row.is_banned:
                row.total_bans += 1
            row.is_banned = True
            auto_ban_triggered = True

        normalized_reason = (reason or "").strip()
        if normalized_reason:
            row.last_reason = normalized_reason[:1000]

        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

        return ModerationResult(
            state=self._to_moderation_state(row),
            action=action,
            auto_warns_added=auto_warns_added,
            auto_ban_triggered=auto_ban_triggered,
        )

    async def get_moderation_state(self, *, chat_id: int, user_id: int) -> ModerationState | None:
        row = await self._session.get(
            UserChatModerationStateModel,
            {"chat_id": chat_id, "user_id": user_id},
        )
        if row is None:
            return None
        return self._to_moderation_state(row)

    async def _accept_pair_proposal(
        self,
        *,
        row: RelationshipProposalModel,
        event_at: datetime,
    ) -> tuple[RelationshipState | None, str | None]:
        proposer_marriage = await self.get_active_marriage(user_id=int(row.proposer_user_id), chat_id=int(row.chat_id) if row.chat_id is not None else None)
        target_marriage = await self.get_active_marriage(user_id=int(row.target_user_id), chat_id=int(row.chat_id) if row.chat_id is not None else None)
        if proposer_marriage is not None or target_marriage is not None:
            return None, "Один из участников уже состоит в браке."

        proposer_pair = await self.get_active_pair(user_id=int(row.proposer_user_id), chat_id=int(row.chat_id) if row.chat_id is not None else None)
        if proposer_pair is not None:
            return None, "У отправителя уже есть активные отношения."
        target_pair = await self.get_active_pair(user_id=int(row.target_user_id), chat_id=int(row.chat_id) if row.chat_id is not None else None)
        if target_pair is not None:
            return None, "У получателя уже есть активные отношения."

        pair_stmt = (
            select(PairModel)
            .where(
                self._relationship_chat_match(PairModel.chat_id, int(row.chat_id) if row.chat_id is not None else None),
                PairModel.user_low_id == row.user_low_id,
                PairModel.user_high_id == row.user_high_id,
            )
            .limit(1)
        )
        pair_row = (await self._session.execute(pair_stmt)).scalar_one_or_none()
        if pair_row is None:
            pair_row = PairModel(
                user_low_id=row.user_low_id,
                user_high_id=row.user_high_id,
                chat_id=row.chat_id,
                paired_at=event_at,
                affection_points=0,
            )
            self._session.add(pair_row)
            await self._session.flush()

        return self._relationship_state_from_pair(self._to_pair_state(pair_row)), None

    async def _accept_marriage_proposal(
        self,
        *,
        row: RelationshipProposalModel,
        event_at: datetime,
    ) -> tuple[RelationshipState | None, str | None]:
        proposer_marriage = await self.get_active_marriage(user_id=int(row.proposer_user_id), chat_id=int(row.chat_id) if row.chat_id is not None else None)
        target_marriage = await self.get_active_marriage(user_id=int(row.target_user_id), chat_id=int(row.chat_id) if row.chat_id is not None else None)
        if proposer_marriage is not None or target_marriage is not None:
            return None, "Один из участников уже состоит в браке."

        pair_stmt = (
            select(PairModel)
            .where(
                self._relationship_chat_match(PairModel.chat_id, int(row.chat_id) if row.chat_id is not None else None),
                PairModel.user_low_id == row.user_low_id,
                PairModel.user_high_id == row.user_high_id,
            )
            .limit(1)
        )
        pair_row = (await self._session.execute(pair_stmt)).scalar_one_or_none()

        proposer_pair = await self.get_active_pair(user_id=int(row.proposer_user_id), chat_id=int(row.chat_id) if row.chat_id is not None else None)
        if proposer_pair is not None:
            partner_id = self._partner_id_for_pair(
                proposer_pair.user_low_id,
                proposer_pair.user_high_id,
                int(row.proposer_user_id),
            )
            if partner_id != int(row.target_user_id):
                return None, "Сначала завершите текущие отношения."

        target_pair = await self.get_active_pair(user_id=int(row.target_user_id), chat_id=int(row.chat_id) if row.chat_id is not None else None)
        if target_pair is not None:
            partner_id = self._partner_id_for_pair(
                target_pair.user_low_id,
                target_pair.user_high_id,
                int(row.target_user_id),
            )
            if partner_id != int(row.proposer_user_id):
                return None, "У получателя есть другие отношения."

        inherited_affection = int(pair_row.affection_points) if pair_row is not None else 0
        marriage_stmt = (
            select(MarriageModel)
            .where(
                self._relationship_chat_match(MarriageModel.chat_id, int(row.chat_id) if row.chat_id is not None else None),
                MarriageModel.user_low_id == row.user_low_id,
                MarriageModel.user_high_id == row.user_high_id,
                MarriageModel.is_active.is_(True),
            )
            .limit(1)
        )
        marriage_row = (await self._session.execute(marriage_stmt)).scalar_one_or_none()
        if marriage_row is None:
            marriage_row = MarriageModel(
                user_low_id=row.user_low_id,
                user_high_id=row.user_high_id,
                chat_id=row.chat_id,
                married_at=event_at,
                affection_points=inherited_affection,
            )
            self._session.add(marriage_row)
            await self._session.flush()
        elif inherited_affection > int(marriage_row.affection_points):
            marriage_row.affection_points = inherited_affection
            marriage_row.updated_at = event_at
            await self._session.flush()

        if pair_row is not None:
            await self._session.execute(
                RelationshipActionUsageModel.__table__.delete().where(
                    RelationshipActionUsageModel.relationship_kind == "pair",
                    RelationshipActionUsageModel.relationship_id == pair_row.id,
                )
            )
            await self._session.delete(pair_row)
            await self._session.flush()

        return self._relationship_state_from_marriage(self._to_marriage_state(marriage_row)), None

    async def _upsert_user(self, user: UserSnapshot) -> None:
        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"
        is_minimal_profile = user.username is None and user.first_name is None and user.last_name is None and not user.is_bot

        if dialect == "postgresql":
            insert_stmt = (
                pg_insert(UserModel)
                .values(
                    telegram_user_id=user.telegram_user_id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    is_bot=user.is_bot,
                )
                .on_conflict_do_nothing(index_elements=[UserModel.telegram_user_id])
                .returning(UserModel.telegram_user_id)
            )
            inserted_user_id = (await self._session.execute(insert_stmt)).scalar_one_or_none()
            if inserted_user_id is not None:
                await increment_global_users_base_count(self._session)
                if is_minimal_profile:
                    return

            if not is_minimal_profile:
                stmt = pg_insert(UserModel).values(
                    telegram_user_id=user.telegram_user_id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    is_bot=user.is_bot,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[UserModel.telegram_user_id],
                    set_={
                        "username": stmt.excluded.username,
                        "first_name": stmt.excluded.first_name,
                        "last_name": stmt.excluded.last_name,
                        "is_bot": stmt.excluded.is_bot,
                        "updated_at": func.now(),
                    },
                )
                await self._session.execute(stmt)
            return

        user_row = await self._session.get(UserModel, user.telegram_user_id)
        if user_row is None:
            self._session.add(
                UserModel(
                    telegram_user_id=user.telegram_user_id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    is_bot=user.is_bot,
                )
            )
            await self._session.flush()
            await increment_global_users_base_count(self._session)
            return

        if is_minimal_profile:
            return

        user_row.username = user.username
        user_row.first_name = user.first_name
        user_row.last_name = user.last_name
        user_row.is_bot = user.is_bot

    async def _upsert_chat(self, chat: ChatSnapshot) -> None:
        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"
        is_minimal_chat = chat.title is None

        if dialect == "postgresql":
            stmt = pg_insert(ChatModel).values(
                telegram_chat_id=chat.telegram_chat_id,
                type=chat.chat_type,
                title=chat.title,
            )
            if is_minimal_chat:
                stmt = stmt.on_conflict_do_nothing(index_elements=[ChatModel.telegram_chat_id])
            else:
                stmt = stmt.on_conflict_do_update(
                    index_elements=[ChatModel.telegram_chat_id],
                    set_={
                        "type": stmt.excluded.type,
                        "title": stmt.excluded.title,
                        "updated_at": func.now(),
                    },
                )
            await self._session.execute(stmt)
            return

        chat_row = await self._session.get(ChatModel, chat.telegram_chat_id)
        if chat_row is None:
            self._session.add(
                ChatModel(
                    telegram_chat_id=chat.telegram_chat_id,
                    type=chat.chat_type,
                    title=chat.title,
                )
            )
            return

        if is_minimal_chat:
            return

        chat_row.type = chat.chat_type
        chat_row.title = chat.title

    async def _upsert_activity_daily(self, *, chat_id: int, user_id: int, event_at: datetime) -> None:
        activity_date = event_at.astimezone(timezone.utc).date()
        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"

        if dialect == "postgresql":
            insert_stmt = pg_insert(UserChatActivityDailyModel).values(
                chat_id=chat_id,
                user_id=user_id,
                activity_date=activity_date,
                message_count=1,
                last_seen_at=event_at,
            )
            upsert_stmt = insert_stmt.on_conflict_do_update(
                index_elements=[
                    UserChatActivityDailyModel.chat_id,
                    UserChatActivityDailyModel.user_id,
                    UserChatActivityDailyModel.activity_date,
                ],
                set_={
                    "message_count": UserChatActivityDailyModel.message_count + 1,
                    "last_seen_at": func.greatest(UserChatActivityDailyModel.last_seen_at, insert_stmt.excluded.last_seen_at),
                    "updated_at": func.now(),
                },
            )
            await self._session.execute(upsert_stmt)
            return

        row = await self._session.get(
            UserChatActivityDailyModel,
            {"chat_id": chat_id, "user_id": user_id, "activity_date": activity_date},
        )
        if row is None:
            self._session.add(
                UserChatActivityDailyModel(
                    chat_id=chat_id,
                    user_id=user_id,
                    activity_date=activity_date,
                    message_count=1,
                    last_seen_at=event_at,
                )
            )
            return

        row.message_count += 1
        row.last_seen_at = _latest_datetime(row.last_seen_at, event_at)
        row.updated_at = datetime.now(timezone.utc)

    async def _upsert_activity_minute(self, *, chat_id: int, user_id: int, event_at: datetime) -> None:
        minute_bucket = event_at.astimezone(timezone.utc).replace(second=0, microsecond=0)
        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"

        if dialect == "postgresql":
            insert_stmt = pg_insert(UserChatActivityMinuteModel).values(
                chat_id=chat_id,
                user_id=user_id,
                activity_minute=minute_bucket,
                message_count=1,
                last_seen_at=event_at,
            )
            upsert_stmt = insert_stmt.on_conflict_do_update(
                index_elements=[
                    UserChatActivityMinuteModel.chat_id,
                    UserChatActivityMinuteModel.user_id,
                    UserChatActivityMinuteModel.activity_minute,
                ],
                set_={
                    "message_count": UserChatActivityMinuteModel.message_count + 1,
                    "last_seen_at": func.greatest(UserChatActivityMinuteModel.last_seen_at, insert_stmt.excluded.last_seen_at),
                    "updated_at": func.now(),
                },
            )
            await self._session.execute(upsert_stmt)
            return

        row = await self._session.get(
            UserChatActivityMinuteModel,
            {"chat_id": chat_id, "user_id": user_id, "activity_minute": minute_bucket},
        )
        if row is None:
            self._session.add(
                UserChatActivityMinuteModel(
                    chat_id=chat_id,
                    user_id=user_id,
                    activity_minute=minute_bucket,
                    message_count=1,
                    last_seen_at=event_at,
                )
            )
            return

        row.message_count += 1
        row.last_seen_at = _latest_datetime(row.last_seen_at, event_at)
        row.updated_at = datetime.now(timezone.utc)

    async def _get_activity_aggregate(
        self,
        *,
        chat_id: int,
        period: LeaderboardPeriod,
        since: datetime | None,
    ) -> dict[int, tuple[int, datetime | None]]:
        if period == "all":
            stmt = select(
                UserChatActivityModel.user_id,
                UserChatActivityModel.message_count,
                UserChatActivityModel.last_seen_at,
            ).where(UserChatActivityModel.chat_id == chat_id)
            rows = (await self._session.execute(stmt)).all()
            return {int(user_id): (int(message_count), last_seen_at) for user_id, message_count, last_seen_at in rows}

        if since is None:
            return {}
        if period in {"hour", "day", "week", "month", "7d"}:
            stmt = (
                select(
                    UserChatActivityMinuteModel.user_id,
                    func.coalesce(func.sum(UserChatActivityMinuteModel.message_count), 0),
                    func.max(UserChatActivityMinuteModel.last_seen_at),
                )
                .where(
                    UserChatActivityMinuteModel.chat_id == chat_id,
                    UserChatActivityMinuteModel.activity_minute >= since,
                )
                .group_by(UserChatActivityMinuteModel.user_id)
            )
            rows = (await self._session.execute(stmt)).all()
            return {int(user_id): (int(message_count), last_seen_at) for user_id, message_count, last_seen_at in rows}

        stmt = (
            select(
                UserChatActivityDailyModel.user_id,
                func.coalesce(func.sum(UserChatActivityDailyModel.message_count), 0),
                func.max(UserChatActivityDailyModel.last_seen_at),
            )
            .where(
                UserChatActivityDailyModel.chat_id == chat_id,
                UserChatActivityDailyModel.activity_date >= since.date(),
            )
            .group_by(UserChatActivityDailyModel.user_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {int(user_id): (int(message_count), last_seen_at) for user_id, message_count, last_seen_at in rows}

    async def _get_karma_aggregate(
        self,
        *,
        chat_id: int,
        period: LeaderboardPeriod,
        since: datetime | None,
    ) -> dict[int, int]:
        stmt = select(
            UserKarmaVoteModel.target_user_id,
            func.coalesce(func.sum(UserKarmaVoteModel.vote_value), 0),
        ).where(UserKarmaVoteModel.chat_id == chat_id)

        if period != "all" and since is not None:
            stmt = stmt.where(UserKarmaVoteModel.created_at >= since)

        stmt = stmt.group_by(UserKarmaVoteModel.target_user_id)
        rows = (await self._session.execute(stmt)).all()
        values = {int(user_id): int(karma_value or 0) for user_id, karma_value in rows}
        if period == "all":
            bases = await self._get_iris_karma_bases(chat_id=chat_id, user_ids=None)
            for user_id, karma_base in bases.items():
                values[user_id] = values.get(user_id, 0) + karma_base
        return values

    async def _get_iris_karma_base(self, *, chat_id: int, user_id: int) -> int:
        row = await self._session.get(
            UserChatIrisImportStateModel,
            {"chat_id": chat_id, "user_id": user_id},
        )
        if row is None:
            return 0
        return int(row.karma_base_all_time or 0)

    async def _get_iris_karma_bases(self, *, chat_id: int, user_ids: Sequence[int] | None) -> dict[int, int]:
        stmt = select(UserChatIrisImportStateModel).where(UserChatIrisImportStateModel.chat_id == chat_id)
        if user_ids is not None:
            if not user_ids:
                return {}
            stmt = stmt.where(UserChatIrisImportStateModel.user_id.in_(list(user_ids)))
        rows = (await self._session.execute(stmt)).scalars().all()
        return {int(row.user_id): int(row.karma_base_all_time or 0) for row in rows}

    async def _get_users_by_ids(self, user_ids: Sequence[int]) -> dict[int, UserModel]:
        if not user_ids:
            return {}

        stmt = select(UserModel).where(UserModel.telegram_user_id.in_(list(user_ids)))
        rows = (await self._session.execute(stmt)).scalars().all()
        return {int(user.telegram_user_id): user for user in rows}

    async def _get_chat_display_overrides(self, *, chat_id: int, user_ids: Sequence[int]) -> dict[int, str]:
        if not user_ids:
            return {}

        stmt = (
            select(
                UserModel.telegram_user_id,
                UserModel.username,
                UserModel.first_name,
                UserModel.last_name,
                UserChatActivityModel.display_name_override,
                UserChatActivityModel.title_prefix,
            )
            .join(UserChatActivityModel, UserChatActivityModel.user_id == UserModel.telegram_user_id)
            .where(
                UserChatActivityModel.chat_id == chat_id,
                UserChatActivityModel.user_id.in_(list(user_ids)),
                or_(
                    UserChatActivityModel.display_name_override.is_not(None),
                    UserChatActivityModel.title_prefix.is_not(None),
                ),
            )
        )
        rows = (await self._session.execute(stmt)).all()
        values: dict[int, str] = {}
        for user_id, username, first_name, last_name, display_name_override, title_prefix in rows:
            normalized = self._compose_chat_display_name(
                user_id=int(user_id),
                username=username,
                first_name=first_name,
                last_name=last_name,
                chat_display_name=display_name_override,
                title_prefix=title_prefix,
            )
            if normalized:
                values[int(user_id)] = normalized
        return values

    async def _get_latest_group_display_overrides(self, *, user_ids: Sequence[int]) -> dict[int, str]:
        if not user_ids:
            return {}

        stmt = (
            select(
                UserModel.telegram_user_id,
                UserModel.username,
                UserModel.first_name,
                UserModel.last_name,
                UserChatActivityModel.display_name_override,
                UserChatActivityModel.title_prefix,
                UserChatActivityModel.last_seen_at,
            )
            .join(UserModel, UserModel.telegram_user_id == UserChatActivityModel.user_id)
            .join(ChatModel, ChatModel.telegram_chat_id == UserChatActivityModel.chat_id)
            .where(
                UserChatActivityModel.user_id.in_(list(user_ids)),
                or_(
                    UserChatActivityModel.display_name_override.is_not(None),
                    UserChatActivityModel.title_prefix.is_not(None),
                ),
                ChatModel.type.in_(["group", "supergroup"]),
            )
            .order_by(UserChatActivityModel.last_seen_at.desc())
        )
        rows = (await self._session.execute(stmt)).all()

        values: dict[int, str] = {}
        for user_id, username, first_name, last_name, display_name_override, title_prefix, _last_seen_at in rows:
            normalized = self._compose_chat_display_name(
                user_id=int(user_id),
                username=username,
                first_name=first_name,
                last_name=last_name,
                chat_display_name=display_name_override,
                title_prefix=title_prefix,
            )
            if not normalized:
                continue

            normalized_user_id = int(user_id)
            if normalized_user_id in values:
                continue
            values[normalized_user_id] = normalized
        return values

    @staticmethod
    def _to_stats(activity: UserChatActivityModel, user: UserModel) -> ActivityStats:
        return ActivityStats(
            chat_id=activity.chat_id,
            user_id=activity.user_id,
            message_count=activity.message_count,
            last_seen_at=activity.last_seen_at,
            first_seen_at=activity.created_at,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            chat_display_name=SqlAlchemyActivityRepository._compose_chat_display_name(
                user_id=int(user.telegram_user_id),
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                chat_display_name=activity.display_name_override,
                title_prefix=activity.title_prefix,
            ),
        )

    @staticmethod
    def _to_user_snapshot(
        user: UserModel,
        *,
        chat_display_name: str | None = None,
        title_prefix: str | None = None,
    ) -> UserSnapshot:
        normalized = SqlAlchemyActivityRepository._compose_chat_display_name(
            user_id=int(user.telegram_user_id),
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            chat_display_name=chat_display_name,
            title_prefix=title_prefix,
        )
        return UserSnapshot(
            telegram_user_id=int(user.telegram_user_id),
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            is_bot=bool(user.is_bot),
            chat_display_name=normalized,
        )

    @staticmethod
    def _compose_chat_display_name(
        *,
        user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        chat_display_name: str | None,
        title_prefix: str | None,
    ) -> str | None:
        alias = (chat_display_name or "").strip() or None
        title = _normalize_title_prefix(title_prefix)
        if alias is None and title is None:
            return None

        base = alias
        if not base:
            base = display_name_from_parts(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                chat_display_name=None,
            )

        if title:
            return f"[{title}] {base}"
        return base

    @staticmethod
    def _to_chat_trigger(row: ChatTriggerModel) -> ChatTrigger:
        return ChatTrigger(
            id=int(row.id),
            chat_id=int(row.chat_id),
            keyword=row.keyword,
            keyword_norm=row.keyword_norm,
            match_type=row.match_type,  # type: ignore[arg-type]
            response_text=row.response_text,
            media_file_id=row.media_file_id,
            media_type=row.media_type,
            created_by_user_id=int(row.created_by_user_id) if row.created_by_user_id is not None else None,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_custom_social_action(row: ChatCustomSocialActionModel) -> CustomSocialAction:
        return CustomSocialAction(
            id=int(row.id),
            chat_id=int(row.chat_id),
            trigger_text=row.trigger_text,
            trigger_text_norm=row.trigger_text_norm,
            response_template=row.response_template,
            created_by_user_id=int(row.created_by_user_id) if row.created_by_user_id is not None else None,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_graph_relationship(row: RelationshipGraphModel) -> GraphRelationship:
        return GraphRelationship(
            id=int(row.id),
            chat_id=int(row.chat_id),
            user_a=int(row.user_a),
            user_b=int(row.user_b),
            relation_type=row.relation_type,  # type: ignore[arg-type]
            created_by_user_id=int(row.created_by_user_id) if row.created_by_user_id is not None else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_chat_audit_log(row: ChatAuditLogModel) -> ChatAuditLogEntry:
        return ChatAuditLogEntry(
            id=int(row.id),
            chat_id=int(row.chat_id),
            actor_user_id=int(row.actor_user_id) if row.actor_user_id is not None else None,
            target_user_id=int(row.target_user_id) if row.target_user_id is not None else None,
            action_code=row.action_code,
            description=row.description,
            meta_json=row.meta_json if isinstance(row.meta_json, dict) else None,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_chat_role_definition(row: ChatRoleDefinitionModel) -> ChatRoleDefinition:
        raw_permissions = row.permissions if isinstance(row.permissions, list) else []
        permissions = tuple(
            sorted(
                {
                    str(item).strip().lower()
                    for item in raw_permissions
                    if str(item).strip() and str(item).strip().lower() in BOT_PERMISSIONS
                }
            )
        )
        return ChatRoleDefinition(
            chat_id=int(row.chat_id),
            role_code=normalize_assigned_role_code(str(row.role_code)) or normalize_role_code(str(row.role_code)),
            title_ru=normalize_role_title(row.title_ru),
            rank=int(row.rank),
            permissions=permissions,
            is_system=bool(row.is_system),
            template_key=(row.template_key or "").strip() or None,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_chat_command_access_rule(row: ChatCommandAccessRuleModel) -> ChatCommandAccessRule:
        return ChatCommandAccessRule(
            chat_id=int(row.chat_id),
            command_key=SqlAlchemyActivityRepository._normalize_command_key(row.command_key),
            min_role_code=normalize_assigned_role_code(row.min_role_code) or normalize_role_code(row.min_role_code),
            updated_by_user_id=int(row.updated_by_user_id) if row.updated_by_user_id is not None else None,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_chat_text_alias(row: ChatTextAliasModel) -> ChatTextAlias:
        return ChatTextAlias(
            id=int(row.id),
            chat_id=int(row.chat_id),
            command_key=row.command_key,
            alias_text_norm=row.alias_text_norm,
            source_trigger_norm=row.source_trigger_norm,
            created_by_user_id=int(row.created_by_user_id) if row.created_by_user_id is not None else None,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_inline_private_message(row: InlinePrivateMessageModel) -> InlinePrivateMessage:
        ids_raw = row.receiver_ids if isinstance(row.receiver_ids, list) else []
        receiver_ids = tuple(int(item) for item in ids_raw if isinstance(item, int) or str(item).isdigit())
        usernames_raw = row.receiver_usernames if isinstance(row.receiver_usernames, list) else []
        receiver_usernames = tuple(
            str(item).lstrip("@").strip().lower()
            for item in usernames_raw
            if str(item).lstrip("@").strip()
        )
        return InlinePrivateMessage(
            id=str(row.id),
            chat_id=int(row.chat_id) if row.chat_id is not None else None,
            chat_instance=(row.chat_instance or "").strip() or None,
            sender_id=int(row.sender_id),
            receiver_ids=receiver_ids,
            receiver_usernames=receiver_usernames,
            text=row.text,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_user_chat_profile(row: UserChatProfileModel) -> UserChatProfile:
        return UserChatProfile(
            chat_id=int(row.chat_id),
            user_id=int(row.user_id),
            description=(row.description_text or "").strip() or None,
            avatar_frame_code=(row.avatar_frame_code or "").strip() or None,
            emoji_status_code=(row.emoji_status_code or "").strip() or None,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_user_chat_award(row: UserChatAwardModel) -> UserChatAward:
        return UserChatAward(
            id=int(row.id),
            chat_id=int(row.chat_id),
            user_id=int(row.user_id),
            title=row.title,
            granted_by_user_id=int(row.granted_by_user_id) if row.granted_by_user_id is not None else None,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_iris_import_state(row: UserChatIrisImportStateModel) -> IrisImportState:
        return IrisImportState(
            chat_id=int(row.chat_id),
            user_id=int(row.user_id),
            imported_at=row.imported_at,
            imported_by_user_id=int(row.imported_by_user_id) if row.imported_by_user_id is not None else None,
            source_bot_username=row.source_bot_username,
            source_target_username=row.source_target_username,
            karma_base_all_time=int(row.karma_base_all_time),
        )

    @staticmethod
    def _to_relationship_proposal(row: RelationshipProposalModel) -> RelationshipProposal:
        return RelationshipProposal(
            id=int(row.id),
            kind=row.kind,  # type: ignore[arg-type]
            proposer_user_id=int(row.proposer_user_id),
            target_user_id=int(row.target_user_id),
            user_low_id=int(row.user_low_id),
            user_high_id=int(row.user_high_id),
            status=row.status,  # type: ignore[arg-type]
            chat_id=int(row.chat_id) if row.chat_id is not None else None,
            created_at=row.created_at,
            expires_at=row.expires_at,
            responded_at=row.responded_at,
        )

    @staticmethod
    def _to_pair_state(row: PairModel) -> PairState:
        return PairState(
            id=int(row.id),
            user_low_id=int(row.user_low_id),
            user_high_id=int(row.user_high_id),
            chat_id=int(row.chat_id) if row.chat_id is not None else None,
            paired_at=row.paired_at,
            affection_points=int(row.affection_points),
            last_affection_at=row.last_affection_at,
            last_affection_by_user_id=int(row.last_affection_by_user_id) if row.last_affection_by_user_id is not None else None,
        )

    @staticmethod
    def _to_marriage_state(row: MarriageModel) -> MarriageState:
        return MarriageState(
            id=int(row.id),
            user_low_id=int(row.user_low_id),
            user_high_id=int(row.user_high_id),
            chat_id=int(row.chat_id) if row.chat_id is not None else None,
            married_at=row.married_at,
            affection_points=int(row.affection_points),
            last_affection_at=row.last_affection_at,
            last_affection_by_user_id=int(row.last_affection_by_user_id) if row.last_affection_by_user_id is not None else None,
            is_active=bool(row.is_active),
            ended_at=row.ended_at,
            ended_by_user_id=int(row.ended_by_user_id) if row.ended_by_user_id is not None else None,
            ended_reason=row.ended_reason,
        )

    @staticmethod
    def _relationship_state_from_pair(value: PairState | None) -> RelationshipState | None:
        if value is None:
            return None
        return RelationshipState(
            kind="pair",
            id=value.id,
            user_low_id=value.user_low_id,
            user_high_id=value.user_high_id,
            chat_id=value.chat_id,
            started_at=value.paired_at,
            affection_points=value.affection_points,
            last_affection_at=value.last_affection_at,
            last_affection_by_user_id=value.last_affection_by_user_id,
        )

    @staticmethod
    def _relationship_state_from_marriage(value: MarriageState | None) -> RelationshipState | None:
        if value is None:
            return None
        return RelationshipState(
            kind="marriage",
            id=value.id,
            user_low_id=value.user_low_id,
            user_high_id=value.user_high_id,
            chat_id=value.chat_id,
            started_at=value.married_at,
            affection_points=value.affection_points,
            last_affection_at=value.last_affection_at,
            last_affection_by_user_id=value.last_affection_by_user_id,
        )

    @staticmethod
    def _marriage_state_from_relationship(value: RelationshipState) -> MarriageState:
        return MarriageState(
            id=value.id,
            user_low_id=value.user_low_id,
            user_high_id=value.user_high_id,
            chat_id=value.chat_id,
            married_at=value.started_at,
            affection_points=value.affection_points,
            last_affection_at=value.last_affection_at,
            last_affection_by_user_id=value.last_affection_by_user_id,
            is_active=True,
            ended_at=None,
            ended_by_user_id=None,
            ended_reason=None,
        )

    @staticmethod
    def _partner_id_for_pair(user_low_id: int, user_high_id: int, user_id: int) -> int:
        return user_high_id if user_low_id == user_id else user_low_id

    @staticmethod
    def _sorted_user_pair(user_a_id: int, user_b_id: int) -> tuple[int, int]:
        if user_a_id <= user_b_id:
            return user_a_id, user_b_id
        return user_b_id, user_a_id

    @staticmethod
    def _to_moderation_state(row: UserChatModerationStateModel) -> ModerationState:
        return ModerationState(
            chat_id=int(row.chat_id),
            user_id=int(row.user_id),
            pending_preds=int(row.pending_preds),
            warn_count=int(row.warn_count),
            total_preds=int(row.total_preds),
            total_warns=int(row.total_warns),
            total_bans=int(row.total_bans),
            is_banned=bool(row.is_banned),
            last_reason=row.last_reason,
            updated_at=row.updated_at,
        )


class SqlAlchemyEconomyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def set_private_chat_context(self, *, user_id: int, chat_id: int) -> None:
        await self._upsert_chat(ChatSnapshot(telegram_chat_id=chat_id, chat_type="group", title=None))
        await self._upsert_user(
            UserSnapshot(
                telegram_user_id=user_id,
                username=None,
                first_name=None,
                last_name=None,
                is_bot=False,
            )
        )

        row = await self._session.get(EconomyPrivateContextModel, user_id)
        if row is None:
            row = EconomyPrivateContextModel(user_id=user_id, chat_id=chat_id)
            self._session.add(row)
        else:
            row.chat_id = chat_id
            row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def get_private_chat_context(self, *, user_id: int) -> int | None:
        row = await self._session.get(EconomyPrivateContextModel, user_id)
        if row is None:
            return None
        return int(row.chat_id)

    async def resolve_scope(
        self,
        *,
        mode: str,
        chat_id: int | None,
        user_id: int,
    ) -> tuple[EconomyScope | None, str | None]:
        normalized = (mode or "global").lower()
        if normalized == "global":
            return EconomyScope(scope_id="global", scope_type="global", chat_id=None), None

        if normalized != "local":
            return None, "Некорректный режим экономики. Ожидается global/local."

        if chat_id is not None:
            return EconomyScope(scope_id=f"chat:{chat_id}", scope_type="chat", chat_id=chat_id), None

        context_chat_id = await self.get_private_chat_context(user_id=user_id)
        if context_chat_id is None:
            return None, "Для local-режима откройте экономику из группы (deep-link /start eco_<chat_id>)."
        return EconomyScope(scope_id=f"chat:{context_chat_id}", scope_type="chat", chat_id=context_chat_id), None

    async def get_or_create_account(
        self,
        *,
        scope: EconomyScope,
        user_id: int,
    ) -> tuple[EconomyAccount, FarmState]:
        if scope.chat_id is not None:
            await self._upsert_chat(ChatSnapshot(telegram_chat_id=scope.chat_id, chat_type="group", title=None))
        await self._upsert_user(
            UserSnapshot(
                telegram_user_id=user_id,
                username=None,
                first_name=None,
                last_name=None,
                is_bot=False,
            )
        )

        stmt = select(EconomyAccountModel).where(
            EconomyAccountModel.scope_id == scope.scope_id,
            EconomyAccountModel.user_id == user_id,
        )
        account_row = (await self._session.execute(stmt)).scalar_one_or_none()
        if account_row is None:
            account_row = EconomyAccountModel(
                scope_id=scope.scope_id,
                scope_type=scope.scope_type,
                chat_id=scope.chat_id,
                user_id=user_id,
                balance=0,
            )
            self._session.add(account_row)
            await self._session.flush()

        farm_row = await self._session.get(EconomyFarmModel, account_row.id)
        if farm_row is None:
            farm_row = EconomyFarmModel(
                account_id=account_row.id,
                farm_level=1,
                size_tier="small",
                negative_event_streak=0,
                last_planted_crop_code=None,
            )
            self._session.add(farm_row)
            await self._session.flush()

        # Pre-create empty plots up to max capacity for simpler handlers.
        for plot_no in range(1, 7):
            plot = await self.get_plot(account_id=account_row.id, plot_no=plot_no)
            if plot is None:
                self._session.add(
                    EconomyPlotModel(
                        account_id=account_row.id,
                        plot_no=plot_no,
                        crop_code=None,
                        planted_at=None,
                        ready_at=None,
                        yield_boost_pct=0,
                        shield_active=False,
                    )
                )
        await self._session.flush()

        return self._to_economy_account(account_row), self._to_farm_state(farm_row)

    async def get_account(
        self,
        *,
        scope: EconomyScope,
        user_id: int,
    ) -> EconomyAccount | None:
        stmt = select(EconomyAccountModel).where(
            EconomyAccountModel.scope_id == scope.scope_id,
            EconomyAccountModel.user_id == user_id,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return self._to_economy_account(row)

    async def list_plots(self, *, account_id: int) -> list[PlotState]:
        stmt = select(EconomyPlotModel).where(EconomyPlotModel.account_id == account_id).order_by(EconomyPlotModel.plot_no.asc())
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_plot_state(row) for row in rows]

    async def get_plot(self, *, account_id: int, plot_no: int) -> PlotState | None:
        stmt = select(EconomyPlotModel).where(
            EconomyPlotModel.account_id == account_id,
            EconomyPlotModel.plot_no == plot_no,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return self._to_plot_state(row)

    async def upsert_plot(
        self,
        *,
        account_id: int,
        plot_no: int,
        crop_code: str | None,
        planted_at: datetime | None,
        ready_at: datetime | None,
        yield_boost_pct: int,
        shield_active: bool,
    ) -> PlotState:
        stmt = select(EconomyPlotModel).where(
            EconomyPlotModel.account_id == account_id,
            EconomyPlotModel.plot_no == plot_no,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = EconomyPlotModel(account_id=account_id, plot_no=plot_no)
            self._session.add(row)

        row.crop_code = crop_code
        row.planted_at = planted_at
        row.ready_at = ready_at
        row.yield_boost_pct = max(0, int(yield_boost_pct))
        row.shield_active = bool(shield_active)
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return self._to_plot_state(row)

    async def list_inventory(self, *, account_id: int) -> list[InventoryItem]:
        stmt = select(EconomyInventoryModel).where(
            EconomyInventoryModel.account_id == account_id,
            EconomyInventoryModel.quantity > 0,
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_inventory_item(row) for row in rows]

    async def get_inventory_item(self, *, account_id: int, item_code: str) -> InventoryItem | None:
        row = await self._session.get(EconomyInventoryModel, {"account_id": account_id, "item_code": item_code})
        if row is None:
            return None
        return self._to_inventory_item(row)

    async def add_inventory_item(self, *, account_id: int, item_code: str, delta: int) -> InventoryItem:
        row = await self._session.get(EconomyInventoryModel, {"account_id": account_id, "item_code": item_code})
        if row is None:
            if delta < 0:
                raise ValueError("Cannot subtract missing inventory item")
            row = EconomyInventoryModel(account_id=account_id, item_code=item_code, quantity=0)
            self._session.add(row)

        row.quantity = int(row.quantity) + int(delta)
        if row.quantity < 0:
            raise ValueError("Inventory quantity cannot be negative")

        if row.quantity == 0:
            await self._session.delete(row)
            await self._session.flush()
            return InventoryItem(account_id=account_id, item_code=item_code, quantity=0)

        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return self._to_inventory_item(row)

    async def add_balance(self, *, account_id: int, delta: int) -> int:
        row = await self._session.get(EconomyAccountModel, account_id)
        if row is None:
            raise RuntimeError("Economy account not found")

        new_balance = int(row.balance) + int(delta)
        if new_balance < 0:
            raise ValueError("Insufficient balance")

        row.balance = new_balance
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return new_balance

    async def update_tap_state(
        self,
        *,
        account_id: int,
        tap_streak: int,
        last_tap_at: datetime,
    ) -> None:
        row = await self._session.get(EconomyAccountModel, account_id)
        if row is None:
            return
        row.tap_streak = max(0, int(tap_streak))
        row.last_tap_at = last_tap_at
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def update_daily_state(
        self,
        *,
        account_id: int,
        daily_streak: int,
        last_daily_claimed_at: datetime,
    ) -> None:
        row = await self._session.get(EconomyAccountModel, account_id)
        if row is None:
            return
        row.daily_streak = max(0, int(daily_streak))
        row.last_daily_claimed_at = last_daily_claimed_at
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def mark_free_lottery_claimed(
        self,
        *,
        account_id: int,
        claimed_on: date,
    ) -> None:
        row = await self._session.get(EconomyAccountModel, account_id)
        if row is None:
            return
        row.free_lottery_claimed_on = claimed_on
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def increment_paid_lottery_used(
        self,
        *,
        account_id: int,
        used_on: date,
    ) -> int:
        row = await self._session.get(EconomyAccountModel, account_id)
        if row is None:
            return 0

        if row.paid_lottery_used_on != used_on:
            row.paid_lottery_used_on = used_on
            row.paid_lottery_used_today = 1
        else:
            row.paid_lottery_used_today = int(row.paid_lottery_used_today) + 1

        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return int(row.paid_lottery_used_today)

    async def set_paid_lottery_used(
        self,
        *,
        account_id: int,
        used_on: date,
        used_count: int,
    ) -> None:
        row = await self._session.get(EconomyAccountModel, account_id)
        if row is None:
            return
        row.paid_lottery_used_on = used_on
        row.paid_lottery_used_today = max(0, int(used_count))
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def update_farm_level(self, *, account_id: int, farm_level: int) -> None:
        row = await self._session.get(EconomyFarmModel, account_id)
        if row is None:
            row = EconomyFarmModel(
                account_id=account_id,
                farm_level=max(1, int(farm_level)),
                size_tier="small",
                last_planted_crop_code=None,
            )
            self._session.add(row)
        else:
            row.farm_level = max(1, int(farm_level))
            row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def update_farm_size_tier(self, *, account_id: int, size_tier: str) -> None:
        row = await self._session.get(EconomyFarmModel, account_id)
        if row is None:
            row = EconomyFarmModel(
                account_id=account_id,
                farm_level=1,
                size_tier=size_tier,
                last_planted_crop_code=None,
            )
            self._session.add(row)
        else:
            row.size_tier = size_tier
            row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def get_farm_state(self, *, account_id: int) -> FarmState | None:
        row = await self._session.get(EconomyFarmModel, account_id)
        if row is None:
            return None
        return self._to_farm_state(row)

    async def set_last_planted_crop_code(self, *, account_id: int, crop_code: str | None) -> None:
        row = await self._session.get(EconomyFarmModel, account_id)
        if row is None:
            row = EconomyFarmModel(
                account_id=account_id,
                farm_level=1,
                size_tier="small",
                negative_event_streak=0,
                last_planted_crop_code=crop_code,
            )
            self._session.add(row)
        else:
            row.last_planted_crop_code = crop_code
            row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def set_negative_event_streak(self, *, account_id: int, value: int) -> None:
        row = await self._session.get(EconomyFarmModel, account_id)
        if row is None:
            row = EconomyFarmModel(
                account_id=account_id,
                farm_level=1,
                size_tier="small",
                negative_event_streak=max(0, value),
                last_planted_crop_code=None,
            )
            self._session.add(row)
        else:
            row.negative_event_streak = max(0, int(value))
            row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def set_upgrade_level(self, *, account_id: int, upgrade_code: str, new_level: int) -> None:
        row = await self._session.get(EconomyAccountModel, account_id)
        if row is None:
            return

        value = max(0, int(new_level))
        if upgrade_code == "sprinkler":
            row.sprinkler_level = value
        elif upgrade_code == "tap_glove":
            row.tap_glove_level = value
        elif upgrade_code == "storage_rack":
            row.storage_level = value
        else:
            raise ValueError(f"Unknown upgrade code: {upgrade_code}")

        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def update_growth_state(
        self,
        *,
        account_id: int,
        growth_size_mm: int,
        growth_stress_pct: int,
        growth_actions: int,
        last_growth_at: datetime | None,
        growth_boost_pct: int,
        growth_cooldown_discount_seconds: int,
    ) -> None:
        row = await self._session.get(EconomyAccountModel, account_id)
        if row is None:
            return

        row.growth_size_mm = max(0, int(growth_size_mm))
        row.growth_stress_pct = max(0, min(100, int(growth_stress_pct)))
        row.growth_actions = max(0, int(growth_actions))
        row.last_growth_at = last_growth_at
        row.growth_boost_pct = max(0, int(growth_boost_pct))
        row.growth_cooldown_discount_seconds = max(0, int(growth_cooldown_discount_seconds))
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def create_market_listing(
        self,
        *,
        scope: EconomyScope,
        chat_id: int | None,
        seller_user_id: int,
        item_code: str,
        qty_total: int,
        unit_price: int,
        fee_paid: int,
        expires_at: datetime,
    ) -> MarketListing:
        row = EconomyMarketListingModel(
            scope_id=scope.scope_id,
            scope_type=scope.scope_type,
            chat_id=chat_id,
            seller_user_id=seller_user_id,
            item_code=item_code,
            qty_total=qty_total,
            qty_left=qty_total,
            unit_price=unit_price,
            fee_paid=fee_paid,
            status="open",
            expires_at=expires_at,
        )
        self._session.add(row)
        await self._session.flush()
        return self._to_market_listing(row)

    async def list_market_open(self, *, scope: EconomyScope, limit: int = 20) -> list[MarketListing]:
        stmt = (
            select(EconomyMarketListingModel)
            .where(
                EconomyMarketListingModel.scope_id == scope.scope_id,
                EconomyMarketListingModel.status == "open",
            )
            .order_by(EconomyMarketListingModel.created_at.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_market_listing(row) for row in rows]

    async def get_market_listing(self, *, listing_id: int) -> MarketListing | None:
        row = await self._session.get(EconomyMarketListingModel, listing_id)
        if row is None:
            return None
        return self._to_market_listing(row)

    async def update_market_listing_qty_and_status(
        self,
        *,
        listing_id: int,
        qty_left: int,
        status: str,
    ) -> None:
        row = await self._session.get(EconomyMarketListingModel, listing_id)
        if row is None:
            return
        row.qty_left = max(0, int(qty_left))
        row.status = status
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def count_open_market_listings_for_seller(self, *, scope: EconomyScope, seller_user_id: int) -> int:
        stmt = select(func.count(EconomyMarketListingModel.id)).where(
            EconomyMarketListingModel.scope_id == scope.scope_id,
            EconomyMarketListingModel.seller_user_id == seller_user_id,
            EconomyMarketListingModel.status == "open",
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def create_market_trade(
        self,
        *,
        listing: MarketListing,
        buyer_user_id: int,
        quantity: int,
        total_price: int,
        created_at: datetime,
    ) -> MarketTrade:
        await self._upsert_user(
            UserSnapshot(
                telegram_user_id=buyer_user_id,
                username=None,
                first_name=None,
                last_name=None,
                is_bot=False,
            )
        )
        row = EconomyMarketTradeModel(
            listing_id=listing.id,
            scope_id=listing.scope_id,
            scope_type=listing.scope_type,
            chat_id=listing.chat_id,
            seller_user_id=listing.seller_user_id,
            buyer_user_id=buyer_user_id,
            item_code=listing.item_code,
            quantity=max(1, int(quantity)),
            unit_price=max(1, int(listing.unit_price)),
            total_price=max(1, int(total_price)),
            created_at=created_at,
        )
        self._session.add(row)
        await self._session.flush()
        return self._to_market_trade(row)

    async def list_market_trades(
        self,
        *,
        scope: EconomyScope,
        item_code: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[MarketTrade]:
        stmt = select(EconomyMarketTradeModel).where(EconomyMarketTradeModel.scope_id == scope.scope_id)
        if item_code:
            stmt = stmt.where(EconomyMarketTradeModel.item_code == item_code)
        if since is not None:
            stmt = stmt.where(EconomyMarketTradeModel.created_at >= since)
        stmt = stmt.order_by(EconomyMarketTradeModel.created_at.desc()).limit(max(1, int(limit)))
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_market_trade(row) for row in rows]

    async def create_chat_boost(
        self,
        *,
        chat_id: int,
        scope: EconomyScope,
        boost_code: str,
        value_percent: int,
        starts_at: datetime,
        ends_at: datetime,
        created_by_user_id: int,
    ) -> ChatBoost:
        await self._upsert_chat(ChatSnapshot(telegram_chat_id=chat_id, chat_type="group", title=None))
        await self._upsert_user(
            UserSnapshot(
                telegram_user_id=created_by_user_id,
                username=None,
                first_name=None,
                last_name=None,
                is_bot=False,
            )
        )
        row = ChatGlobalBoostModel(
            chat_id=chat_id,
            scope_id=scope.scope_id,
            scope_type=scope.scope_type,
            boost_code=boost_code,
            value_percent=max(1, int(value_percent)),
            starts_at=starts_at,
            ends_at=ends_at,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(row)
        await self._session.flush()
        return self._to_chat_boost(row)

    async def list_active_chat_boosts(
        self,
        *,
        chat_id: int,
        as_of: datetime | None = None,
    ) -> list[ChatBoost]:
        point = as_of or datetime.now(timezone.utc)
        stmt = (
            select(ChatGlobalBoostModel)
            .where(
                ChatGlobalBoostModel.chat_id == chat_id,
                ChatGlobalBoostModel.starts_at <= point,
                ChatGlobalBoostModel.ends_at >= point,
            )
            .order_by(ChatGlobalBoostModel.created_at.desc(), ChatGlobalBoostModel.id.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_chat_boost(row) for row in rows]

    async def create_chat_auction(
        self,
        *,
        chat_id: int,
        scope: EconomyScope,
        seller_user_id: int,
        item_code: str,
        quantity: int,
        start_price: int,
        min_increment: int,
        ends_at: datetime,
        message_id: int | None,
    ) -> ChatAuction:
        await self._upsert_chat(ChatSnapshot(telegram_chat_id=chat_id, chat_type="group", title=None))
        await self._upsert_user(
            UserSnapshot(
                telegram_user_id=seller_user_id,
                username=None,
                first_name=None,
                last_name=None,
                is_bot=False,
            )
        )
        row = ChatAuctionModel(
            chat_id=chat_id,
            scope_id=scope.scope_id,
            scope_type=scope.scope_type,
            seller_user_id=seller_user_id,
            item_code=item_code,
            quantity=max(1, int(quantity)),
            start_price=max(1, int(start_price)),
            current_bid=0,
            highest_bid_user_id=None,
            min_increment=max(1, int(min_increment)),
            status="open",
            message_id=message_id,
            ends_at=ends_at,
        )
        self._session.add(row)
        await self._session.flush()
        return self._to_chat_auction(row)

    async def get_chat_auction(self, *, auction_id: int) -> ChatAuction | None:
        row = await self._session.get(ChatAuctionModel, auction_id)
        if row is None:
            return None
        return self._to_chat_auction(row)

    async def get_active_chat_auction(self, *, chat_id: int) -> ChatAuction | None:
        stmt = (
            select(ChatAuctionModel)
            .where(
                ChatAuctionModel.chat_id == chat_id,
                ChatAuctionModel.status == "open",
            )
            .order_by(ChatAuctionModel.created_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._to_chat_auction(row) if row is not None else None

    async def update_chat_auction_bid(
        self,
        *,
        auction_id: int,
        current_bid: int,
        highest_bid_user_id: int | None,
        message_id: int | None = None,
    ) -> ChatAuction | None:
        row = await self._session.get(ChatAuctionModel, auction_id)
        if row is None:
            return None
        if highest_bid_user_id is not None:
            await self._upsert_user(
                UserSnapshot(
                    telegram_user_id=highest_bid_user_id,
                    username=None,
                    first_name=None,
                    last_name=None,
                    is_bot=False,
                )
            )
        row.current_bid = max(0, int(current_bid))
        row.highest_bid_user_id = highest_bid_user_id
        if message_id is not None:
            row.message_id = message_id
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return self._to_chat_auction(row)

    async def close_chat_auction(
        self,
        *,
        auction_id: int,
        status: str,
        closed_at: datetime,
        message_id: int | None = None,
    ) -> ChatAuction | None:
        row = await self._session.get(ChatAuctionModel, auction_id)
        if row is None:
            return None
        row.status = status
        row.closed_at = closed_at
        if message_id is not None:
            row.message_id = message_id
        row.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return self._to_chat_auction(row)

    async def touch_transfer_daily(
        self,
        *,
        account_id: int,
        limit_date: date,
        sent_delta: int,
        count_delta: int,
    ) -> tuple[int, int]:
        row = await self._session.get(
            EconomyTransferDailyModel,
            {"account_id": account_id, "limit_date": limit_date},
        )
        if row is None:
            row = EconomyTransferDailyModel(
                account_id=account_id,
                limit_date=limit_date,
                sent_amount=max(0, int(sent_delta)),
                sent_count=max(0, int(count_delta)),
            )
            self._session.add(row)
        else:
            row.sent_amount = max(0, int(row.sent_amount) + int(sent_delta))
            row.sent_count = max(0, int(row.sent_count) + int(count_delta))
            row.updated_at = datetime.now(timezone.utc)

        await self._session.flush()
        return int(row.sent_amount), int(row.sent_count)

    async def get_transfer_daily(self, *, account_id: int, limit_date: date) -> tuple[int, int]:
        row = await self._session.get(
            EconomyTransferDailyModel,
            {"account_id": account_id, "limit_date": limit_date},
        )
        if row is None:
            return 0, 0
        return int(row.sent_amount), int(row.sent_count)

    async def add_ledger(
        self,
        *,
        account_id: int,
        direction: str,
        amount: int,
        reason: str,
        meta_json: str,
    ) -> None:
        import json

        payload: dict | None
        try:
            payload = json.loads(meta_json)
            if not isinstance(payload, dict):
                payload = {"value": payload}
        except Exception:
            payload = {"raw": meta_json}

        self._session.add(
            EconomyLedgerModel(
                account_id=account_id,
                direction=direction,
                amount=max(0, int(amount)),
                reason=reason,
                meta_json=payload,
            )
        )
        await self._session.flush()

    async def _upsert_user(self, user: UserSnapshot) -> None:
        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"

        if dialect == "postgresql":
            insert_stmt = (
                pg_insert(UserModel)
                .values(
                    telegram_user_id=user.telegram_user_id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    is_bot=user.is_bot,
                )
                .on_conflict_do_nothing(index_elements=[UserModel.telegram_user_id])
                .returning(UserModel.telegram_user_id)
            )
            inserted_user_id = (await self._session.execute(insert_stmt)).scalar_one_or_none()
            if inserted_user_id is not None:
                await increment_global_users_base_count(self._session)

            stmt = pg_insert(UserModel).values(
                telegram_user_id=user.telegram_user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                is_bot=user.is_bot,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[UserModel.telegram_user_id],
                set_={
                    "username": stmt.excluded.username,
                    "first_name": stmt.excluded.first_name,
                    "last_name": stmt.excluded.last_name,
                    "is_bot": stmt.excluded.is_bot,
                    "updated_at": func.now(),
                },
            )
            await self._session.execute(stmt)
            return

        user_row = await self._session.get(UserModel, user.telegram_user_id)
        if user_row is None:
            self._session.add(
                UserModel(
                    telegram_user_id=user.telegram_user_id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    is_bot=user.is_bot,
                )
            )
            await self._session.flush()
            await increment_global_users_base_count(self._session)
            return

        user_row.username = user.username
        user_row.first_name = user.first_name
        user_row.last_name = user.last_name
        user_row.is_bot = user.is_bot

    async def _upsert_chat(self, chat: ChatSnapshot) -> None:
        dialect = self._session.bind.dialect.name if self._session.bind else "unknown"

        if dialect == "postgresql":
            stmt = pg_insert(ChatModel).values(
                telegram_chat_id=chat.telegram_chat_id,
                type=chat.chat_type,
                title=chat.title,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[ChatModel.telegram_chat_id],
                set_={
                    "type": stmt.excluded.type,
                    "title": stmt.excluded.title,
                    "updated_at": func.now(),
                },
            )
            await self._session.execute(stmt)
            return

        chat_row = await self._session.get(ChatModel, chat.telegram_chat_id)
        if chat_row is None:
            self._session.add(
                ChatModel(
                    telegram_chat_id=chat.telegram_chat_id,
                    type=chat.chat_type,
                    title=chat.title,
                )
            )
            return

        chat_row.type = chat.chat_type
        chat_row.title = chat.title

    @staticmethod
    def _to_economy_account(row: EconomyAccountModel) -> EconomyAccount:
        return EconomyAccount(
            id=int(row.id),
            scope_id=row.scope_id,
            scope_type=row.scope_type,  # type: ignore[arg-type]
            chat_id=int(row.chat_id) if row.chat_id is not None else None,
            user_id=int(row.user_id),
            balance=int(row.balance),
            tap_streak=int(row.tap_streak),
            last_tap_at=row.last_tap_at,
            daily_streak=int(row.daily_streak),
            last_daily_claimed_at=row.last_daily_claimed_at,
            free_lottery_claimed_on=row.free_lottery_claimed_on,
            paid_lottery_used_today=int(row.paid_lottery_used_today),
            paid_lottery_used_on=row.paid_lottery_used_on,
            sprinkler_level=int(row.sprinkler_level),
            tap_glove_level=int(row.tap_glove_level),
            storage_level=int(row.storage_level),
            growth_size_mm=int(row.growth_size_mm),
            growth_stress_pct=int(row.growth_stress_pct),
            growth_actions=int(row.growth_actions),
            last_growth_at=row.last_growth_at,
            growth_boost_pct=int(row.growth_boost_pct),
            growth_cooldown_discount_seconds=int(row.growth_cooldown_discount_seconds),
        )

    @staticmethod
    def _to_farm_state(row: EconomyFarmModel) -> FarmState:
        return FarmState(
            account_id=int(row.account_id),
            farm_level=int(row.farm_level),
            size_tier=row.size_tier,
            negative_event_streak=int(row.negative_event_streak),
            last_planted_crop_code=row.last_planted_crop_code,
        )

    @staticmethod
    def _to_plot_state(row: EconomyPlotModel) -> PlotState:
        return PlotState(
            id=int(row.id),
            account_id=int(row.account_id),
            plot_no=int(row.plot_no),
            crop_code=row.crop_code,
            planted_at=row.planted_at,
            ready_at=row.ready_at,
            yield_boost_pct=int(row.yield_boost_pct),
            shield_active=bool(row.shield_active),
        )

    @staticmethod
    def _to_inventory_item(row: EconomyInventoryModel) -> InventoryItem:
        return InventoryItem(
            account_id=int(row.account_id),
            item_code=row.item_code,
            quantity=int(row.quantity),
        )

    @staticmethod
    def _to_market_listing(row: EconomyMarketListingModel) -> MarketListing:
        return MarketListing(
            id=int(row.id),
            scope_id=row.scope_id,
            scope_type=row.scope_type,  # type: ignore[arg-type]
            chat_id=int(row.chat_id) if row.chat_id is not None else None,
            seller_user_id=int(row.seller_user_id),
            item_code=row.item_code,
            qty_total=int(row.qty_total),
            qty_left=int(row.qty_left),
            unit_price=int(row.unit_price),
            fee_paid=int(row.fee_paid),
            status=row.status,  # type: ignore[arg-type]
            expires_at=row.expires_at,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_market_trade(row: EconomyMarketTradeModel) -> MarketTrade:
        return MarketTrade(
            id=int(row.id),
            listing_id=int(row.listing_id),
            scope_id=row.scope_id,
            scope_type=row.scope_type,  # type: ignore[arg-type]
            chat_id=int(row.chat_id) if row.chat_id is not None else None,
            seller_user_id=int(row.seller_user_id),
            buyer_user_id=int(row.buyer_user_id),
            item_code=row.item_code,
            quantity=int(row.quantity),
            unit_price=int(row.unit_price),
            total_price=int(row.total_price),
            created_at=row.created_at,
        )

    @staticmethod
    def _to_chat_boost(row: ChatGlobalBoostModel) -> ChatBoost:
        return ChatBoost(
            id=int(row.id),
            chat_id=int(row.chat_id),
            scope_id=row.scope_id,
            scope_type=row.scope_type,  # type: ignore[arg-type]
            boost_code=row.boost_code,
            value_percent=int(row.value_percent),
            starts_at=row.starts_at,
            ends_at=row.ends_at,
            created_by_user_id=int(row.created_by_user_id),
            created_at=row.created_at,
        )

    @staticmethod
    def _to_chat_auction(row: ChatAuctionModel) -> ChatAuction:
        return ChatAuction(
            id=int(row.id),
            chat_id=int(row.chat_id),
            scope_id=row.scope_id,
            scope_type=row.scope_type,  # type: ignore[arg-type]
            seller_user_id=int(row.seller_user_id),
            item_code=row.item_code,
            quantity=int(row.quantity),
            start_price=int(row.start_price),
            current_bid=int(row.current_bid),
            highest_bid_user_id=int(row.highest_bid_user_id) if row.highest_bid_user_id is not None else None,
            min_increment=int(row.min_increment),
            status=row.status,  # type: ignore[arg-type]
            message_id=int(row.message_id) if row.message_id is not None else None,
            ends_at=row.ends_at,
            closed_at=row.closed_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
