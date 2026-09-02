"""add daily_summary_* settings to chat_settings

Revision ID: 0059_daily_summary_settings
Revises: 0058_daily_summary_messages
Create Date: 2026-09-03 00:00:01
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059_daily_summary_settings"
down_revision: str | None = "0058_daily_summary_messages"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column("daily_summary_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "chat_settings",
        sa.Column("daily_summary_hour", sa.SmallInteger(), nullable=False, server_default="3"),
    )
    op.add_column(
        "chat_settings",
        sa.Column("daily_summary_min_messages", sa.BigInteger(), nullable=False, server_default="50"),
    )
    op.add_column(
        "chat_settings",
        sa.Column("daily_summary_style", sa.String(length=16), nullable=False, server_default="neutral"),
    )
    op.add_column(
        "chat_settings",
        sa.Column("daily_summary_include_voice", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "chat_settings",
        sa.Column("daily_summary_include_video_notes", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("chat_settings", "daily_summary_include_video_notes")
    op.drop_column("chat_settings", "daily_summary_include_voice")
    op.drop_column("chat_settings", "daily_summary_style")
    op.drop_column("chat_settings", "daily_summary_min_messages")
    op.drop_column("chat_settings", "daily_summary_hour")
    op.drop_column("chat_settings", "daily_summary_enabled")
