from datetime import date, datetime

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from selara.infrastructure.db.base import Base


class UserModel(Base):
    __tablename__ = "users"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_bot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ChatModel(Base):
    __tablename__ = "chats"

    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserChatActivityModel(Base):
    __tablename__ = "user_chat_activity"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    message_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    display_name_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_prefix: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index("idx_user_chat_activity_chat_count", UserChatActivityModel.chat_id, UserChatActivityModel.message_count)
Index("idx_user_chat_activity_chat_last_seen", UserChatActivityModel.chat_id, UserChatActivityModel.last_seen_at)


class UserChatProfileModel(Base):
    __tablename__ = "user_chat_profiles"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    description_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserChatAwardModel(Base):
    __tablename__ = "user_chat_awards"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    granted_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


Index("idx_user_chat_profiles_chat_user", UserChatProfileModel.chat_id, UserChatProfileModel.user_id)
Index("idx_user_chat_awards_chat_user_created", UserChatAwardModel.chat_id, UserChatAwardModel.user_id, UserChatAwardModel.created_at)


class UserChatActivityDailyModel(Base):
    __tablename__ = "user_chat_activity_daily"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    activity_date: Mapped[date] = mapped_column(Date, primary_key=True)
    message_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserChatActivityMinuteModel(Base):
    __tablename__ = "user_chat_activity_minute"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    activity_minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    message_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserKarmaVoteModel(Base):
    __tablename__ = "user_karma_votes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    voter_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    target_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    vote_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (CheckConstraint("vote_value IN (-1, 1)", name="ck_user_karma_votes_vote_value"),)


Index("idx_user_chat_activity_daily_chat_date", UserChatActivityDailyModel.chat_id, UserChatActivityDailyModel.activity_date)
Index("idx_user_chat_activity_minute_chat_minute", UserChatActivityMinuteModel.chat_id, UserChatActivityMinuteModel.activity_minute)
Index("idx_user_karma_votes_chat_target_created", UserKarmaVoteModel.chat_id, UserKarmaVoteModel.target_user_id, UserKarmaVoteModel.created_at)
Index("idx_user_karma_votes_chat_voter_created", UserKarmaVoteModel.chat_id, UserKarmaVoteModel.voter_user_id, UserKarmaVoteModel.created_at)


class UserChatAnnouncementSubscriptionModel(Base):
    __tablename__ = "user_chat_announce_subscriptions"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index(
    "idx_user_chat_announce_subscriptions_chat_enabled",
    UserChatAnnouncementSubscriptionModel.chat_id,
    UserChatAnnouncementSubscriptionModel.is_enabled,
)


class UserChatBotRoleModel(Base):
    __tablename__ = "user_chat_bot_roles"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    assigned_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

Index("idx_user_chat_bot_roles_chat_role", UserChatBotRoleModel.chat_id, UserChatBotRoleModel.role)


class ChatRoleDefinitionModel(Base):
    __tablename__ = "chat_role_definitions"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    title_ru: Mapped[str] = mapped_column(String(128), nullable=False)
    rank: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    permissions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    template_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index("idx_chat_role_definitions_chat_rank", ChatRoleDefinitionModel.chat_id, ChatRoleDefinitionModel.rank)


class ChatCommandAccessRuleModel(Base):
    __tablename__ = "chat_command_access_rules"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    command_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    min_role_code: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index(
    "idx_chat_command_access_rules_chat_role",
    ChatCommandAccessRuleModel.chat_id,
    ChatCommandAccessRuleModel.min_role_code,
)


class UserChatModerationStateModel(Base):
    __tablename__ = "user_chat_moderation_states"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    pending_preds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    warn_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    total_preds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    total_warns: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    total_bans: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    is_banned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    last_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index("idx_user_chat_moderation_states_chat_banned", UserChatModerationStateModel.chat_id, UserChatModerationStateModel.is_banned)


class RelationshipProposalModel(Base):
    __tablename__ = "relationship_proposals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    proposer_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    target_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_low_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_high_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="marriage", server_default="marriage")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('pair', 'marriage')",
            name="ck_relationship_proposals_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'cancelled', 'expired')",
            name="ck_relationship_proposals_status",
        ),
        CheckConstraint("user_low_id < user_high_id", name="ck_relationship_proposals_pair_order"),
    )


Index("idx_relationship_proposals_target_status", RelationshipProposalModel.target_user_id, RelationshipProposalModel.status)
Index(
    "idx_relationship_proposals_chat_pair_status",
    RelationshipProposalModel.chat_id,
    RelationshipProposalModel.user_low_id,
    RelationshipProposalModel.user_high_id,
    RelationshipProposalModel.status,
)
Index(
    "idx_relationship_proposals_chat_pair_kind_status",
    RelationshipProposalModel.chat_id,
    RelationshipProposalModel.user_low_id,
    RelationshipProposalModel.user_high_id,
    RelationshipProposalModel.kind,
    RelationshipProposalModel.status,
)


class PairModel(Base):
    __tablename__ = "pairs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_low_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_high_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="SET NULL"),
        nullable=True,
    )
    paired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    affection_points: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    last_affection_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_affection_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint("user_low_id < user_high_id", name="ck_pairs_pair_order"),
        CheckConstraint("affection_points >= 0", name="ck_pairs_affection_non_negative"),
    )


Index("uq_pairs_chat_pair", PairModel.chat_id, PairModel.user_low_id, PairModel.user_high_id, unique=True)
Index("idx_pairs_user_low", PairModel.user_low_id)
Index("idx_pairs_user_high", PairModel.user_high_id)


class RelationshipActionUsageModel(Base):
    __tablename__ = "relationship_action_usage"

    relationship_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    relationship_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    action_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "relationship_kind IN ('pair', 'marriage')",
            name="ck_relationship_action_usage_kind",
        ),
        CheckConstraint(
            "action_code IN ('care', 'date', 'gift', 'support', 'love', 'flirt', 'surprise', 'vow')",
            name="ck_relationship_action_usage_code",
        ),
    )


Index(
    "idx_relationship_action_usage_lookup",
    RelationshipActionUsageModel.relationship_kind,
    RelationshipActionUsageModel.relationship_id,
    RelationshipActionUsageModel.actor_user_id,
    RelationshipActionUsageModel.action_code,
    unique=True,
)


class MarriageModel(Base):
    __tablename__ = "marriages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_low_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_high_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="SET NULL"),
        nullable=True,
    )
    married_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    affection_points: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    last_affection_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_affection_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint("user_low_id < user_high_id", name="ck_marriages_pair_order"),
        CheckConstraint("affection_points >= 0", name="ck_marriages_affection_non_negative"),
    )


Index("uq_marriages_chat_pair", MarriageModel.chat_id, MarriageModel.user_low_id, MarriageModel.user_high_id, unique=True)
Index("idx_marriages_user_low", MarriageModel.user_low_id)
Index("idx_marriages_user_high", MarriageModel.user_high_id)


class ChatSettingsModel(Base):
    __tablename__ = "chat_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    top_limit_default: Mapped[int] = mapped_column(BigInteger, nullable=False)
    top_limit_max: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vote_daily_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    leaderboard_hybrid_buttons_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    leaderboard_hybrid_karma_weight: Mapped[float] = mapped_column(nullable=False)
    leaderboard_hybrid_activity_weight: Mapped[float] = mapped_column(nullable=False)
    leaderboard_7d_days: Mapped[int] = mapped_column(BigInteger, nullable=False)
    leaderboard_week_start_weekday: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    leaderboard_week_start_hour: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    mafia_night_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=90, server_default="90")
    mafia_day_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=120, server_default="120")
    mafia_vote_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=60, server_default="60")
    mafia_reveal_eliminated_role: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    text_commands_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    text_commands_locale: Mapped[str] = mapped_column(String(8), nullable=False, default="ru", server_default="ru")
    actions_18_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    smart_triggers_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    welcome_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    welcome_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="Привет, {user}! Добро пожаловать в {chat}.",
        server_default="Привет, {user}! Добро пожаловать в {chat}.",
    )
    welcome_button_text: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    welcome_button_url: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    goodbye_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    goodbye_text: Mapped[str] = mapped_column(Text, nullable=False, default="Пока, {user}.", server_default="Пока, {user}.")
    welcome_cleanup_service_messages: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    entry_captcha_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    entry_captcha_timeout_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=180, server_default="180")
    entry_captcha_kick_on_fail: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    custom_rp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    family_tree_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    titles_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    title_price: Mapped[int] = mapped_column(BigInteger, nullable=False, default=50000, server_default="50000")
    craft_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    auctions_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    auction_duration_minutes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=10, server_default="10")
    auction_min_increment: Mapped[int] = mapped_column(BigInteger, nullable=False, default=100, server_default="100")
    economy_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    economy_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="global", server_default="global")
    economy_tap_cooldown_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=45, server_default="45")
    economy_daily_base_reward: Mapped[int] = mapped_column(BigInteger, nullable=False, default=120, server_default="120")
    economy_daily_streak_cap: Mapped[int] = mapped_column(BigInteger, nullable=False, default=7, server_default="7")
    economy_lottery_ticket_price: Mapped[int] = mapped_column(BigInteger, nullable=False, default=150, server_default="150")
    economy_lottery_paid_daily_limit: Mapped[int] = mapped_column(BigInteger, nullable=False, default=10, server_default="10")
    economy_transfer_daily_limit: Mapped[int] = mapped_column(BigInteger, nullable=False, default=5000, server_default="5000")
    economy_transfer_tax_percent: Mapped[int] = mapped_column(BigInteger, nullable=False, default=5, server_default="5")
    economy_market_fee_percent: Mapped[int] = mapped_column(BigInteger, nullable=False, default=2, server_default="2")
    economy_negative_event_chance_percent: Mapped[int] = mapped_column(BigInteger, nullable=False, default=22, server_default="22")
    economy_negative_event_loss_percent: Mapped[int] = mapped_column(BigInteger, nullable=False, default=30, server_default="30")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ChatTriggerModel(Base):
    __tablename__ = "chat_triggers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    keyword_norm: Mapped[str] = mapped_column(String(255), nullable=False)
    match_type: Mapped[str] = mapped_column(String(32), nullable=False, default="contains", server_default="contains")
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "match_type IN ('exact', 'contains', 'starts_with')",
            name="ck_chat_triggers_match_type",
        ),
    )


Index("idx_chat_triggers_chat_match", ChatTriggerModel.chat_id, ChatTriggerModel.match_type)
Index("uq_chat_triggers_chat_keyword_match", ChatTriggerModel.chat_id, ChatTriggerModel.keyword_norm, ChatTriggerModel.match_type, unique=True)


class ChatCustomSocialActionModel(Base):
    __tablename__ = "chat_custom_social_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    trigger_text: Mapped[str] = mapped_column(String(128), nullable=False)
    trigger_text_norm: Mapped[str] = mapped_column(String(128), nullable=False)
    response_template: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index("uq_chat_custom_social_actions_chat_trigger", ChatCustomSocialActionModel.chat_id, ChatCustomSocialActionModel.trigger_text_norm, unique=True)


class RelationshipGraphModel(Base):
    __tablename__ = "relationships_graph"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_a: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_b: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "relation_type IN ('spouse', 'parent', 'child', 'pet')",
            name="ck_relationships_graph_type",
        ),
        CheckConstraint("user_a != user_b", name="ck_relationships_graph_distinct_users"),
    )


Index("idx_relationships_graph_chat_a", RelationshipGraphModel.chat_id, RelationshipGraphModel.user_a)
Index("idx_relationships_graph_chat_b", RelationshipGraphModel.chat_id, RelationshipGraphModel.user_b)
Index(
    "uq_relationships_graph_chat_relation_pair",
    RelationshipGraphModel.chat_id,
    RelationshipGraphModel.user_a,
    RelationshipGraphModel.user_b,
    RelationshipGraphModel.relation_type,
    unique=True,
)


class ChatAuditLogModel(Base):
    __tablename__ = "chat_audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    target_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    action_code: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    meta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


Index("idx_chat_audit_logs_chat_created", ChatAuditLogModel.chat_id, ChatAuditLogModel.created_at)
Index("idx_chat_audit_logs_chat_action_created", ChatAuditLogModel.chat_id, ChatAuditLogModel.action_code, ChatAuditLogModel.created_at)


class ChatTextAliasSettingsModel(Base):
    __tablename__ = "chat_text_alias_settings"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="both", server_default="both")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "mode IN ('aliases_if_exists', 'both', 'standard_only')",
            name="ck_chat_text_alias_settings_mode",
        ),
    )


class ChatTextAliasModel(Base):
    __tablename__ = "chat_text_aliases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    command_key: Mapped[str] = mapped_column(String(64), nullable=False)
    alias_text_norm: Mapped[str] = mapped_column(String(128), nullable=False)
    source_trigger_norm: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index("uq_chat_text_aliases_chat_alias", ChatTextAliasModel.chat_id, ChatTextAliasModel.alias_text_norm, unique=True)
Index("idx_chat_text_aliases_chat_command", ChatTextAliasModel.chat_id, ChatTextAliasModel.command_key)


class InlinePrivateMessageModel(Base):
    __tablename__ = "inline_private_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chat_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="SET NULL"),
        nullable=True,
    )
    chat_instance: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    receiver_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    receiver_usernames: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


Index("idx_inline_private_messages_sender_created", InlinePrivateMessageModel.sender_id, InlinePrivateMessageModel.created_at)
Index("idx_inline_private_messages_chat_instance", InlinePrivateMessageModel.chat_instance)


class EconomyAccountModel(Base):
    __tablename__ = "economy_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    tap_streak: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    last_tap_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    daily_streak: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    last_daily_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    free_lottery_claimed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_lottery_used_today: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    paid_lottery_used_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    sprinkler_level: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    tap_glove_level: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    storage_level: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    growth_size_mm: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    growth_stress_pct: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    growth_actions: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    last_growth_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    growth_boost_pct: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    growth_cooldown_discount_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint("scope_type IN ('global', 'chat')", name="ck_economy_accounts_scope_type"),
        CheckConstraint("balance >= 0", name="ck_economy_accounts_balance_non_negative"),
        CheckConstraint("growth_size_mm >= 0", name="ck_economy_accounts_growth_size_min"),
        CheckConstraint("growth_stress_pct >= 0 AND growth_stress_pct <= 100", name="ck_economy_accounts_growth_stress_range"),
        CheckConstraint("growth_actions >= 0", name="ck_economy_accounts_growth_actions_non_negative"),
        CheckConstraint("growth_boost_pct >= 0", name="ck_economy_accounts_growth_boost_non_negative"),
        CheckConstraint(
            "growth_cooldown_discount_seconds >= 0",
            name="ck_economy_accounts_growth_cd_discount_non_negative",
        ),
    )


Index("uq_economy_accounts_scope_user", EconomyAccountModel.scope_id, EconomyAccountModel.user_id, unique=True)
Index("idx_economy_accounts_scope", EconomyAccountModel.scope_id)
Index("idx_economy_accounts_chat", EconomyAccountModel.chat_id)


class EconomyFarmModel(Base):
    __tablename__ = "economy_farms"

    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("economy_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    farm_level: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
    size_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="small", server_default="small")
    negative_event_streak: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint("size_tier IN ('small', 'medium', 'large')", name="ck_economy_farms_size_tier"),
        CheckConstraint("farm_level >= 1 AND farm_level <= 5", name="ck_economy_farms_level_range"),
    )


class EconomyPlotModel(Base):
    __tablename__ = "economy_plots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("economy_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    plot_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    crop_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    planted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    yield_boost_pct: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    shield_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index("uq_economy_plots_account_plot", EconomyPlotModel.account_id, EconomyPlotModel.plot_no, unique=True)
Index("idx_economy_plots_account_ready", EconomyPlotModel.account_id, EconomyPlotModel.ready_at)


class EconomyInventoryModel(Base):
    __tablename__ = "economy_inventory"

    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("economy_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    item_code: Mapped[str] = mapped_column(String(128), primary_key=True)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_economy_inventory_qty_non_negative"),
    )


Index("idx_economy_inventory_account", EconomyInventoryModel.account_id)


class EconomyLedgerModel(Base):
    __tablename__ = "economy_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("economy_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    meta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("direction IN ('in', 'out')", name="ck_economy_ledger_direction"),
        CheckConstraint("amount >= 0", name="ck_economy_ledger_amount_non_negative"),
    )


Index("idx_economy_ledger_account_created", EconomyLedgerModel.account_id, EconomyLedgerModel.created_at)


class EconomyTransferDailyModel(Base):
    __tablename__ = "economy_transfer_daily"

    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("economy_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    limit_date: Mapped[date] = mapped_column(Date, primary_key=True)
    sent_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    sent_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class EconomyMarketListingModel(Base):
    __tablename__ = "economy_market_listings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=True,
    )
    seller_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    item_code: Mapped[str] = mapped_column(String(128), nullable=False)
    qty_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    qty_left: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unit_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fee_paid: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", server_default="open")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint("scope_type IN ('global', 'chat')", name="ck_economy_market_scope_type"),
        CheckConstraint("status IN ('open', 'closed', 'cancelled', 'expired')", name="ck_economy_market_status"),
        CheckConstraint("qty_total > 0", name="ck_economy_market_qty_total_positive"),
        CheckConstraint("qty_left >= 0", name="ck_economy_market_qty_left_non_negative"),
        CheckConstraint("unit_price > 0", name="ck_economy_market_unit_price_positive"),
    )


Index("idx_economy_market_scope_status_created", EconomyMarketListingModel.scope_id, EconomyMarketListingModel.status, EconomyMarketListingModel.created_at)
Index("idx_economy_market_seller_status", EconomyMarketListingModel.seller_user_id, EconomyMarketListingModel.status)


class EconomyPrivateContextModel(Base):
    __tablename__ = "economy_private_contexts"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ChatAuctionModel(Base):
    __tablename__ = "chat_auctions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    seller_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    item_code: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    start_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_bid: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    highest_bid_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    min_increment: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", server_default="open")
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("scope_type IN ('global', 'chat')", name="ck_chat_auctions_scope_type"),
        CheckConstraint("quantity > 0", name="ck_chat_auctions_qty_positive"),
        CheckConstraint("start_price > 0", name="ck_chat_auctions_start_price_positive"),
        CheckConstraint("current_bid >= 0", name="ck_chat_auctions_current_bid_non_negative"),
        CheckConstraint("min_increment > 0", name="ck_chat_auctions_min_increment_positive"),
        CheckConstraint("status IN ('open', 'closed', 'cancelled')", name="ck_chat_auctions_status"),
    )


Index("idx_chat_auctions_chat_status_created", ChatAuctionModel.chat_id, ChatAuctionModel.status, ChatAuctionModel.created_at)
Index("idx_chat_auctions_chat_status_ends", ChatAuctionModel.chat_id, ChatAuctionModel.status, ChatAuctionModel.ends_at)


class WebLoginCodeModel(Base):
    __tablename__ = "web_login_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    code_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


Index("idx_web_login_codes_digest", WebLoginCodeModel.code_digest, WebLoginCodeModel.expires_at)
Index("idx_web_login_codes_user_created", WebLoginCodeModel.user_id, WebLoginCodeModel.created_at)


class WebSessionModel(Base):
    __tablename__ = "web_sessions"

    session_digest: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


Index("idx_web_sessions_user_created", WebSessionModel.user_id, WebSessionModel.created_at)
Index("idx_web_sessions_expires", WebSessionModel.expires_at, WebSessionModel.revoked_at)
