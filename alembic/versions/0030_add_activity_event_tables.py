"""add activity event tables

Revision ID: 0030_add_activity_event_tables
Revises: 0029_add_iris_import_tables
Create Date: 2026-03-13 14:30:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030_add_activity_event_tables"
down_revision: str | None = "0029_add_iris_import_tables"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_chat_message_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source_kind", sa.String(length=32), nullable=True),
        sa.Column("source_bucket_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_seq", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "telegram_message_id", name="uq_user_chat_message_events_chat_message"),
        sa.UniqueConstraint(
            "chat_id",
            "user_id",
            "source_kind",
            "source_bucket_at",
            "source_seq",
            name="uq_user_chat_message_events_synthetic_source",
        ),
    )
    op.create_index(
        "idx_user_chat_message_events_chat_sent",
        "user_chat_message_events",
        ["chat_id", "sent_at"],
        unique=False,
    )
    op.create_index(
        "idx_user_chat_message_events_chat_user_sent",
        "user_chat_message_events",
        ["chat_id", "user_id", "sent_at"],
        unique=False,
    )

    op.create_table(
        "chat_activity_event_sync_state",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("legacy_total_messages", sa.BigInteger(), nullable=True),
        sa.Column("event_total_messages", sa.BigInteger(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id"),
    )


def downgrade() -> None:
    op.drop_table("chat_activity_event_sync_state")
    op.drop_index("idx_user_chat_message_events_chat_user_sent", table_name="user_chat_message_events")
    op.drop_index("idx_user_chat_message_events_chat_sent", table_name="user_chat_message_events")
    op.drop_table("user_chat_message_events")
