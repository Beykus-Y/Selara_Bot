"""add subscription_exempt to users

Revision ID: 0040_add_subscription_exempt
Revises: 0039_add_chat_rest_states
Create Date: 2026-04-04 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_add_subscription_exempt"
down_revision: str | None = "0039_add_chat_rest_states"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "subscription_exempt",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "subscription_exempt")
