"""add antiraid and chat lock settings

Revision ID: 0035_antiraid_chat_lock
Revises: 0034_add_user_feature_requests
Create Date: 2026-03-19 20:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0035_antiraid_chat_lock"
down_revision: str | None = "0034_add_user_feature_requests"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column("antiraid_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("antiraid_recent_window_minutes", sa.BigInteger(), nullable=False, server_default="10"),
    )
    op.add_column(
        "chat_settings",
        sa.Column("chat_write_locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("chat_settings", "chat_write_locked")
    op.drop_column("chat_settings", "antiraid_recent_window_minutes")
    op.drop_column("chat_settings", "antiraid_enabled")
