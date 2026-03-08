"""add chat assistant feature tables and settings

Revision ID: 0023_chat_assistant
Revises: 0022_rel_per_chat
Create Date: 2026-03-08 11:20:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023_chat_assistant"
down_revision: str | None = "0022_rel_per_chat"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_chat_activity", sa.Column("title_prefix", sa.String(length=96), nullable=True))

    op.add_column(
        "chat_settings",
        sa.Column("smart_triggers_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("welcome_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "chat_settings",
        sa.Column(
            "welcome_text",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'Привет, {user}! Добро пожаловать в {chat}.'"),
        ),
    )
    op.add_column(
        "chat_settings",
        sa.Column("welcome_button_text", sa.String(length=128), nullable=False, server_default=""),
    )
    op.add_column(
        "chat_settings",
        sa.Column("welcome_button_url", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "chat_settings",
        sa.Column("goodbye_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "chat_settings",
        sa.Column(
            "goodbye_text",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'Пока, {user}.'"),
        ),
    )
    op.add_column(
        "chat_settings",
        sa.Column(
            "welcome_cleanup_service_messages",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "chat_settings",
        sa.Column("entry_captcha_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("entry_captcha_timeout_seconds", sa.BigInteger(), nullable=False, server_default="180"),
    )
    op.add_column(
        "chat_settings",
        sa.Column("entry_captcha_kick_on_fail", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("custom_rp_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("family_tree_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("titles_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("title_price", sa.BigInteger(), nullable=False, server_default="50000"),
    )
    op.add_column(
        "chat_settings",
        sa.Column("craft_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("auctions_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("auction_duration_minutes", sa.BigInteger(), nullable=False, server_default="10"),
    )
    op.add_column(
        "chat_settings",
        sa.Column("auction_min_increment", sa.BigInteger(), nullable=False, server_default="100"),
    )

    op.create_table(
        "chat_triggers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("keyword", sa.String(length=255), nullable=False),
        sa.Column("keyword_norm", sa.String(length=255), nullable=False),
        sa.Column("match_type", sa.String(length=32), nullable=False, server_default="contains"),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("media_file_id", sa.String(length=255), nullable=True),
        sa.Column("media_type", sa.String(length=32), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
        sa.CheckConstraint("match_type IN ('exact', 'contains', 'starts_with')", name="ck_chat_triggers_match_type"),
    )
    op.create_index("idx_chat_triggers_chat_match", "chat_triggers", ["chat_id", "match_type"], unique=False)
    op.create_index(
        "uq_chat_triggers_chat_keyword_match",
        "chat_triggers",
        ["chat_id", "keyword_norm", "match_type"],
        unique=True,
    )

    op.create_table(
        "chat_custom_social_actions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("trigger_text", sa.String(length=128), nullable=False),
        sa.Column("trigger_text_norm", sa.String(length=128), nullable=False),
        sa.Column("response_template", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
    )
    op.create_index(
        "uq_chat_custom_social_actions_chat_trigger",
        "chat_custom_social_actions",
        ["chat_id", "trigger_text_norm"],
        unique=True,
    )

    op.create_table(
        "relationships_graph",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_a", sa.BigInteger(), nullable=False),
        sa.Column("user_b", sa.BigInteger(), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_a"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_b"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
        sa.CheckConstraint("relation_type IN ('spouse', 'parent', 'child', 'pet')", name="ck_relationships_graph_type"),
        sa.CheckConstraint("user_a != user_b", name="ck_relationships_graph_distinct_users"),
    )
    op.create_index("idx_relationships_graph_chat_a", "relationships_graph", ["chat_id", "user_a"], unique=False)
    op.create_index("idx_relationships_graph_chat_b", "relationships_graph", ["chat_id", "user_b"], unique=False)
    op.create_index(
        "uq_relationships_graph_chat_relation_pair",
        "relationships_graph",
        ["chat_id", "user_a", "user_b", "relation_type"],
        unique=True,
    )

    op.create_table(
        "chat_audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("target_user_id", sa.BigInteger(), nullable=True),
        sa.Column("action_code", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("meta_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
    )
    op.create_index("idx_chat_audit_logs_chat_created", "chat_audit_logs", ["chat_id", "created_at"], unique=False)
    op.create_index(
        "idx_chat_audit_logs_chat_action_created",
        "chat_audit_logs",
        ["chat_id", "action_code", "created_at"],
        unique=False,
    )

    op.create_table(
        "chat_auctions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("seller_user_id", sa.BigInteger(), nullable=False),
        sa.Column("item_code", sa.String(length=128), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("start_price", sa.BigInteger(), nullable=False),
        sa.Column("current_bid", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("highest_bid_user_id", sa.BigInteger(), nullable=True),
        sa.Column("min_increment", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["seller_user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["highest_bid_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
        sa.CheckConstraint("scope_type IN ('global', 'chat')", name="ck_chat_auctions_scope_type"),
        sa.CheckConstraint("quantity > 0", name="ck_chat_auctions_qty_positive"),
        sa.CheckConstraint("start_price > 0", name="ck_chat_auctions_start_price_positive"),
        sa.CheckConstraint("current_bid >= 0", name="ck_chat_auctions_current_bid_non_negative"),
        sa.CheckConstraint("min_increment > 0", name="ck_chat_auctions_min_increment_positive"),
        sa.CheckConstraint("status IN ('open', 'closed', 'cancelled')", name="ck_chat_auctions_status"),
    )
    op.create_index(
        "idx_chat_auctions_chat_status_created",
        "chat_auctions",
        ["chat_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_chat_auctions_chat_status_ends",
        "chat_auctions",
        ["chat_id", "status", "ends_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_chat_auctions_chat_status_ends", table_name="chat_auctions")
    op.drop_index("idx_chat_auctions_chat_status_created", table_name="chat_auctions")
    op.drop_table("chat_auctions")

    op.drop_index("idx_chat_audit_logs_chat_action_created", table_name="chat_audit_logs")
    op.drop_index("idx_chat_audit_logs_chat_created", table_name="chat_audit_logs")
    op.drop_table("chat_audit_logs")

    op.drop_index("uq_relationships_graph_chat_relation_pair", table_name="relationships_graph")
    op.drop_index("idx_relationships_graph_chat_b", table_name="relationships_graph")
    op.drop_index("idx_relationships_graph_chat_a", table_name="relationships_graph")
    op.drop_table("relationships_graph")

    op.drop_index("uq_chat_custom_social_actions_chat_trigger", table_name="chat_custom_social_actions")
    op.drop_table("chat_custom_social_actions")

    op.drop_index("uq_chat_triggers_chat_keyword_match", table_name="chat_triggers")
    op.drop_index("idx_chat_triggers_chat_match", table_name="chat_triggers")
    op.drop_table("chat_triggers")

    op.drop_column("chat_settings", "auction_min_increment")
    op.drop_column("chat_settings", "auction_duration_minutes")
    op.drop_column("chat_settings", "auctions_enabled")
    op.drop_column("chat_settings", "craft_enabled")
    op.drop_column("chat_settings", "title_price")
    op.drop_column("chat_settings", "titles_enabled")
    op.drop_column("chat_settings", "family_tree_enabled")
    op.drop_column("chat_settings", "custom_rp_enabled")
    op.drop_column("chat_settings", "entry_captcha_kick_on_fail")
    op.drop_column("chat_settings", "entry_captcha_timeout_seconds")
    op.drop_column("chat_settings", "entry_captcha_enabled")
    op.drop_column("chat_settings", "welcome_cleanup_service_messages")
    op.drop_column("chat_settings", "goodbye_text")
    op.drop_column("chat_settings", "goodbye_enabled")
    op.drop_column("chat_settings", "welcome_button_url")
    op.drop_column("chat_settings", "welcome_button_text")
    op.drop_column("chat_settings", "welcome_text")
    op.drop_column("chat_settings", "welcome_enabled")
    op.drop_column("chat_settings", "smart_triggers_enabled")

    op.drop_column("user_chat_activity", "title_prefix")
