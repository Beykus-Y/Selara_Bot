"""add chat-scoped feature quota history

Revision ID: 0073_feature_access_quotas
Revises: 0072_ai_accounting_foundation
Create Date: 2026-10-05 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0073_feature_access_quotas"
down_revision: str | None = "0072_ai_accounting_foundation"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_feature_quota_usage",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("invocation_id", sa.BigInteger(), nullable=True),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_key", sa.String(length=64), nullable=False),
        sa.Column("quota_limit", sa.Integer(), nullable=False),
        sa.Column("access_tier", sa.String(length=32), nullable=False),
        sa.Column("owner_exempt", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="consumed"),
        sa.Column("release_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("period_start < period_end", name="ck_ai_feature_quota_period_bounds"),
        sa.CheckConstraint("quota_limit > 0", name="ck_ai_feature_quota_positive_limit"),
        sa.CheckConstraint("status IN ('consumed', 'released')", name="ck_ai_feature_quota_status"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["invocation_id"], ["ai_feature_invocations.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("idempotency_key", name="uq_ai_feature_quota_idempotency"),
        sa.UniqueConstraint("invocation_id", name="uq_ai_feature_quota_invocation"),
    )
    op.create_index(
        "idx_ai_feature_quota_period_usage",
        "ai_feature_quota_usage",
        ["feature", "chat_id", "period_start", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_ai_feature_quota_period_usage", table_name="ai_feature_quota_usage")
    op.drop_table("ai_feature_quota_usage")
