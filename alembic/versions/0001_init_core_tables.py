"""init core tables

Revision ID: 0001_init_core_tables
Revises:
Create Date: 2026-02-12 00:00:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_init_core_tables"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column("last_name", sa.String(length=255), nullable=True),
        sa.Column("is_bot", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("telegram_user_id"),
    )

    op.create_table(
        "chats",
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("telegram_chat_id"),
    )

    op.create_table(
        "user_chat_activity",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("message_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id", "user_id"),
    )

    op.create_index(
        "idx_user_chat_activity_chat_count",
        "user_chat_activity",
        ["chat_id", "message_count"],
        unique=False,
    )
    op.create_index(
        "idx_user_chat_activity_chat_last_seen",
        "user_chat_activity",
        ["chat_id", "last_seen_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_user_chat_activity_chat_last_seen", table_name="user_chat_activity")
    op.drop_index("idx_user_chat_activity_chat_count", table_name="user_chat_activity")
    op.drop_table("user_chat_activity")
    op.drop_table("chats")
    op.drop_table("users")
