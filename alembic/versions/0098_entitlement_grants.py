"""Owner-made subscription grants: an immutable journal

Revision ID: 0098_entitlement_grants
Revises: 0097_pets_on_by_default
Create Date: 2026-10-08 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0098_entitlement_grants"
down_revision: str | None = "0097_pets_on_by_default"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entitlement_grants",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(8), nullable=False),
        sa.Column("target_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("target_user_id", sa.BigInteger(), nullable=True),
        sa.Column("product_key", sa.String(64), nullable=False),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("delta_seconds", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("valid_until_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_before", sa.String(16), nullable=True),
        sa.Column("status_after", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("notified", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="uq_entitlement_grants_idempotency_key"),
        sa.CheckConstraint("scope IN ('chat', 'user')", name="ck_entitlement_grants_scope"),
        sa.CheckConstraint(
            "(scope = 'chat' AND target_chat_id IS NOT NULL AND target_user_id IS NULL"
            " AND product_key = 'selara_ai_monthly')"
            " OR (scope = 'user' AND target_user_id IS NOT NULL AND target_chat_id IS NULL"
            " AND product_key = 'selara_personal_monthly')",
            name="ck_entitlement_grants_target",
        ),
        sa.CheckConstraint("action IN ('grant', 'extend', 'revoke', 'shorten')", name="ck_entitlement_grants_action"),
        sa.CheckConstraint("delta_seconds >= 0", name="ck_entitlement_grants_delta"),
        sa.CheckConstraint("status_after IN ('active', 'revoked')", name="ck_entitlement_grants_status"),
        sa.CheckConstraint("source IN ('miniapp', 'command', 'admin_panel')", name="ck_entitlement_grants_source"),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 300", name="ck_entitlement_grants_reason"),
    )
    op.create_index("idx_entitlement_grants_user", "entitlement_grants", ["target_user_id", "created_at"])
    op.create_index("idx_entitlement_grants_chat", "entitlement_grants", ["target_chat_id", "created_at"])
    op.create_index("idx_entitlement_grants_created", "entitlement_grants", ["created_at"])


def downgrade() -> None:
    op.drop_table("entitlement_grants")
