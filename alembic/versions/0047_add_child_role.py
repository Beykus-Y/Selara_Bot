"""add child_role to relationships_graph and family_relationship_archive table

Revision ID: 0047_add_child_role
Revises: 0046_add_disabled_rp_actions
Create Date: 2026-06-09 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047_add_child_role"
down_revision: str | None = "0046_add_disabled_rp_actions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "relationships_graph",
        sa.Column("child_role", sa.String(length=16), nullable=True),
    )

    op.create_table(
        "family_relationship_archive",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("original_id", sa.BigInteger(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_a", sa.BigInteger(), nullable=False),
        sa.Column("user_b", sa.BigInteger(), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("child_role", sa.String(length=16), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archive_reason", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("family_relationship_archive")
    op.drop_column("relationships_graph", "child_role")
