"""add messages archive and save_message setting

Revision ID: 0041_add_messages_archive
Revises: 0040_add_subscription_exempt
Create Date: 2026-04-09 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_add_messages_archive"
down_revision: str | None = "0040_add_subscription_exempt"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column(
            "save_message",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_kind", sa.String(length=16), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_type", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("raw_message_json", sa.JSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("snapshot_kind IN ('created', 'edited')", name="ck_messages_snapshot_kind"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("chat_id", "telegram_message_id", "snapshot_hash", name="uq_messages_chat_message_snapshot"),
    )
    op.create_index("idx_messages_chat_snapshot", "messages", ["chat_id", "snapshot_at"], unique=False)
    op.create_index("idx_messages_chat_user_snapshot", "messages", ["chat_id", "user_id", "snapshot_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_messages_chat_user_snapshot", table_name="messages")
    op.drop_index("idx_messages_chat_snapshot", table_name="messages")
    op.drop_table("messages")
    op.drop_column("chat_settings", "save_message")
