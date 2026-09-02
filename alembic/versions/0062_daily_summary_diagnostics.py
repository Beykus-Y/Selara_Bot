"""add diagnostics_json to daily_summary_runs (beta observability, docs/DAILY_SUMMARY_TODO.md)

Revision ID: 0062_daily_summary_diag
Revises: 0061_llm_usage_log
Create Date: 2026-09-03 00:00:04
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062_daily_summary_diag"
down_revision: str | None = "0061_llm_usage_log"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("daily_summary_runs", sa.Column("diagnostics_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("daily_summary_runs", "diagnostics_json")
