"""add achievements tables

Revision ID: 0028_add_achievements
Revises: 0027_chat_membership_state
Create Date: 2026-03-09 21:15:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028_add_achievements"
down_revision: str | None = "0027_chat_membership_state"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_chat_achievement",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("achievement_id", sa.String(length=128), nullable=False),
        sa.Column("awarded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("award_reason", sa.Text(), nullable=True),
        sa.Column("meta_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "user_id", "achievement_id", name="uq_user_chat_achievement"),
    )
    op.create_index("idx_user_chat_achievement_chat_user", "user_chat_achievement", ["chat_id", "user_id"], unique=False)
    op.create_index(
        "idx_user_chat_achievement_chat_achievement",
        "user_chat_achievement",
        ["chat_id", "achievement_id"],
        unique=False,
    )
    op.create_index("idx_user_chat_achievement_user", "user_chat_achievement", ["user_id"], unique=False)
    op.create_index("idx_user_chat_achievement_achievement", "user_chat_achievement", ["achievement_id"], unique=False)

    op.create_table(
        "user_global_achievement",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("achievement_id", sa.String(length=128), nullable=False),
        sa.Column("awarded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("award_reason", sa.Text(), nullable=True),
        sa.Column("meta_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "achievement_id", name="uq_user_global_achievement"),
    )
    op.create_index("idx_user_global_achievement_user", "user_global_achievement", ["user_id"], unique=False)
    op.create_index("idx_user_global_achievement_achievement", "user_global_achievement", ["achievement_id"], unique=False)

    op.create_table(
        "chat_achievement_stats",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("achievement_id", sa.String(length=128), nullable=False),
        sa.Column("holders_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("holders_percent", sa.Numeric(5, 2), server_default="0", nullable=False),
        sa.Column("active_members_base_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "achievement_id", name="uq_chat_achievement_stats"),
    )
    op.create_index("idx_chat_achievement_stats_chat", "chat_achievement_stats", ["chat_id"], unique=False)
    op.create_index(
        "idx_chat_achievement_stats_chat_achievement",
        "chat_achievement_stats",
        ["chat_id", "achievement_id"],
        unique=False,
    )

    op.create_table(
        "global_achievement_stats",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("achievement_id", sa.String(length=128), nullable=False),
        sa.Column("holders_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("holders_percent", sa.Numeric(5, 2), server_default="0", nullable=False),
        sa.Column("global_base_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("achievement_id", name="uq_global_achievement_stats"),
    )
    op.create_index(
        "idx_global_achievement_stats_achievement",
        "global_achievement_stats",
        ["achievement_id"],
        unique=False,
    )

    op.create_table(
        "chat_metrics",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("active_members_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id"),
    )

    op.create_table(
        "global_metrics",
        sa.Column("id", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("global_users_base_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("global_metrics")
    op.drop_table("chat_metrics")
    op.drop_index("idx_global_achievement_stats_achievement", table_name="global_achievement_stats")
    op.drop_table("global_achievement_stats")
    op.drop_index("idx_chat_achievement_stats_chat_achievement", table_name="chat_achievement_stats")
    op.drop_index("idx_chat_achievement_stats_chat", table_name="chat_achievement_stats")
    op.drop_table("chat_achievement_stats")
    op.drop_index("idx_user_global_achievement_achievement", table_name="user_global_achievement")
    op.drop_index("idx_user_global_achievement_user", table_name="user_global_achievement")
    op.drop_table("user_global_achievement")
    op.drop_index("idx_user_chat_achievement_achievement", table_name="user_chat_achievement")
    op.drop_index("idx_user_chat_achievement_user", table_name="user_chat_achievement")
    op.drop_index("idx_user_chat_achievement_chat_achievement", table_name="user_chat_achievement")
    op.drop_index("idx_user_chat_achievement_chat_user", table_name="user_chat_achievement")
    op.drop_table("user_chat_achievement")
