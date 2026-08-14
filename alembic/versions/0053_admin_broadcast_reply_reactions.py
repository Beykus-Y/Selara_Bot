"""store bot reactions on admin broadcast replies

Revision ID: 0053_reply_bot_reactions
Revises: 0052_all_broadcast_reactions
Create Date: 2026-08-14 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0053_reply_bot_reactions"
down_revision: str | None = "0052_all_broadcast_reactions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "admin_broadcast_replies",
        sa.Column("bot_reaction_emoji", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "admin_broadcast_replies",
        sa.Column("bot_reaction_updated_by_user_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "admin_broadcast_replies",
        sa.Column(
            "bot_reaction_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("admin_broadcast_replies", "bot_reaction_updated_at")
    op.drop_column("admin_broadcast_replies", "bot_reaction_updated_by_user_id")
    op.drop_column("admin_broadcast_replies", "bot_reaction_emoji")
