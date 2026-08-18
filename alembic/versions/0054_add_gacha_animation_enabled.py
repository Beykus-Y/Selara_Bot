"""add gacha_animation_enabled to users

Revision ID: 0054_add_gacha_animation_enabled
Revises: 0053_reply_bot_reactions
Create Date: 2026-08-19 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054_add_gacha_animation_enabled"
down_revision: str | None = "0053_reply_bot_reactions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "gacha_animation_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "gacha_animation_enabled")
