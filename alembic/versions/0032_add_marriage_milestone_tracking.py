"""add last_milestone_days to marriages

Revision ID: 0032_add_marriage_milestone_tracking
Revises: 0031_merge_activity_heads
Create Date: 2026-04-01 00:00:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_add_marriage_milestone_tracking"
down_revision: str = "0031_merge_activity_heads"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "marriages",
        sa.Column(
            "last_milestone_days",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("marriages", "last_milestone_days")
