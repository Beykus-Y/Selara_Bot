"""add admin broadcasts and reply tracking

Revision ID: 0043_add_admin_broadcasts
Revises: 0042_add_iris_view_setting
Create Date: 2026-04-11 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_add_admin_broadcasts"
down_revision: str | None = "0042_add_iris_view_setting"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_broadcasts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("active_since_days", sa.SmallInteger(), nullable=False, server_default="3"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_admin_broadcasts_created", "admin_broadcasts", ["created_at"], unique=False)

    op.create_table(
        "admin_broadcast_deliveries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("broadcast_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_title_snapshot", sa.Text(), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_admin_broadcast_deliveries_status"),
        sa.ForeignKeyConstraint(["broadcast_id"], ["admin_broadcasts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("broadcast_id", "chat_id", name="uq_admin_broadcast_deliveries_broadcast_chat"),
    )
    op.create_index(
        "idx_admin_broadcast_deliveries_broadcast_status",
        "admin_broadcast_deliveries",
        ["broadcast_id", "status"],
        unique=False,
    )
    op.create_index(
        "idx_admin_broadcast_deliveries_chat_message",
        "admin_broadcast_deliveries",
        ["chat_id", "telegram_message_id"],
        unique=False,
    )

    op.create_table(
        "admin_broadcast_replies",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("delivery_id", sa.BigInteger(), nullable=False),
        sa.Column("reply_user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("message_type", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("raw_message_json", sa.JSON(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["delivery_id"], ["admin_broadcast_deliveries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reply_user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("delivery_id", "telegram_message_id", name="uq_admin_broadcast_replies_delivery_message"),
    )
    op.create_index(
        "idx_admin_broadcast_replies_delivery_sent",
        "admin_broadcast_replies",
        ["delivery_id", "sent_at"],
        unique=False,
    )
    op.create_index(
        "idx_admin_broadcast_replies_user_sent",
        "admin_broadcast_replies",
        ["reply_user_id", "sent_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_admin_broadcast_replies_user_sent", table_name="admin_broadcast_replies")
    op.drop_index("idx_admin_broadcast_replies_delivery_sent", table_name="admin_broadcast_replies")
    op.drop_table("admin_broadcast_replies")

    op.drop_index("idx_admin_broadcast_deliveries_chat_message", table_name="admin_broadcast_deliveries")
    op.drop_index("idx_admin_broadcast_deliveries_broadcast_status", table_name="admin_broadcast_deliveries")
    op.drop_table("admin_broadcast_deliveries")

    op.drop_index("idx_admin_broadcasts_created", table_name="admin_broadcasts")
    op.drop_table("admin_broadcasts")
