"""add llm_usage_log table (per-call cost accounting for the daily summary feature)

Revision ID: 0061_llm_usage_log
Revises: 0060_daily_summary_runs
Create Date: 2026-09-03 00:00:03
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061_llm_usage_log"
down_revision: str | None = "0060_daily_summary_runs"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_usage_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("summary_run_id", sa.BigInteger(), nullable=True),
        sa.Column("message_archive_id", sa.BigInteger(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("audio_seconds", sa.Numeric(10, 2), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["summary_run_id"], ["daily_summary_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_archive_id"], ["messages.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_llm_usage_log_summary_run", "llm_usage_log", ["summary_run_id"], unique=False)
    op.create_index("idx_llm_usage_log_message_archive", "llm_usage_log", ["message_archive_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_llm_usage_log_message_archive", table_name="llm_usage_log")
    op.drop_index("idx_llm_usage_log_summary_run", table_name="llm_usage_log")
    op.drop_table("llm_usage_log")
