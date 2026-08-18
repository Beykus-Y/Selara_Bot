"""add cache_version to gacha_animation_variants

Revision ID: 0056_gacha_variant_cache_version
Revises: 0055_gacha_animation_variants
Create Date: 2026-08-19 00:00:02
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0056_gacha_variant_cache_version"
down_revision: str | None = "0055_gacha_animation_variants"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gacha_animation_variants",
        sa.Column("cache_version", sa.String(length=32), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("gacha_animation_variants", "cache_version")
