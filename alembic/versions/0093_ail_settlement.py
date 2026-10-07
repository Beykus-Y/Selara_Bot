"""AIL settlement marker on quota usage rows

Revision ID: 0093_ail_settlement
Revises: 0092_personal_model_ail
Create Date: 2026-10-06 00:00:07
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0093_ail_settlement"
down_revision: str | None = "0092_personal_model_ail"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Nullable and additive: the previous image never reads or writes it.
    op.add_column("ai_feature_quota_usage", sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_feature_quota_usage", "settled_at")
