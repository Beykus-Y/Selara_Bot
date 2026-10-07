"""Group member-mode actions switch

Revision ID: 0095_group_member_actions
Revises: 0094_llm_feature_routes
Create Date: 2026-10-07 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0095_group_member_actions"
down_revision: str | None = "0094_llm_feature_routes"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Additive with a server default: the previous image ignores the column.
    op.add_column(
        "chat_ai_characters",
        sa.Column("member_actions_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("chat_ai_characters", "member_actions_enabled")
