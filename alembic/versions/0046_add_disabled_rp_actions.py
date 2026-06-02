"""add disabled_rp_actions table

Revision ID: 0046_add_disabled_rp_actions
Revises: 0045_add_clans
Create Date: 2026-06-02 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_add_disabled_rp_actions"
down_revision: str | None = "0045_add_clans"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "disabled_rp_actions",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("action_key", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["chats.telegram_chat_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chat_id", "action_key"),
    )


def downgrade() -> None:
    op.drop_table("disabled_rp_actions")
