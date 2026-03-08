"""add 18+ social actions setting

Revision ID: 0013_actions_18_setting
Revises: 0012_add_relationships
Create Date: 2026-02-15 20:45:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_actions_18_setting"
down_revision: str | None = "0012_add_relationships"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column("actions_18_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("chat_settings", "actions_18_enabled")
