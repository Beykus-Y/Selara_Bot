"""Durable inbox for tracked group messages until ActivityBatcher aggregates them (issue #91).

Revision ID: 0106_activity_event_inbox
Revises: 0103_ai_turn_leases
"""

import sqlalchemy as sa
from alembic import op

revision = "0106_activity_event_inbox"
down_revision = "0103_ai_turn_leases"
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
    )
    op.create_index("ix_activity_event_inbox_chat_id", "activity_event_inbox", ["chat_id"])


def downgrade() -> None:
    op.drop_index("ix_activity_event_inbox_chat_id", table_name="activity_event_inbox")
    op.drop_table("activity_event_inbox")
