"""add clans

Revision ID: 0045_add_clans
Revises: 0044_add_chat_personas
Create Date: 2026-06-02 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045_add_clans"
down_revision: str | None = "0044_add_chat_personas"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("creator_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "name", name="uq_clan_chat_name"),
    )
    op.create_index("idx_clans_chat_id", "clans", ["chat_id"])

    op.create_table(
        "clan_members",
        sa.Column("clan_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["clan_id"], ["clans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("clan_id", "user_id"),
        sa.UniqueConstraint("chat_id", "user_id", name="uq_clan_member_chat_user"),
    )
    op.create_index("idx_clan_members_chat_id", "clan_members", ["chat_id"])


def downgrade() -> None:
    op.drop_index("idx_clan_members_chat_id", table_name="clan_members")
    op.drop_table("clan_members")
    op.drop_index("idx_clans_chat_id", table_name="clans")
    op.drop_table("clans")
