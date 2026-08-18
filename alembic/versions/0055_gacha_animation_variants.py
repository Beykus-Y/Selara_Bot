"""add gacha_animation_variants cache table

Revision ID: 0055_gacha_animation_variants
Revises: 0054_add_gacha_animation_enabled
Create Date: 2026-08-19 00:00:01
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055_gacha_animation_variants"
down_revision: str | None = "0054_add_gacha_animation_enabled"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gacha_animation_variants",
        sa.Column("banner", sa.String(length=32), nullable=False),
        sa.Column("card_code", sa.String(length=64), nullable=False),
        sa.Column("variant_index", sa.SmallInteger(), nullable=False),
        sa.Column("telegram_file_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("banner", "card_code", "variant_index"),
    )


def downgrade() -> None:
    op.drop_table("gacha_animation_variants")
