"""add chat settings

Revision ID: 0003_add_chat_settings
Revises: 0002_add_engagement_tables
Create Date: 2026-02-13 00:30:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_add_chat_settings"
down_revision: str | None = "0002_add_engagement_tables"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_settings",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("top_limit_default", sa.BigInteger(), nullable=False),
        sa.Column("top_limit_max", sa.BigInteger(), nullable=False),
        sa.Column("vote_daily_limit", sa.BigInteger(), nullable=False),
        sa.Column("leaderboard_hybrid_karma_weight", sa.Float(), nullable=False),
        sa.Column("leaderboard_hybrid_activity_weight", sa.Float(), nullable=False),
        sa.Column("leaderboard_7d_days", sa.BigInteger(), nullable=False),
        sa.Column("text_commands_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("text_commands_locale", sa.String(length=8), nullable=False, server_default="ru"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id"),
    )


def downgrade() -> None:
    op.drop_table("chat_settings")
