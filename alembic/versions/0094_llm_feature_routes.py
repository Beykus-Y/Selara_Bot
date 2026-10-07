"""Owner-chosen model profiles for group AI features

Revision ID: 0094_llm_feature_routes
Revises: 0093_ail_settlement
Create Date: 2026-10-07 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0094_llm_feature_routes"
down_revision: str | None = "0093_ail_settlement"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # New table only: the previous image never reads it.
    op.create_table(
        "llm_feature_routes",
        sa.Column("route_key", sa.String(32), primary_key=True),
        sa.Column("profile_key", sa.String(32), nullable=True),
        sa.Column("updated_by", sa.BigInteger, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "profile_key IS NULL OR profile_key IN ('basic', 'analytics', 'freeform', 'creative', 'fast')",
            name="ck_llm_feature_routes_profile",
        ),
    )


def downgrade() -> None:
    op.drop_table("llm_feature_routes")
