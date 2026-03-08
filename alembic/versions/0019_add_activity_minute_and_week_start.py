"""add minute activity aggregate and week start settings

Revision ID: 0019_activity_minute_week_start
Revises: 0018_inline_pm_rcv_usernames
Create Date: 2026-02-22 18:10:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019_activity_minute_week_start"
down_revision: str | None = "0018_inline_pm_rcv_usernames"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_chat_activity_minute",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("activity_minute", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id", "user_id", "activity_minute"),
    )
    op.create_index(
        "idx_user_chat_activity_minute_chat_minute",
        "user_chat_activity_minute",
        ["chat_id", "activity_minute"],
        unique=False,
    )

    op.add_column(
        "chat_settings",
        sa.Column(
            "leaderboard_week_start_weekday",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "chat_settings",
        sa.Column(
            "leaderboard_week_start_hour",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_settings", "leaderboard_week_start_hour")
    op.drop_column("chat_settings", "leaderboard_week_start_weekday")

    op.drop_index("idx_user_chat_activity_minute_chat_minute", table_name="user_chat_activity_minute")
    op.drop_table("user_chat_activity_minute")
