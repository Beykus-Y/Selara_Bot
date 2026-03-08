"""add mafia reveal eliminated role setting

Revision ID: 0005_mafia_reveal_role
Revises: 0004_add_mafia_chat_timers
Create Date: 2026-02-13 19:10:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_mafia_reveal_role"
down_revision: str | None = "0004_add_mafia_chat_timers"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column("mafia_reveal_eliminated_role", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("chat_settings", "mafia_reveal_eliminated_role")
