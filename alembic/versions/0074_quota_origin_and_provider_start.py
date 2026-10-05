"""preserve quota request origin and provider start evidence

Revision ID: 0074_quota_origin_provider_start
Revises: 0073_feature_access_quotas
Create Date: 2026-10-05 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0074_quota_origin_provider_start"
down_revision: str | None = "0073_feature_access_quotas"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_feature_quota_usage",
        sa.Column("source_chat_id", sa.BigInteger(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE ai_feature_quota_usage "
            "SET source_chat_id = chat_id "
            "WHERE source_message_id IS NOT NULL AND source_chat_id IS NULL"
        )
    )
    op.add_column(
        "ai_feature_invocations",
        sa.Column("provider_attempt_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_feature_invocations", "provider_attempt_started_at")
    op.drop_column("ai_feature_quota_usage", "source_chat_id")
