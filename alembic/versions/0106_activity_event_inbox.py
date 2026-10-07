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
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("activity_event_inbox")
