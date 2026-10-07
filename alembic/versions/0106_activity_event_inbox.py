"""Durable inbox for tracked group messages until ActivityBatcher aggregates them (issue #91).

Revision ID: 0106_activity_event_inbox
Revises: 0105_broadcast_resume
"""

import sqlalchemy as sa
from alembic import op

revision = "0106_activity_event_inbox"
down_revision = "0105_broadcast_resume"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "activity_event_inbox",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_type", sa.String(length=32), nullable=False),
        sa.Column("chat_title", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_activity_event_inbox_chat_id", "activity_event_inbox", ["chat_id"])
    op.create_table(
        "activity_event_dead_letters",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_type", sa.String(length=32), nullable=False),
        sa.Column("chat_title", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("activity_event_dead_letters")
    op.drop_index("ix_activity_event_inbox_chat_id", table_name="activity_event_inbox")
    op.drop_table("activity_event_inbox")
