"""add chat rest states

Revision ID: 0039_add_chat_rest_states
Revises: 0038_marriage_milestones
Create Date: 2026-04-01 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_add_chat_rest_states"
down_revision: str | None = "0038_marriage_milestones"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_chat_rest_states",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("chat_id", "user_id"),
    )
    op.create_index(
        "idx_user_chat_rest_states_chat_expires",
        "user_chat_rest_states",
        ["chat_id", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_user_chat_rest_states_chat_expires", table_name="user_chat_rest_states")
    op.drop_table("user_chat_rest_states")
