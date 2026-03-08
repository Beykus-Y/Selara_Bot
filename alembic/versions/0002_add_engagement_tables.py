"""add engagement tables

Revision ID: 0002_add_engagement_tables
Revises: 0001_init_core_tables
Create Date: 2026-02-13 00:00:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_add_engagement_tables"
down_revision: str | None = "0001_init_core_tables"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_chat_activity_daily",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("message_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id", "user_id", "activity_date"),
    )

    op.create_index(
        "idx_user_chat_activity_daily_chat_date",
        "user_chat_activity_daily",
        ["chat_id", "activity_date"],
        unique=False,
    )

    op.create_table(
        "user_karma_votes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("voter_user_id", sa.BigInteger(), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=False),
        sa.Column("vote_value", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("vote_value IN (-1, 1)", name="ck_user_karma_votes_vote_value"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["voter_user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "idx_user_karma_votes_chat_target_created",
        "user_karma_votes",
        ["chat_id", "target_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_user_karma_votes_chat_voter_created",
        "user_karma_votes",
        ["chat_id", "voter_user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_user_karma_votes_chat_voter_created", table_name="user_karma_votes")
    op.drop_index("idx_user_karma_votes_chat_target_created", table_name="user_karma_votes")
    op.drop_table("user_karma_votes")

    op.drop_index("idx_user_chat_activity_daily_chat_date", table_name="user_chat_activity_daily")
    op.drop_table("user_chat_activity_daily")
