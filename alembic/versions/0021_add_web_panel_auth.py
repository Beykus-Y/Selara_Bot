"""add web panel auth tables

Revision ID: 0021_web_panel_auth
Revises: 0020_dynamic_roles
Create Date: 2026-03-06 12:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021_web_panel_auth"
down_revision: str | None = "0020_dynamic_roles"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_login_codes",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("code_digest", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_web_login_codes_digest",
        "web_login_codes",
        ["code_digest", "expires_at"],
        unique=False,
    )
    op.create_index(
        "idx_web_login_codes_user_created",
        "web_login_codes",
        ["user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "web_sessions",
        sa.Column("session_digest", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_digest"),
    )
    op.create_index(
        "idx_web_sessions_user_created",
        "web_sessions",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_web_sessions_expires",
        "web_sessions",
        ["expires_at", "revoked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_web_sessions_expires", table_name="web_sessions")
    op.drop_index("idx_web_sessions_user_created", table_name="web_sessions")
    op.drop_table("web_sessions")

    op.drop_index("idx_web_login_codes_user_created", table_name="web_login_codes")
    op.drop_index("idx_web_login_codes_digest", table_name="web_login_codes")
    op.drop_table("web_login_codes")
