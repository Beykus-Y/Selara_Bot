"""add interesting facts settings and state

Revision ID: 0036_add_interesting_facts
Revises: 0035_antiraid_chat_lock
Create Date: 2026-03-19 21:30:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0036_add_interesting_facts"
down_revision: str | None = "0035_antiraid_chat_lock"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column("interesting_facts_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("interesting_facts_interval_minutes", sa.BigInteger(), nullable=False, server_default="180"),
    )
    op.add_column(
        "chat_settings",
        sa.Column("interesting_facts_target_messages", sa.BigInteger(), nullable=False, server_default="150"),
    )
    op.add_column(
        "chat_settings",
        sa.Column("interesting_facts_sleep_cap_minutes", sa.BigInteger(), nullable=False, server_default="1440"),
    )
    op.create_table(
        "chat_interesting_fact_state",
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fact_id", sa.String(length=64), nullable=True),
        sa.Column("used_fact_ids_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("chat_id"),
    )


def downgrade() -> None:
    op.drop_table("chat_interesting_fact_state")
    op.drop_column("chat_settings", "interesting_facts_sleep_cap_minutes")
    op.drop_column("chat_settings", "interesting_facts_target_messages")
    op.drop_column("chat_settings", "interesting_facts_interval_minutes")
    op.drop_column("chat_settings", "interesting_facts_enabled")
