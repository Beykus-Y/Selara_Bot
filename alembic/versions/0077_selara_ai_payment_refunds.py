"""add an audited, single-attempt refund record for rejected Stars payments

Revision ID: 0077_selara_ai_payment_refunds
Revises: 0076_selara_ai_purchase_terms
Create Date: 2026-10-05 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0077_selara_ai_payment_refunds"
down_revision: str | None = "0076_selara_ai_purchase_terms"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "selara_ai_payment_refunds",
        sa.Column(
            "payment_id",
            sa.BigInteger(),
            sa.ForeignKey("selara_ai_payments.id", ondelete="RESTRICT"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("requested_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'refunded', 'failed')",
            name="ck_selara_ai_payment_refunds_status",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND completed_at IS NULL) OR "
            "(status IN ('refunded', 'failed') AND completed_at IS NOT NULL)",
            name="ck_selara_ai_payment_refunds_completion",
        ),
    )


def downgrade() -> None:
    op.drop_table("selara_ai_payment_refunds")
