"""add hybrid reactions and media to admin broadcasts

Revision ID: 0051_broadcast_reactions
Revises: 0050_leave_service_cleanup
Create Date: 2026-08-14 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0051_broadcast_reactions"
down_revision: str | None = "0050_leave_service_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("admin_broadcasts", sa.Column("rendered_body", sa.Text(), nullable=True))
    op.add_column("admin_broadcasts", sa.Column("reaction_options_json", sa.JSON(), nullable=True))
    op.add_column("admin_broadcasts", sa.Column("media_type", sa.String(length=16), nullable=True))
    op.add_column("admin_broadcasts", sa.Column("media_file_id", sa.Text(), nullable=True))
    op.add_column("admin_broadcasts", sa.Column("media_file_unique_id", sa.Text(), nullable=True))
    op.execute("UPDATE admin_broadcasts SET rendered_body = body WHERE rendered_body IS NULL")

    op.add_column(
        "admin_broadcast_deliveries",
        sa.Column("reaction_mode", sa.String(length=16), nullable=False, server_default="none"),
    )
    op.add_column("admin_broadcast_deliveries", sa.Column("bot_member_status", sa.String(length=32), nullable=True))
    op.create_check_constraint(
        "ck_admin_broadcast_deliveries_reaction_mode",
        "admin_broadcast_deliveries",
        "reaction_mode IN ('none', 'native', 'inline')",
    )

    op.create_table(
        "admin_broadcast_reactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("delivery_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("option_key", sa.String(length=16), nullable=False),
        sa.Column("emoji", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("reacted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("source IN ('native', 'inline')", name="ck_admin_broadcast_reactions_source"),
        sa.CheckConstraint(
            "actor_user_id IS NOT NULL OR actor_chat_id IS NOT NULL",
            name="ck_admin_broadcast_reactions_actor",
        ),
        sa.ForeignKeyConstraint(["delivery_id"], ["admin_broadcast_deliveries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "delivery_id",
            "source",
            "actor_user_id",
            "option_key",
            name="uq_admin_broadcast_reaction_user_option",
        ),
    )
    op.create_index(
        "idx_admin_broadcast_reactions_delivery_active",
        "admin_broadcast_reactions",
        ["delivery_id", "active"],
    )
    op.create_index(
        "idx_admin_broadcast_reactions_user",
        "admin_broadcast_reactions",
        ["actor_user_id", "updated_at"],
    )

    op.create_table(
        "admin_broadcast_reaction_counts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("delivery_id", sa.BigInteger(), nullable=False),
        sa.Column("emoji", sa.String(length=64), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("count >= 0", name="ck_admin_broadcast_reaction_counts_nonnegative"),
        sa.ForeignKeyConstraint(["delivery_id"], ["admin_broadcast_deliveries.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("delivery_id", "emoji", name="uq_admin_broadcast_reaction_count_delivery_emoji"),
    )


def downgrade() -> None:
    op.drop_table("admin_broadcast_reaction_counts")
    op.drop_index("idx_admin_broadcast_reactions_user", table_name="admin_broadcast_reactions")
    op.drop_index("idx_admin_broadcast_reactions_delivery_active", table_name="admin_broadcast_reactions")
    op.drop_table("admin_broadcast_reactions")
    op.drop_constraint(
        "ck_admin_broadcast_deliveries_reaction_mode",
        "admin_broadcast_deliveries",
        type_="check",
    )
    op.drop_column("admin_broadcast_deliveries", "bot_member_status")
    op.drop_column("admin_broadcast_deliveries", "reaction_mode")
    op.drop_column("admin_broadcasts", "media_file_unique_id")
    op.drop_column("admin_broadcasts", "media_file_id")
    op.drop_column("admin_broadcasts", "media_type")
    op.drop_column("admin_broadcasts", "reaction_options_json")
    op.drop_column("admin_broadcasts", "rendered_body")
