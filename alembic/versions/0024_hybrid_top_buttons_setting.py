"""add hybrid top inline buttons setting

Revision ID: 0024_hybrid_top_buttons
Revises: 0023_chat_assistant
Create Date: 2026-03-08 16:35:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024_hybrid_top_buttons"
down_revision: str | None = "0023_chat_assistant"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column("leaderboard_hybrid_buttons_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("chat_settings", "leaderboard_hybrid_buttons_enabled")
