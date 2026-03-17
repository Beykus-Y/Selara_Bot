"""add user feature requests table

Revision ID: 0034_add_user_feature_requests
Revises: 0033_add_admin_sessions
Create Date: 2026-03-17 13:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0034_add_user_feature_requests"
down_revision: str | None = "0033_add_admin_sessions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_feature_requests",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('open', 'done')", name="ck_user_feature_requests_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_user_feature_requests_user_created",
        "user_feature_requests",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_user_feature_requests_status_created",
        "user_feature_requests",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_user_feature_requests_status_created", table_name="user_feature_requests")
    op.drop_index("idx_user_feature_requests_user_created", table_name="user_feature_requests")
    op.drop_table("user_feature_requests")
