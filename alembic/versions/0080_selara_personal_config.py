"""runtime overrides for Selara Personal price, duration, limits and unit weights

Revision ID: 0080_selara_personal_config
Revises: 0079_quota_user_scope
Create Date: 2026-10-06 00:00:02
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0080_selara_personal_config"
down_revision: str | None = "0079_quota_user_scope"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "selara_personal_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("price_stars", sa.Integer(), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("free_daily_limit", sa.Integer(), nullable=True),
        sa.Column("paid_daily_limit", sa.Integer(), nullable=True),
        sa.Column("default_units", sa.Numeric(10, 2), nullable=True),
        sa.Column("unit_weights", sa.JSON(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_selara_personal_config_singleton"),
        sa.CheckConstraint("price_stars IS NULL OR price_stars > 0", name="ck_selara_personal_config_price"),
        sa.CheckConstraint("duration_days IS NULL OR duration_days > 0", name="ck_selara_personal_config_duration"),
        sa.CheckConstraint("free_daily_limit IS NULL OR free_daily_limit > 0", name="ck_selara_personal_config_free"),
        sa.CheckConstraint("paid_daily_limit IS NULL OR paid_daily_limit > 0", name="ck_selara_personal_config_paid"),
        sa.CheckConstraint("default_units IS NULL OR default_units >= 0", name="ck_selara_personal_config_units"),
    )


def downgrade() -> None:
    # Overrides are an optional layer over .env; dropping them only reverts to the base values.
    op.drop_table("selara_personal_config")
