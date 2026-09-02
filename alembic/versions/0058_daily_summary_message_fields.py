"""add transcript/reply fields to messages for daily summary feature

Revision ID: 0058_daily_summary_messages
Revises: 0057_glossary_history_auth
Create Date: 2026-09-03 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0058_daily_summary_messages"
down_revision: str | None = "0057_glossary_history_auth"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("transcript", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("transcribed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("reply_to_telegram_message_id", sa.BigInteger(), nullable=True))
    op.create_index(
        "idx_messages_chat_reply_to",
        "messages",
        ["chat_id", "reply_to_telegram_message_id"],
        unique=False,
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute("CREATE INDEX idx_messages_text_trgm ON messages USING gin (text gin_trgm_ops)")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_messages_text_trgm")

    op.drop_index("idx_messages_chat_reply_to", table_name="messages")
    op.drop_column("messages", "reply_to_telegram_message_id")
    op.drop_column("messages", "transcribed_at")
    op.drop_column("messages", "transcript")
