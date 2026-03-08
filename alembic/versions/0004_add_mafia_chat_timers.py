"""add per-chat mafia timer settings

Revision ID: 0004_add_mafia_chat_timers
Revises: 0003_add_chat_settings
Create Date: 2026-02-13 18:20:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_add_mafia_chat_timers"
down_revision: str | None = "0003_add_chat_settings"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column("mafia_night_seconds", sa.BigInteger(), nullable=False, server_default=sa.text("90")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("mafia_day_seconds", sa.BigInteger(), nullable=False, server_default=sa.text("120")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("mafia_vote_seconds", sa.BigInteger(), nullable=False, server_default=sa.text("60")),
    )


def downgrade() -> None:
    op.drop_column("chat_settings", "mafia_vote_seconds")
    op.drop_column("chat_settings", "mafia_day_seconds")
    op.drop_column("chat_settings", "mafia_night_seconds")
