"""store the terms accepted for Selara AI purchases

Revision ID: 0076_selara_ai_purchase_terms
Revises: 0075_telegram_stars_selara_ai
Create Date: 2026-10-05 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0076_selara_ai_purchase_terms"
down_revision: str | None = "0075_telegram_stars_selara_ai"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "selara_ai_purchase_intents",
        sa.Column("terms_version", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "selara_ai_purchase_intents",
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_selara_ai_purchase_intents_terms_acceptance",
        "selara_ai_purchase_intents",
        "(terms_version IS NULL AND terms_accepted_at IS NULL) OR "
        "(terms_version IS NOT NULL AND terms_accepted_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_selara_ai_purchase_intents_terms_acceptance",
        "selara_ai_purchase_intents",
        type_="check",
    )
    op.drop_column("selara_ai_purchase_intents", "terms_accepted_at")
    op.drop_column("selara_ai_purchase_intents", "terms_version")
