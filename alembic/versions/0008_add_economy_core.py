"""add economy core

Revision ID: 0008_add_economy_core
Revises: 0007_bot_roles_mod
Create Date: 2026-02-14 00:10:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_add_economy_core"
down_revision: str | None = "0007_bot_roles_mod"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_settings", sa.Column("economy_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column("chat_settings", sa.Column("economy_mode", sa.String(length=16), nullable=False, server_default="global"))
    op.add_column(
        "chat_settings",
        sa.Column("economy_tap_cooldown_seconds", sa.BigInteger(), nullable=False, server_default=sa.text("45")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("economy_daily_base_reward", sa.BigInteger(), nullable=False, server_default=sa.text("120")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("economy_daily_streak_cap", sa.BigInteger(), nullable=False, server_default=sa.text("7")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("economy_lottery_ticket_price", sa.BigInteger(), nullable=False, server_default=sa.text("150")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("economy_lottery_paid_daily_limit", sa.BigInteger(), nullable=False, server_default=sa.text("10")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("economy_transfer_daily_limit", sa.BigInteger(), nullable=False, server_default=sa.text("5000")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("economy_transfer_tax_percent", sa.BigInteger(), nullable=False, server_default=sa.text("5")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("economy_market_fee_percent", sa.BigInteger(), nullable=False, server_default=sa.text("2")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("economy_negative_event_chance_percent", sa.BigInteger(), nullable=False, server_default=sa.text("22")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("economy_negative_event_loss_percent", sa.BigInteger(), nullable=False, server_default=sa.text("30")),
    )

    op.create_table(
        "economy_accounts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("balance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tap_streak", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_tap_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("daily_streak", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_daily_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("free_lottery_claimed_on", sa.Date(), nullable=True),
        sa.Column("paid_lottery_used_today", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("paid_lottery_used_on", sa.Date(), nullable=True),
        sa.Column("sprinkler_level", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tap_glove_level", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("storage_level", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("scope_type IN ('global', 'chat')", name="ck_economy_accounts_scope_type"),
        sa.CheckConstraint("balance >= 0", name="ck_economy_accounts_balance_non_negative"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_economy_accounts_scope_user", "economy_accounts", ["scope_id", "user_id"], unique=True)
    op.create_index("idx_economy_accounts_scope", "economy_accounts", ["scope_id"], unique=False)
    op.create_index("idx_economy_accounts_chat", "economy_accounts", ["chat_id"], unique=False)

    op.create_table(
        "economy_farms",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("farm_level", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("size_tier", sa.String(length=16), nullable=False, server_default="small"),
        sa.Column("negative_event_streak", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("size_tier IN ('small', 'medium', 'large')", name="ck_economy_farms_size_tier"),
        sa.CheckConstraint("farm_level >= 1 AND farm_level <= 5", name="ck_economy_farms_level_range"),
        sa.ForeignKeyConstraint(["account_id"], ["economy_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id"),
    )

    op.create_table(
        "economy_plots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("plot_no", sa.BigInteger(), nullable=False),
        sa.Column("crop_code", sa.String(length=64), nullable=True),
        sa.Column("planted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("yield_boost_pct", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("shield_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["account_id"], ["economy_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_economy_plots_account_plot", "economy_plots", ["account_id", "plot_no"], unique=True)
    op.create_index("idx_economy_plots_account_ready", "economy_plots", ["account_id", "ready_at"], unique=False)

    op.create_table(
        "economy_inventory",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("item_code", sa.String(length=128), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("quantity >= 0", name="ck_economy_inventory_qty_non_negative"),
        sa.ForeignKeyConstraint(["account_id"], ["economy_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "item_code"),
    )
    op.create_index("idx_economy_inventory_account", "economy_inventory", ["account_id"], unique=False)

    op.create_table(
        "economy_ledger",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("meta_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("direction IN ('in', 'out')", name="ck_economy_ledger_direction"),
        sa.CheckConstraint("amount >= 0", name="ck_economy_ledger_amount_non_negative"),
        sa.ForeignKeyConstraint(["account_id"], ["economy_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_economy_ledger_account_created", "economy_ledger", ["account_id", "created_at"], unique=False)

    op.create_table(
        "economy_transfer_daily",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("limit_date", sa.Date(), nullable=False),
        sa.Column("sent_amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sent_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["account_id"], ["economy_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "limit_date"),
    )

    op.create_table(
        "economy_market_listings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("seller_user_id", sa.BigInteger(), nullable=False),
        sa.Column("item_code", sa.String(length=128), nullable=False),
        sa.Column("qty_total", sa.BigInteger(), nullable=False),
        sa.Column("qty_left", sa.BigInteger(), nullable=False),
        sa.Column("unit_price", sa.BigInteger(), nullable=False),
        sa.Column("fee_paid", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("scope_type IN ('global', 'chat')", name="ck_economy_market_scope_type"),
        sa.CheckConstraint("status IN ('open', 'closed', 'cancelled', 'expired')", name="ck_economy_market_status"),
        sa.CheckConstraint("qty_total > 0", name="ck_economy_market_qty_total_positive"),
        sa.CheckConstraint("qty_left >= 0", name="ck_economy_market_qty_left_non_negative"),
        sa.CheckConstraint("unit_price > 0", name="ck_economy_market_unit_price_positive"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["seller_user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_economy_market_scope_status_created",
        "economy_market_listings",
        ["scope_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_economy_market_seller_status",
        "economy_market_listings",
        ["seller_user_id", "status"],
        unique=False,
    )

    op.create_table(
        "economy_private_contexts",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("economy_private_contexts")

    op.drop_index("idx_economy_market_seller_status", table_name="economy_market_listings")
    op.drop_index("idx_economy_market_scope_status_created", table_name="economy_market_listings")
    op.drop_table("economy_market_listings")

    op.drop_table("economy_transfer_daily")

    op.drop_index("idx_economy_ledger_account_created", table_name="economy_ledger")
    op.drop_table("economy_ledger")

    op.drop_index("idx_economy_inventory_account", table_name="economy_inventory")
    op.drop_table("economy_inventory")

    op.drop_index("idx_economy_plots_account_ready", table_name="economy_plots")
    op.drop_index("uq_economy_plots_account_plot", table_name="economy_plots")
    op.drop_table("economy_plots")

    op.drop_table("economy_farms")

    op.drop_index("idx_economy_accounts_chat", table_name="economy_accounts")
    op.drop_index("idx_economy_accounts_scope", table_name="economy_accounts")
    op.drop_index("uq_economy_accounts_scope_user", table_name="economy_accounts")
    op.drop_table("economy_accounts")

    op.drop_column("chat_settings", "economy_negative_event_loss_percent")
    op.drop_column("chat_settings", "economy_negative_event_chance_percent")
    op.drop_column("chat_settings", "economy_market_fee_percent")
    op.drop_column("chat_settings", "economy_transfer_tax_percent")
    op.drop_column("chat_settings", "economy_transfer_daily_limit")
    op.drop_column("chat_settings", "economy_lottery_paid_daily_limit")
    op.drop_column("chat_settings", "economy_lottery_ticket_price")
    op.drop_column("chat_settings", "economy_daily_streak_cap")
    op.drop_column("chat_settings", "economy_daily_base_reward")
    op.drop_column("chat_settings", "economy_tap_cooldown_seconds")
    op.drop_column("chat_settings", "economy_mode")
    op.drop_column("chat_settings", "economy_enabled")
