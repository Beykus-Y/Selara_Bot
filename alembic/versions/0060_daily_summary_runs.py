"""add daily_summary_runs table (atomic claim/lease for the daily summary feature)

Revision ID: 0060_daily_summary_runs
Revises: 0059_daily_summary_settings
Create Date: 2026-09-03 00:00:02
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0060_daily_summary_runs"
down_revision: str | None = "0059_daily_summary_settings"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_summary_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("summary_date", sa.Date(), nullable=False),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="claimed"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_text", sa.Text(), nullable=True),
        sa.Column("topics_json", sa.JSON(), nullable=True),
        sa.Column("pipeline_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("context_stt_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint("trigger IN ('scheduled', 'manual')", name="ck_daily_summary_runs_trigger"),
        sa.CheckConstraint(
            "status IN ('claimed', 'generating', 'generated', 'sent', 'send_failed', 'failed')",
            name="ck_daily_summary_runs_status",
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("chat_id", "summary_date", "trigger", name="uq_daily_summary_runs_chat_date_trigger"),
    )


def downgrade() -> None:
    op.drop_table("daily_summary_runs")
