from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from selara.infrastructure.db.base import Base

_AUTOINCREMENT_PK = BigInteger().with_variant(Integer, "sqlite")


class UserModel(Base):
    __tablename__ = "users"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_bot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    subscription_exempt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    gacha_animation_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
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
    is_bot_member: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ChatEntitlementModel(Base):
    """Current time-bounded product access for one logical Telegram chat."""

    __tablename__ = "chat_entitlements"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("chat_id", "product_key", name="uq_chat_entitlements_chat_product"),
        CheckConstraint("status IN ('active', 'revoked')", name="ck_chat_entitlements_status"),
        CheckConstraint("valid_from < valid_until", name="ck_chat_entitlements_validity"),
        CheckConstraint("product_key IN ('selara_ai_monthly')", name="ck_chat_entitlements_product"),
        Index("idx_chat_entitlements_active_until", "product_key", "status", "valid_until"),
    )


class UserEntitlementModel(Base):
    """Current time-bounded personal product access for one Telegram user.

    Kept apart from ``chat_entitlements`` on purpose: that table carries the
    group -> supergroup merge rules and chat-keyed advisory locks, and its
    product CHECK must keep refusing personal products.
    """

    __tablename__ = "user_entitlements"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="CASCADE"), nullable=False
    )
    product_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Daily limit sold with the subscription (snapshot taken at purchase); NULL = use the configured one.
    paid_daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "product_key", name="uq_user_entitlements_user_product"),
        CheckConstraint("paid_daily_limit IS NULL OR paid_daily_limit > 0", name="ck_user_entitlements_paid_limit"),
        CheckConstraint("status IN ('active', 'revoked')", name="ck_user_entitlements_status"),
        CheckConstraint("valid_from < valid_until", name="ck_user_entitlements_validity"),
        CheckConstraint("product_key IN ('selara_personal_monthly')", name="ck_user_entitlements_product"),
        Index("idx_user_entitlements_active_until", "product_key", "status", "valid_until"),
    )


class SelaraAiPurchaseIntentModel(Base):
    """Server-side invoice context; the payload itself contains only its UUID."""

    __tablename__ = "selara_ai_purchase_intents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    buyer_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Buyer and recipient are stored separately so gifts need no schema change;
    # until then ``ck_..._personal_self_only`` pins the recipient to the buyer.
    target_scope: Mapped[str] = mapped_column(String(8), nullable=False, default="chat", server_default="chat")
    target_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Quota terms sold with the invoice; copied onto the user entitlement when the payment is applied.
    paid_daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chat_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_key: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_stars: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    invoice_payload: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open", server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    invoice_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terms_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pre_checkout_query_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pre_checkout_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "product_key IN ('selara_ai_monthly', 'selara_personal_monthly')",
            name="ck_selara_ai_purchase_intents_product",
        ),
        CheckConstraint("target_scope IN ('chat', 'user')", name="ck_selara_ai_purchase_intents_target_scope"),
        CheckConstraint(
            "(target_scope = 'chat' AND chat_id IS NOT NULL AND target_user_id IS NULL) OR "
            "(target_scope = 'user' AND target_user_id IS NOT NULL AND chat_id IS NULL)",
            name="ck_selara_ai_purchase_intents_target_shape",
        ),
        CheckConstraint(
            "(target_scope = 'chat' AND product_key = 'selara_ai_monthly') OR "
            "(target_scope = 'user' AND product_key = 'selara_personal_monthly')",
            name="ck_selara_ai_purchase_intents_scope_product",
        ),
        CheckConstraint(
            "target_scope <> 'user' OR target_user_id = buyer_user_id",
            name="ck_selara_ai_purchase_intents_personal_self_only",
        ),
        CheckConstraint(
            "paid_daily_limit IS NULL OR paid_daily_limit > 0", name="ck_selara_ai_purchase_intents_paid_limit"
        ),
        CheckConstraint("amount_stars > 0", name="ck_selara_ai_purchase_intents_amount"),
        CheckConstraint("duration_seconds > 0", name="ck_selara_ai_purchase_intents_duration"),
        CheckConstraint("status IN ('open', 'checkout_accepted', 'consumed')", name="ck_selara_ai_purchase_intents_status"),
        CheckConstraint("currency = 'XTR'", name="ck_selara_ai_purchase_intents_currency"),
        CheckConstraint(
            "(terms_version IS NULL AND terms_accepted_at IS NULL) OR "
            "(terms_version IS NOT NULL AND terms_accepted_at IS NOT NULL)",
            name="ck_selara_ai_purchase_intents_terms_acceptance",
        ),
        Index("idx_selara_ai_purchase_intents_buyer_created", "buyer_user_id", "created_at"),
        Index("idx_selara_ai_purchase_intents_chat", "chat_id", "status"),
        Index("idx_selara_ai_purchase_intents_target_user", "target_user_id", "status"),
    )


class SelaraAiPaymentModel(Base):
    """Immutable Telegram payment audit, including rejected/mismatched updates."""

    __tablename__ = "selara_ai_payments"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    telegram_payment_charge_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    provider_payment_charge_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoice_payload: Mapped[str] = mapped_column(Text, nullable=False)
    purchase_intent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("selara_ai_purchase_intents.id", ondelete="SET NULL"), nullable=True
    )
    buyer_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_scope: Mapped[str] = mapped_column(String(8), nullable=False, default="chat", server_default="chat")
    target_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    product_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount_stars: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processing_state: Mapped[str] = mapped_column(String(16), nullable=False)
    processing_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("amount_stars >= 0", name="ck_selara_ai_payments_nonnegative_amount"),
        CheckConstraint("processing_state IN ('applied', 'rejected')", name="ck_selara_ai_payments_state"),
        CheckConstraint(
            "product_key IS NULL OR product_key IN ('selara_ai_monthly', 'selara_personal_monthly')",
            name="ck_selara_ai_payments_product",
        ),
        CheckConstraint("target_scope IN ('chat', 'user')", name="ck_selara_ai_payments_target_scope"),
        CheckConstraint(
            "(target_scope = 'chat' AND target_user_id IS NULL) OR "
            "(target_scope = 'user' AND target_user_id IS NOT NULL AND target_chat_id IS NULL)",
            name="ck_selara_ai_payments_target_shape",
        ),
        CheckConstraint(
            "product_key IS NULL OR "
            "(target_scope = 'chat' AND product_key = 'selara_ai_monthly') OR "
            "(target_scope = 'user' AND product_key = 'selara_personal_monthly')",
            name="ck_selara_ai_payments_scope_product",
        ),
        Index("idx_selara_ai_payments_target_time", "target_chat_id", "payment_at"),
        Index("idx_selara_ai_payments_target_user_time", "target_user_id", "payment_at"),
        Index("idx_selara_ai_payments_buyer_time", "buyer_user_id", "payment_at"),
        Index("idx_selara_ai_payments_state_time", "processing_state", "payment_at"),
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
    is_active_member: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    display_name_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_prefix: Mapped[str | None] = mapped_column(String(96), nullable=True)
    persona_label: Mapped[str | None] = mapped_column(String(96), nullable=True)
    persona_label_norm: Mapped[str | None] = mapped_column(String(96), nullable=True)
    persona_granted_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    persona_granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index("idx_user_chat_activity_chat_count", UserChatActivityModel.chat_id, UserChatActivityModel.message_count)
Index("idx_user_chat_activity_chat_last_seen", UserChatActivityModel.chat_id, UserChatActivityModel.last_seen_at)
Index("idx_user_chat_activity_chat_active", UserChatActivityModel.chat_id, UserChatActivityModel.is_active_member)
Index("uq_user_chat_activity_chat_persona_norm", UserChatActivityModel.chat_id, UserChatActivityModel.persona_label_norm, unique=True)


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
    avatar_frame_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    emoji_status_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserChatAwardModel(Base):
    __tablename__ = "user_chat_awards"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
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


class UserChatIrisImportStateModel(Base):
    __tablename__ = "user_chat_iris_import_state"

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
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    imported_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    source_bot_username: Mapped[str] = mapped_column(String(64), nullable=False)
    source_target_username: Mapped[str] = mapped_column(String(255), nullable=False)
    karma_base_all_time: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")


class UserChatIrisImportHistoryModel(Base):
    __tablename__ = "user_chat_iris_import_history"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
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
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    imported_snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_profile_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_awards_text: Mapped[str] = mapped_column(Text, nullable=False)


Index(
    "idx_user_chat_iris_import_state_chat_user",
    UserChatIrisImportStateModel.chat_id,
    UserChatIrisImportStateModel.user_id,
)
Index(
    "idx_user_chat_iris_import_history_chat_user_imported",
    UserChatIrisImportHistoryModel.chat_id,
    UserChatIrisImportHistoryModel.user_id,
    UserChatIrisImportHistoryModel.imported_at,
)


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


class UserChatMessageEventModel(Base):
    __tablename__ = "user_chat_message_events"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
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
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    source_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_bucket_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("chat_id", "telegram_message_id", name="uq_user_chat_message_events_chat_message"),
        UniqueConstraint(
            "chat_id",
            "user_id",
            "source_kind",
            "source_bucket_at",
            "source_seq",
            name="uq_user_chat_message_events_synthetic_source",
        ),
    )


class MessageArchiveModel(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
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
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_message_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Daily summary feature fields (see docs/DAILY_SUMMARY_TODO.md). transcribed_at is
    # tracked separately from snapshot_at because a transcript can be produced later
    # than the message itself -- TTL cleanup keys off transcribed_at, not snapshot_at.
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reply_to_telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        CheckConstraint("snapshot_kind IN ('created', 'edited')", name="ck_messages_snapshot_kind"),
        UniqueConstraint("chat_id", "telegram_message_id", "snapshot_hash", name="uq_messages_chat_message_snapshot"),
        Index("idx_messages_chat_reply_to", "chat_id", "reply_to_telegram_message_id"),
    )


class ChatActivityEventSyncStateModel(Base):
    __tablename__ = "chat_activity_event_sync_state"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    legacy_total_messages: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    event_total_messages: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ChatInterestingFactStateModel(Base):
    __tablename__ = "chat_interesting_fact_state"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_fact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    used_fact_ids_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserKarmaVoteModel(Base):
    __tablename__ = "user_karma_votes"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
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
Index("idx_user_chat_message_events_chat_sent", UserChatMessageEventModel.chat_id, UserChatMessageEventModel.sent_at)
Index("idx_user_chat_message_events_chat_user_sent", UserChatMessageEventModel.chat_id, UserChatMessageEventModel.user_id, UserChatMessageEventModel.sent_at)
Index("idx_messages_chat_snapshot", MessageArchiveModel.chat_id, MessageArchiveModel.snapshot_at)
Index("idx_messages_chat_user_snapshot", MessageArchiveModel.chat_id, MessageArchiveModel.user_id, MessageArchiveModel.snapshot_at)
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


class UserChatRestStateModel(Base):
    __tablename__ = "user_chat_rest_states"

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
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    granted_by_user_id: Mapped[int | None] = mapped_column(
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


Index("idx_user_chat_rest_states_chat_expires", UserChatRestStateModel.chat_id, UserChatRestStateModel.expires_at)


class RelationshipProposalModel(Base):
    __tablename__ = "relationship_proposals"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
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

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
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

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
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
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    married_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    ended_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    affection_points: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    last_affection_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_affection_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    last_milestone_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
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


Index(
    "uq_marriages_chat_pair",
    MarriageModel.chat_id,
    MarriageModel.user_low_id,
    MarriageModel.user_high_id,
    unique=True,
    postgresql_where=text("is_active"),
    sqlite_where=text("is_active = 1"),
)
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
    iris_view: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
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
    cleanup_leave_service_messages: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    entry_captcha_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    entry_captcha_timeout_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=180, server_default="180")
    entry_captcha_kick_on_fail: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    antiraid_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    antiraid_recent_window_minutes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=10, server_default="10")
    chat_write_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    interesting_facts_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    interesting_facts_interval_minutes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=180, server_default="180")
    interesting_facts_target_messages: Mapped[int] = mapped_column(BigInteger, nullable=False, default=150, server_default="150")
    interesting_facts_sleep_cap_minutes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1440, server_default="1440")
    custom_rp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    family_tree_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # Permission for AI pets to live in this chat; not a subscription.
    pets_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Lets pets post rare spontaneous lines here; paid by each pet's owner, off by default.
    pets_spontaneous_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    persona_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    persona_display_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="image_name", server_default="image_name")
    save_message: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
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
    cleanup_economy_commands: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    gacha_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    gacha_restore_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    llm_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    llm_context_threshold: Mapped[int] = mapped_column(BigInteger, nullable=False, default=30, server_default="30")
    daily_summary_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    daily_summary_hour: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3, server_default="3")
    daily_summary_min_messages: Mapped[int] = mapped_column(BigInteger, nullable=False, default=50, server_default="50")
    daily_summary_style: Mapped[str] = mapped_column(String(16), nullable=False, default="neutral", server_default="neutral")
    daily_summary_include_voice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    daily_summary_include_video_notes: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ChatTriggerModel(Base):
    __tablename__ = "chat_triggers"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
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

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
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
    child_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
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


class FamilyRelationshipArchiveModel(Base):
    __tablename__ = "family_relationship_archive"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    original_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_a: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_b: Mapped[int] = mapped_column(BigInteger, nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    child_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    archive_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


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

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
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
    last_planted_crop_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
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


class EconomyMarketTradeModel(Base):
    __tablename__ = "economy_market_trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("economy_market_listings.id", ondelete="CASCADE"),
        nullable=False,
    )
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
    buyer_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    item_code: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unit_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("scope_type IN ('global', 'chat')", name="ck_economy_market_trades_scope_type"),
        CheckConstraint("quantity > 0", name="ck_economy_market_trades_qty_positive"),
        CheckConstraint("unit_price > 0", name="ck_economy_market_trades_unit_price_positive"),
        CheckConstraint("total_price > 0", name="ck_economy_market_trades_total_price_positive"),
    )


Index("idx_economy_market_trades_scope_created", EconomyMarketTradeModel.scope_id, EconomyMarketTradeModel.created_at)
Index("idx_economy_market_trades_item_created", EconomyMarketTradeModel.item_code, EconomyMarketTradeModel.created_at)
Index("idx_economy_market_trades_buyer_created", EconomyMarketTradeModel.buyer_user_id, EconomyMarketTradeModel.created_at)


class ChatGlobalBoostModel(Base):
    __tablename__ = "chat_global_boosts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    boost_code: Mapped[str] = mapped_column(String(64), nullable=False)
    value_percent: Mapped[int] = mapped_column(BigInteger, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("scope_type IN ('global', 'chat')", name="ck_chat_global_boosts_scope_type"),
        CheckConstraint("value_percent > 0", name="ck_chat_global_boosts_value_positive"),
        CheckConstraint("ends_at > starts_at", name="ck_chat_global_boosts_interval"),
    )


Index("idx_chat_global_boosts_chat_ends", ChatGlobalBoostModel.chat_id, ChatGlobalBoostModel.ends_at)
Index("idx_chat_global_boosts_scope_ends", ChatGlobalBoostModel.scope_id, ChatGlobalBoostModel.ends_at)


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


class AdminSessionModel(Base):
    __tablename__ = "admin_sessions"

    session_token: Mapped[str] = mapped_column(String(128), primary_key=True)
    admin_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


Index("idx_admin_sessions_user_created", AdminSessionModel.admin_user_id, AdminSessionModel.created_at)
Index("idx_admin_sessions_expires", AdminSessionModel.expires_at, AdminSessionModel.revoked_at)


class AdminBroadcastModel(Base):
    __tablename__ = "admin_broadcasts"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    created_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active_since_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3, server_default="3")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    rendered_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    reaction_options_json: Mapped[list[dict[str, str]] | None] = mapped_column(JSON, nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    media_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_file_unique_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


Index("idx_admin_broadcasts_created", AdminBroadcastModel.created_at)


class AdminBroadcastDeliveryModel(Base):
    __tablename__ = "admin_broadcast_deliveries"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    broadcast_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("admin_broadcasts.id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_title_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reaction_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="none", server_default="none")
    bot_member_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_admin_broadcast_deliveries_status"),
        CheckConstraint(
            "reaction_mode IN ('none', 'native', 'inline')",
            name="ck_admin_broadcast_deliveries_reaction_mode",
        ),
        UniqueConstraint("broadcast_id", "chat_id", name="uq_admin_broadcast_deliveries_broadcast_chat"),
    )


Index("idx_admin_broadcast_deliveries_broadcast_status", AdminBroadcastDeliveryModel.broadcast_id, AdminBroadcastDeliveryModel.status)
Index("idx_admin_broadcast_deliveries_chat_message", AdminBroadcastDeliveryModel.chat_id, AdminBroadcastDeliveryModel.telegram_message_id)


class AdminBroadcastReplyModel(Base):
    __tablename__ = "admin_broadcast_replies"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    delivery_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("admin_broadcast_deliveries.id", ondelete="CASCADE"),
        nullable=False,
    )
    reply_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_message_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bot_reaction_emoji: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bot_reaction_updated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    bot_reaction_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("delivery_id", "telegram_message_id", name="uq_admin_broadcast_replies_delivery_message"),
    )


Index("idx_admin_broadcast_replies_delivery_sent", AdminBroadcastReplyModel.delivery_id, AdminBroadcastReplyModel.sent_at)
Index("idx_admin_broadcast_replies_user_sent", AdminBroadcastReplyModel.reply_user_id, AdminBroadcastReplyModel.sent_at)


class AdminBroadcastReactionModel(Base):
    __tablename__ = "admin_broadcast_reactions"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    delivery_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("admin_broadcast_deliveries.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    option_key: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reaction_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="emoji",
        server_default="emoji",
    )
    reaction_value: Mapped[str] = mapped_column(String(128), nullable=False)
    emoji: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=True,
    )
    actor_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    actor_key: Mapped[str] = mapped_column(String(80), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    reacted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint("source IN ('native', 'inline')", name="ck_admin_broadcast_reactions_source"),
        CheckConstraint(
            "reaction_type IN ('emoji', 'custom_emoji', 'paid')",
            name="ck_admin_broadcast_reactions_type",
        ),
        CheckConstraint(
            "actor_user_id IS NOT NULL OR actor_chat_id IS NOT NULL",
            name="ck_admin_broadcast_reactions_actor",
        ),
        UniqueConstraint(
            "delivery_id",
            "source",
            "actor_key",
            "reaction_type",
            "reaction_value",
            name="uq_admin_broadcast_reaction_actor_value",
        ),
    )


Index("idx_admin_broadcast_reactions_delivery_active", AdminBroadcastReactionModel.delivery_id, AdminBroadcastReactionModel.active)
Index("idx_admin_broadcast_reactions_user", AdminBroadcastReactionModel.actor_user_id, AdminBroadcastReactionModel.updated_at)


class AdminBroadcastReactionCountModel(Base):
    __tablename__ = "admin_broadcast_reaction_counts"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    delivery_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("admin_broadcast_deliveries.id", ondelete="CASCADE"),
        nullable=False,
    )
    reaction_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="emoji",
        server_default="emoji",
    )
    reaction_value: Mapped[str] = mapped_column(String(128), nullable=False)
    emoji: Mapped[str] = mapped_column(String(64), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("count >= 0", name="ck_admin_broadcast_reaction_counts_nonnegative"),
        CheckConstraint(
            "reaction_type IN ('emoji', 'custom_emoji', 'paid')",
            name="ck_admin_broadcast_reaction_counts_type",
        ),
        UniqueConstraint(
            "delivery_id",
            "reaction_type",
            "reaction_value",
            name="uq_admin_broadcast_reaction_count_delivery_value",
        ),
    )


class UserFeatureRequestModel(Base):
    __tablename__ = "user_feature_requests"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", server_default="open")
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint("status IN ('open', 'done')", name="ck_user_feature_requests_status"),
    )


Index("idx_user_feature_requests_user_created", UserFeatureRequestModel.user_id, UserFeatureRequestModel.created_at)
Index("idx_user_feature_requests_status_created", UserFeatureRequestModel.status, UserFeatureRequestModel.created_at)


class AdminRuntimeSettingsModel(Base):
    """Small set of system-wide settings that admins can change at runtime."""

    __tablename__ = "admin_runtime_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    error_alert_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_alerts_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SelaraPersonalConfigModel(Base):
    """Singleton row of Selara Personal overrides edited at runtime; NULL falls back to .env."""

    __tablename__ = "selara_personal_config"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_selara_personal_config_singleton"),
        CheckConstraint("price_stars IS NULL OR price_stars > 0", name="ck_selara_personal_config_price"),
        CheckConstraint("duration_days IS NULL OR duration_days > 0", name="ck_selara_personal_config_duration"),
        CheckConstraint("free_daily_limit IS NULL OR free_daily_limit > 0", name="ck_selara_personal_config_free"),
        CheckConstraint("paid_daily_limit IS NULL OR paid_daily_limit > 0", name="ck_selara_personal_config_paid"),
        CheckConstraint(
            "memory_free_limit IS NULL OR memory_free_limit > 0", name="ck_selara_personal_config_memory_free"
        ),
        CheckConstraint(
            "memory_paid_limit IS NULL OR memory_paid_limit > 0", name="ck_selara_personal_config_memory_paid"
        ),
        CheckConstraint(
            "memory_extract_every IS NULL OR memory_extract_every BETWEEN 2 AND 40",
            name="ck_selara_personal_config_memory_every",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    price_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    free_daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paid_daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_free_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_paid_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_auto_extract: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    memory_extract_every: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OperationalAlertModel(Base):
    __tablename__ = "operational_alerts"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(160), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sanitized_traceback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


Index("idx_operational_alerts_created", OperationalAlertModel.created_at)
Index("idx_operational_alerts_fingerprint", OperationalAlertModel.fingerprint)


class UserChatAchievementModel(Base):
    __tablename__ = "user_chat_achievement"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
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
    achievement_id: Mapped[str] = mapped_column(String(128), nullable=False)
    awarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    award_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", "achievement_id", name="uq_user_chat_achievement"),
    )


Index("idx_user_chat_achievement_chat_user", UserChatAchievementModel.chat_id, UserChatAchievementModel.user_id)
Index("idx_user_chat_achievement_chat_achievement", UserChatAchievementModel.chat_id, UserChatAchievementModel.achievement_id)
Index("idx_user_chat_achievement_user", UserChatAchievementModel.user_id)
Index("idx_user_chat_achievement_achievement", UserChatAchievementModel.achievement_id)


class UserGlobalAchievementModel(Base):
    __tablename__ = "user_global_achievement"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    achievement_id: Mapped[str] = mapped_column(String(128), nullable=False)
    awarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    award_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "achievement_id", name="uq_user_global_achievement"),
    )


Index("idx_user_global_achievement_user", UserGlobalAchievementModel.user_id)
Index("idx_user_global_achievement_achievement", UserGlobalAchievementModel.achievement_id)


class ChatAchievementStatsModel(Base):
    __tablename__ = "chat_achievement_stats"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    achievement_id: Mapped[str] = mapped_column(String(128), nullable=False)
    holders_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    holders_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0, server_default="0")
    active_members_base_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("chat_id", "achievement_id", name="uq_chat_achievement_stats"),
    )


Index("idx_chat_achievement_stats_chat", ChatAchievementStatsModel.chat_id)
Index("idx_chat_achievement_stats_chat_achievement", ChatAchievementStatsModel.chat_id, ChatAchievementStatsModel.achievement_id)


class GlobalAchievementStatsModel(Base):
    __tablename__ = "global_achievement_stats"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    achievement_id: Mapped[str] = mapped_column(String(128), nullable=False)
    holders_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    holders_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0, server_default="0")
    global_base_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("achievement_id", name="uq_global_achievement_stats"),
    )


Index("idx_global_achievement_stats_achievement", GlobalAchievementStatsModel.achievement_id)


class ChatMetricsModel(Base):
    __tablename__ = "chat_metrics"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    active_members_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ChatMemberCountSnapshotModel(Base):
    __tablename__ = "chat_member_count_snapshots"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    member_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GlobalMetricsModel(Base):
    __tablename__ = "global_metrics"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1, server_default="1")
    global_users_base_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ClanModel(Base):
    __tablename__ = "clans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    creator_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("chat_id", "name", name="uq_clan_chat_name"),)


class ClanMemberModel(Base):
    __tablename__ = "clan_members"

    clan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clans.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DisabledRpActionModel(Base):
    __tablename__ = "disabled_rp_actions"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        primary_key=True,
    )
    action_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LlmChatGlossaryModel(Base):
    __tablename__ = "llm_chat_glossary"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    term: Mapped[str] = mapped_column(String(256), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list["LlmChatGlossaryAliasModel"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan",
    )
    # #17: author tracking -- who added/last edited this entry, relevant
    # for tracing back a glossary-poisoning incident (#2).
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="SET NULL"), nullable=True,
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("chat_id", "term", name="uq_llm_glossary_chat_term"),
        Index("idx_llm_glossary_chat", "chat_id"),
    )


class LlmChatGlossaryAliasModel(Base):
    __tablename__ = "llm_chat_glossary_aliases"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    glossary_id: Mapped[int] = mapped_column(
        ForeignKey("llm_chat_glossary.id", ondelete="CASCADE"), nullable=False,
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False,
    )
    alias: Mapped[str] = mapped_column(String(256), nullable=False)

    __table_args__ = (
        UniqueConstraint("chat_id", "alias", name="uq_llm_glossary_chat_alias"),
        Index("idx_llm_glossary_alias_entry", "glossary_id"),
    )


class LlmChatGlossaryHistoryModel(Base):
    """#18: records the definition being replaced before each overwrite, so
    a poisoned or otherwise-bad edit can be inspected/recovered rather than
    silently lost."""
    __tablename__ = "llm_chat_glossary_history"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    term: Mapped[str] = mapped_column(String(256), nullable=False)
    previous_definition: Mapped[str] = mapped_column(Text, nullable=False)
    previous_aliases: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    changed_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="SET NULL"), nullable=True,
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_llm_glossary_history_chat_term", "chat_id", "term"),
    )


class LlmContextMessageModel(Base):
    __tablename__ = "llm_context_messages"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    admin_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    compressed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_context: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'tool')", name="ck_llm_context_messages_role"),
        Index("idx_llm_ctx_msgs_chat_created", "chat_id", "created_at"),
        Index("idx_llm_ctx_msgs_chat_compressed", "chat_id", "compressed"),
        Index("idx_llm_ctx_msgs_chat_is_context_compressed", "chat_id", "is_context", "compressed"),
    )


class LlmContextSummaryModel(Base):
    __tablename__ = "llm_context_summaries"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    messages_count: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_llm_ctx_summaries_chat_period", "chat_id", "period_end"),
        Index("idx_llm_ctx_summaries_chat_level", "chat_id", "level"),
    )


class LlmAdminActionModel(Base):
    __tablename__ = "llm_admin_actions"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
        nullable=False,
    )
    admin_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    action_description: Mapped[str] = mapped_column(Text, nullable=False)
    undo_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index("idx_llm_admin_actions_chat_created", "chat_id", "created_at"),
        Index("idx_llm_admin_actions_chat_admin", "chat_id", "admin_user_id"),
    )


class GachaAnimationVariantModel(Base):
    """Cached reel-animation clips for the animated gacha pull mode (see
    docs/GACHA_MODERNIZATION_TODO.md, Этап 3). Purely a presentation-layer
    cache — lives in the main bot's DB, not the gacha service's own DB, since
    it holds no economy data and carries no risk to gacha history."""

    __tablename__ = "gacha_animation_variants"

    banner: Mapped[str] = mapped_column(String(32), primary_key=True)
    card_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    variant_index: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    telegram_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    cache_version: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DailySummaryRunModel(Base):
    """One "Итоги дня" run — see docs/DAILY_SUMMARY_TODO.md and the approved plan.

    `summary_date` is only the claim/dedup key (one row per chat per calendar day
    per trigger); the actual analysed period is the rolling `window_from..window_to`,
    which for a scheduled run is the PLANNED fire time, not when the scheduler
    actually got to it (so a restart/downtime doesn't shift the window forward).
    `claimed_at`/`lease_until` implement atomic claim + reclaim of a dead run: see
    `status`.
    """

    __tablename__ = "daily_summary_runs"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False
    )
    summary_date: Mapped[date] = mapped_column(Date, nullable=False)
    window_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="claimed", server_default="claimed")
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    topics_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    # Per-run volume/reliability counters (segments, cards, structured-output
    # retries, analyst tool calls/fallbacks, ...) -- see
    # application/daily_summary/pipeline.py's DailySummaryDiagnostics and
    # docs/DAILY_SUMMARY_TODO.md's beta observability wishlist.
    diagnostics_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    pipeline_cost_usd: Mapped[Decimal] = mapped_column(Numeric(20, 9), nullable=False, default=0, server_default="0")
    pipeline_has_unknown_cost: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    context_stt_cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("trigger IN ('scheduled', 'manual')", name="ck_daily_summary_runs_trigger"),
        CheckConstraint(
            "status IN ('claimed', 'generating', 'generated', 'sent', 'send_failed', 'failed')",
            name="ck_daily_summary_runs_status",
        ),
        UniqueConstraint("chat_id", "summary_date", "trigger", name="uq_daily_summary_runs_chat_date_trigger"),
    )


class AiFeatureInvocationModel(Base):
    """One logical Selara AI feature run; provider calls are recorded separately."""

    __tablename__ = "ai_feature_invocations"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False, default="chat", server_default="chat")
    scope_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chat_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chats.telegram_chat_id", ondelete="SET NULL"), nullable=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="SET NULL"), nullable=True
    )
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running", server_default="running")
    source_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provider_attempt_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("daily_summary_runs.id", ondelete="SET NULL"), nullable=True
    )
    error_category: Mapped[str | None] = mapped_column(String(48), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'partial')",
            name="ck_ai_feature_invocations_status",
        ),
        Index("idx_ai_feature_invocations_feature_started", "feature", "started_at"),
        Index("idx_ai_feature_invocations_chat_started", "chat_id", "started_at"),
        Index("idx_ai_feature_invocations_summary_run", "summary_run_id"),
    )


def _quota_scope_id_default(context) -> int | None:
    """Chat-scoped rows default their scope id to ``chat_id`` (mirrors the DB trigger)."""
    params = context.get_current_parameters()
    if params.get("quota_scope_type") in (None, "chat"):
        return params.get("chat_id")
    return None


def _quota_pool_default(context) -> str | None:
    """Without an explicit pool a feature is its own pool."""
    return context.get_current_parameters().get("feature")


class AiFeatureQuotaUsageModel(Base):
    """One logical feature quota reservation, separate from provider-call cost."""

    __tablename__ = "ai_feature_quota_usage"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chats.telegram_chat_id", ondelete="SET NULL"), nullable=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="SET NULL"), nullable=True
    )
    invocation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ai_feature_invocations.id", ondelete="SET NULL"), nullable=True
    )
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    # Immutable Telegram identity at reservation time. `chat_id` tracks the
    # current commercial scope and may change on group -> supergroup migration.
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    policy_key: Mapped[str] = mapped_column(String(64), nullable=False)
    quota_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    access_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_exempt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="consumed", server_default="consumed")
    release_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Who pays for the request: a chat or a user. ``chat_id`` above stays "where it happened".
    # 'legacy_orphan' marks pre-scope rows whose chat was already deleted; they are never counted.
    quota_scope_type: Mapped[str] = mapped_column(String(16), nullable=False, default="chat", server_default="chat")
    quota_scope_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=_quota_scope_id_default)
    # Several features may spend one pool; today a feature is its own pool.
    pool_key: Mapped[str] = mapped_column(String(48), nullable=False, default=_quota_pool_default)
    units: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("1"), server_default="1")

    __table_args__ = (
        CheckConstraint("period_start < period_end", name="ck_ai_feature_quota_period_bounds"),
        CheckConstraint(
            "quota_scope_type IN ('chat', 'user', 'legacy_orphan')", name="ck_ai_feature_quota_scope_type"
        ),
        CheckConstraint(
            "quota_scope_type = 'legacy_orphan' OR quota_scope_id IS NOT NULL",
            name="ck_ai_feature_quota_scope_id",
        ),
        CheckConstraint("units >= 0", name="ck_ai_feature_quota_units"),
        CheckConstraint("quota_limit > 0", name="ck_ai_feature_quota_positive_limit"),
        CheckConstraint("status IN ('consumed', 'released')", name="ck_ai_feature_quota_status"),
        UniqueConstraint("idempotency_key", name="uq_ai_feature_quota_idempotency"),
        UniqueConstraint("invocation_id", name="uq_ai_feature_quota_invocation"),
        Index("idx_ai_feature_quota_period_usage", "feature", "chat_id", "period_start", "status"),
        Index(
            "idx_ai_feature_quota_scope_usage",
            "pool_key",
            "quota_scope_type",
            "quota_scope_id",
            "period_start",
            "status",
        ),
    )


class LlmUsageLogModel(Base):
    """One provider inference or STT attempt, optionally linked to legacy rows."""

    __tablename__ = "llm_usage_log"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    call_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    invocation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ai_feature_invocations.id", ondelete="SET NULL"), nullable=True
    )
    summary_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("daily_summary_runs.id", ondelete="SET NULL"), nullable=True
    )
    message_archive_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    chat_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chats.telegram_chat_id", ondelete="SET NULL"), nullable=True
    )
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    model_profile: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_seconds: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 9), nullable=True)
    pricing_status: Mapped[str] = mapped_column(String(16), nullable=False, default="legacy", server_default="legacy")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="succeeded", server_default="succeeded")
    attempt_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(48), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_llm_usage_log_summary_run", "summary_run_id"),
        Index("idx_llm_usage_log_message_archive", "message_archive_id"),
        Index("idx_llm_usage_log_call_id", "call_id", unique=True),
        Index("idx_llm_usage_log_request_id", "request_id"),
        Index("idx_llm_usage_log_profile_created", "model_profile", "created_at"),
        Index("idx_llm_usage_log_invocation", "invocation_id"),
        Index("idx_llm_usage_log_feature_created", "feature", "created_at"),
        CheckConstraint("pricing_status IN ('known', 'unknown', 'legacy')", name="ck_llm_usage_log_pricing_status"),
        CheckConstraint(
            "status IN ('succeeded', 'failed', 'validation_failed')",
            name="ck_llm_usage_log_status",
        ),
    )


class LlmArtifactModel(Base):
    __tablename__ = "llm_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    creator_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    pages: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    source: Mapped[dict] = mapped_column(JSON, nullable=False)
    delivery: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index("idx_llm_artifacts_chat_expiry", "chat_id", "expires_at"),)


class AutoConfigSessionModel(Base):
    """One private, versioned settings draft per user; never live configuration."""
    __tablename__ = 'autoconfig_sessions'

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('chats.telegram_chat_id', ondelete='CASCADE'), nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidates: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    baseline: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    draft: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    touched: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    history: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_turn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PersonalAiProfileModel(Base):
    """One user's private AI character; owned by the user and removed with them, never by a chat."""

    __tablename__ = "personal_ai_profiles"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="CASCADE"), primary_key=True
    )
    display_name: Mapped[str] = mapped_column(String(32), nullable=False, default="Selara", server_default="Selara")
    character_preset: Mapped[str] = mapped_column(
        String(32), nullable=False, default="assistant", server_default="assistant"
    )
    character_custom: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_form: Mapped[str | None] = mapped_column(String(64), nullable=True)
    formality: Mapped[str] = mapped_column(String(8), nullable=False, default="ty", server_default="ty")
    reply_length: Mapped[str] = mapped_column(String(8), nullable=False, default="medium", server_default="medium")
    emoji_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="assistant", server_default="assistant")
    memory_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # Opt-in for automatic fact extraction (also needs Selara Personal and the global switch).
    auto_memory_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Id of the last user message already looked at by extraction; the next batch starts after it.
    memory_extract_cursor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    # Optimistic lock for the settings wizard (and the future Mini App).
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "character_custom IS NULL OR length(character_custom) <= 500",
            name="ck_personal_ai_profiles_custom_len",
        ),
        CheckConstraint("formality IN ('ty', 'vy')", name="ck_personal_ai_profiles_formality"),
        CheckConstraint("reply_length IN ('short', 'medium', 'long')", name="ck_personal_ai_profiles_reply_length"),
        CheckConstraint("mode IN ('assistant', 'roleplay')", name="ck_personal_ai_profiles_mode"),
    )


class PersonalAiMessageModel(Base):
    """Private dialogue turn. Kept until the user deletes it; there is deliberately no retention job."""

    __tablename__ = "personal_ai_messages"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="CASCADE"), nullable=False
    )
    thread: Mapped[str] = mapped_column(String(16), nullable=False, default="assistant", server_default="assistant")
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    compressed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_personal_ai_messages_role"),
        CheckConstraint("thread IN ('assistant', 'roleplay')", name="ck_personal_ai_messages_thread"),
        Index("idx_personal_ai_messages_user_thread_created", "user_id", "thread", "created_at"),
    )


class PersonalAiSummaryModel(Base):
    """Compressed older part of a private dialogue; the same retention rule as the messages."""

    __tablename__ = "personal_ai_summaries"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="CASCADE"), nullable=False
    )
    thread: Mapped[str] = mapped_column(String(16), nullable=False, default="assistant", server_default="assistant")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    messages_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("thread IN ('assistant', 'roleplay')", name="ck_personal_ai_summaries_thread"),
        Index("idx_personal_ai_summaries_user_thread_period", "user_id", "thread", "period_end"),
    )


class PersonalAiMemoryModel(Base):
    """A fact about the user that their private AI may use. Kept until the user deletes it."""

    __tablename__ = "personal_ai_memories"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(String(300), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("source IN ('explicit', 'extracted')", name="ck_personal_ai_memories_source"),
        CheckConstraint("length(content) BETWEEN 1 AND 300", name="ck_personal_ai_memories_content_len"),
        Index("idx_personal_ai_memories_user_created", "user_id", "created_at"),
    )


# One fact once per user whatever its letter case; the repository also serialises writers per user.
Index(
    "uq_personal_ai_memories_user_content",
    PersonalAiMemoryModel.user_id,
    func.lower(PersonalAiMemoryModel.content),
    unique=True,
)


class LlmModelCatalogModel(Base):
    __tablename__ = "llm_model_catalog"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    prompt_price_usd_per_million: Mapped[Decimal | None] = mapped_column(Numeric(16, 9), nullable=True)
    completion_price_usd_per_million: Mapped[Decimal | None] = mapped_column(Numeric(16, 9), nullable=True)
    supports_tools: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    supports_structured_output: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    supports_vision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )
    __table_args__ = (
        CheckConstraint("length(trim(key)) > 0", name="ck_llm_model_catalog_key"),
        CheckConstraint("length(trim(model_id)) > 0", name="ck_llm_model_catalog_model_id"),
        CheckConstraint("length(trim(display_name)) > 0", name="ck_llm_model_catalog_display_name"),
        CheckConstraint("prompt_price_usd_per_million >= 0 AND prompt_price_usd_per_million <= 1000000",
                        name="ck_llm_model_catalog_prompt_price"),
        CheckConstraint("completion_price_usd_per_million >= 0 AND completion_price_usd_per_million <= 1000000",
                        name="ck_llm_model_catalog_completion_price"),
    )


class LlmModelIdentifierModel(Base):
    """Canonical IDs and aliases share a single unique namespace."""
    __tablename__ = "llm_model_identifiers"

    model_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    model_key: Mapped[str] = mapped_column(
        String(64), ForeignKey("llm_model_catalog.key", ondelete="CASCADE"), nullable=False,
    )
    __table_args__ = (
        Index("idx_llm_model_identifiers_model_key", "model_key"),
        CheckConstraint("length(trim(model_id)) > 0", name="ck_llm_model_identifiers_model_id"),
    )


class LlmModelProfileModel(Base):
    __tablename__ = "llm_model_profiles"

    profile_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_key: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("llm_model_catalog.key", ondelete="SET NULL"), nullable=True,
    )
    ail_multiplier: Mapped[Decimal] = mapped_column(Numeric(13, 9), nullable=False, default=Decimal("1"), server_default="1")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )
    __table_args__ = (
        CheckConstraint("profile_key IN ('basic', 'analytics', 'freeform', 'creative', 'fast')",
                        name="ck_llm_model_profiles_key"),
        CheckConstraint("length(trim(display_name)) > 0", name="ck_llm_model_profiles_display_name"),
        CheckConstraint("ail_multiplier > 0 AND ail_multiplier <= 1000", name="ck_llm_model_profiles_multiplier"),
        Index("idx_llm_model_profiles_model_key", "model_key"),
    )


class AiPetModel(Base):
    """An AI pet owned by a user; it outlives any chat it lives in."""

    __tablename__ = "ai_pets"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="CASCADE"), nullable=False
    )
    home_chat_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chats.telegram_chat_id", ondelete="SET NULL"), nullable=True
    )
    current_chat_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chats.telegram_chat_id", ondelete="SET NULL"), nullable=True
    )
    species_key: Mapped[str] = mapped_column(String(32), nullable=False)
    species_custom: Mapped[str | None] = mapped_column(String(40), nullable=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    name_norm: Mapped[str] = mapped_column(String(32), nullable=False)
    traits: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    character_custom: Mapped[str | None] = mapped_column(String(300), nullable=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    xp: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    mood: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=70, server_default="70")
    satiety: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=70, server_default="70")
    energy: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=70, server_default="70")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    dormant_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    travel_unlocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    last_tick_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("level >= 1", name="ck_ai_pets_level"),
        CheckConstraint("xp >= 0", name="ck_ai_pets_xp"),
        CheckConstraint("mood BETWEEN 0 AND 100", name="ck_ai_pets_mood"),
        CheckConstraint("satiety BETWEEN 0 AND 100", name="ck_ai_pets_satiety"),
        CheckConstraint("energy BETWEEN 0 AND 100", name="ck_ai_pets_energy"),
        CheckConstraint("status IN ('active', 'dormant', 'released')", name="ck_ai_pets_status"),
        Index(
            "uq_ai_pets_owner_alive",
            "owner_user_id",
            unique=True,
            postgresql_where=text("status <> 'released'"),
            sqlite_where=text("status <> 'released'"),
        ),
        Index(
            "uq_ai_pets_chat_name_active",
            "current_chat_id",
            "name_norm",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )


class AiPetRelationshipModel(Base):
    """Affinity between a pet and one person, kept per chat for privacy."""

    __tablename__ = "ai_pet_relationships"

    pet_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ai_pets.id", ondelete="CASCADE"), primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="CASCADE"), primary_key=True
    )
    affinity: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    interactions: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    affinity_gained_today: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    # Raw XP points this person submitted today; the award curve is applied in code.
    xp_gained_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    gained_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("affinity BETWEEN -100 AND 100", name="ck_ai_pet_relationships_affinity"),
    )


class AiPetEventModel(Base):
    """Journal of everything that happened to a pet; also the idempotency guard."""

    __tablename__ = "ai_pet_events"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    pet_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ai_pets.id", ondelete="CASCADE"), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    effects: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_ai_pet_events_idempotency_key"),
        Index("idx_ai_pet_events_pet_created", "pet_id", "created_at"),
        Index("idx_ai_pet_events_pet_actor_type", "pet_id", "actor_user_id", "event_type", "created_at"),
    )


class AiPetItemModel(Base):
    """Owner-editable catalog of pet food and toys; prices live here, not in code."""

    __tablename__ = "ai_pet_items"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # {"satiety": 20, "mood": 5, "affinity": 2}; validated in code, broken rows are not sold.
    effects: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    min_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("kind IN ('food', 'toy')", name="ck_ai_pet_items_kind"),
        CheckConstraint("price >= 0", name="ck_ai_pet_items_price"),
        CheckConstraint("min_level >= 1", name="ck_ai_pet_items_min_level"),
    )


class AiPetMessageModel(Base):
    """A pet's short dialogue history in one chat; also the per-guest talk counter."""

    __tablename__ = "ai_pet_messages"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    pet_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ai_pets.id", ondelete="CASCADE"), nullable=False)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="SET NULL"), nullable=True
    )
    # Whether the author owned the pet when they spoke: guests have their own daily share.
    author_is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok", server_default="ok")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_ai_pet_messages_role"),
        CheckConstraint("status IN ('pending', 'ok', 'failed')", name="ck_ai_pet_messages_status"),
        UniqueConstraint("idempotency_key", name="uq_ai_pet_messages_idempotency_key"),
        Index("idx_ai_pet_messages_pet_chat_created", "pet_id", "chat_id", "created_at"),
        Index("idx_ai_pet_messages_chat_telegram", "chat_id", "telegram_message_id"),
    )


class AiPetMemoryModel(Base):
    """Neutral notes a pet keeps from conversations in one chat; never carried to other chats."""

    __tablename__ = "ai_pet_memories"

    id: Mapped[int] = mapped_column(_AUTOINCREMENT_PK, primary_key=True, autoincrement=True)
    pet_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ai_pets.id", ondelete="CASCADE"), nullable=False)
    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False
    )
    subject_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.telegram_user_id", ondelete="SET NULL"), nullable=True
    )
    content: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="dialogue", server_default="dialogue")
    weight: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("source IN ('aggregate', 'dialogue')", name="ck_ai_pet_memories_source"),
        Index("idx_ai_pet_memories_pet_chat_created", "pet_id", "chat_id", "created_at"),
    )
