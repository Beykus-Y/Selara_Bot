"""add admin panel sessions table

Revision ID: 0033_add_admin_sessions
Revises: 0032_strip_iris_awards
Create Date: 2026-03-14 12:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0033_add_admin_sessions"
down_revision: str | None = "0032_strip_iris_awards"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_sessions",
        sa.Column("session_token", sa.String(length=128), nullable=False),
        sa.Column("admin_user_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("session_token"),
    )
    op.create_index(
        "idx_admin_sessions_user_created",
        "admin_sessions",
        ["admin_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_admin_sessions_expires",
        "admin_sessions",
        ["expires_at", "revoked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_admin_sessions_expires", table_name="admin_sessions")
    op.drop_index("idx_admin_sessions_user_created", table_name="admin_sessions")
    op.drop_table("admin_sessions")
