"""extend family/web economy runtime tables

Revision ID: 0026_family_web_econ
Revises: 0025_user_profiles_awards
Create Date: 2026-03-09 00:30:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0026_family_web_econ"
down_revision: str | None = "0025_user_profiles_awards"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column("cleanup_economy_commands", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.add_column("user_chat_profiles", sa.Column("avatar_frame_code", sa.String(length=64), nullable=True))
    op.add_column("user_chat_profiles", sa.Column("emoji_status_code", sa.String(length=64), nullable=True))
    op.add_column("economy_farms", sa.Column("last_planted_crop_code", sa.String(length=64), nullable=True))

    op.create_table(
        "economy_market_trades",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("listing_id", sa.BigInteger(), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("seller_user_id", sa.BigInteger(), nullable=False),
        sa.Column("buyer_user_id", sa.BigInteger(), nullable=False),
        sa.Column("item_code", sa.String(length=128), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("unit_price", sa.BigInteger(), nullable=False),
        sa.Column("total_price", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("scope_type IN ('global', 'chat')", name="ck_economy_market_trades_scope_type"),
        sa.CheckConstraint("quantity > 0", name="ck_economy_market_trades_qty_positive"),
        sa.CheckConstraint("unit_price > 0", name="ck_economy_market_trades_unit_price_positive"),
        sa.CheckConstraint("total_price > 0", name="ck_economy_market_trades_total_price_positive"),
        sa.ForeignKeyConstraint(["listing_id"], ["economy_market_listings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["seller_user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["buyer_user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_economy_market_trades_scope_created",
        "economy_market_trades",
        ["scope_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_economy_market_trades_item_created",
        "economy_market_trades",
        ["item_code", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_economy_market_trades_buyer_created",
        "economy_market_trades",
        ["buyer_user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "chat_global_boosts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("boost_code", sa.String(length=64), nullable=False),
        sa.Column("value_percent", sa.BigInteger(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("scope_type IN ('global', 'chat')", name="ck_chat_global_boosts_scope_type"),
        sa.CheckConstraint("value_percent > 0", name="ck_chat_global_boosts_value_positive"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_chat_global_boosts_interval"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_chat_global_boosts_chat_ends",
        "chat_global_boosts",
        ["chat_id", "ends_at"],
        unique=False,
    )
    op.create_index(
        "idx_chat_global_boosts_scope_ends",
        "chat_global_boosts",
        ["scope_id", "ends_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_chat_global_boosts_scope_ends", table_name="chat_global_boosts")
    op.drop_index("idx_chat_global_boosts_chat_ends", table_name="chat_global_boosts")
    op.drop_table("chat_global_boosts")

    op.drop_index("idx_economy_market_trades_buyer_created", table_name="economy_market_trades")
    op.drop_index("idx_economy_market_trades_item_created", table_name="economy_market_trades")
    op.drop_index("idx_economy_market_trades_scope_created", table_name="economy_market_trades")
    op.drop_table("economy_market_trades")

    op.drop_column("economy_farms", "last_planted_crop_code")
    op.drop_column("user_chat_profiles", "emoji_status_code")
    op.drop_column("user_chat_profiles", "avatar_frame_code")
    op.drop_column("chat_settings", "cleanup_economy_commands")
