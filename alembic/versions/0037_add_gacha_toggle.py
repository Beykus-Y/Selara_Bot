"""add gacha toggle settings

Revision ID: 0037_add_gacha_toggle
Revises: 0036_add_interesting_facts
Create Date: 2026-04-01 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_add_gacha_toggle"
down_revision: str | None = "0036_add_interesting_facts"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column("gacha_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("gacha_restore_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_settings", "gacha_restore_at")
    op.drop_column("chat_settings", "gacha_enabled")
