"""add growth mechanic state

Revision ID: 0009_add_growth_mechanic
Revises: 0008_add_economy_core
Create Date: 2026-02-14 16:30:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_add_growth_mechanic"
down_revision: str | None = "0008_add_economy_core"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "economy_accounts",
        sa.Column("growth_size_mm", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "economy_accounts",
        sa.Column("growth_stress_pct", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "economy_accounts",
        sa.Column("growth_actions", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "economy_accounts",
        sa.Column("last_growth_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "economy_accounts",
        sa.Column("growth_boost_pct", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "economy_accounts",
        sa.Column("growth_cooldown_discount_seconds", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )

    op.create_check_constraint(
        "ck_economy_accounts_growth_size_min",
        "economy_accounts",
        "growth_size_mm >= 0",
    )
    op.create_check_constraint(
        "ck_economy_accounts_growth_stress_range",
        "economy_accounts",
        "growth_stress_pct >= 0 AND growth_stress_pct <= 100",
    )
    op.create_check_constraint(
        "ck_economy_accounts_growth_actions_non_negative",
        "economy_accounts",
        "growth_actions >= 0",
    )
    op.create_check_constraint(
        "ck_economy_accounts_growth_boost_non_negative",
        "economy_accounts",
        "growth_boost_pct >= 0",
    )
    op.create_check_constraint(
        "ck_economy_accounts_growth_cd_discount_non_negative",
        "economy_accounts",
        "growth_cooldown_discount_seconds >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_economy_accounts_growth_cd_discount_non_negative", "economy_accounts", type_="check")
    op.drop_constraint("ck_economy_accounts_growth_boost_non_negative", "economy_accounts", type_="check")
    op.drop_constraint("ck_economy_accounts_growth_actions_non_negative", "economy_accounts", type_="check")
    op.drop_constraint("ck_economy_accounts_growth_stress_range", "economy_accounts", type_="check")
    op.drop_constraint("ck_economy_accounts_growth_size_min", "economy_accounts", type_="check")

    op.drop_column("economy_accounts", "growth_cooldown_discount_seconds")
    op.drop_column("economy_accounts", "growth_boost_pct")
    op.drop_column("economy_accounts", "last_growth_at")
    op.drop_column("economy_accounts", "growth_actions")
    op.drop_column("economy_accounts", "growth_stress_pct")
    op.drop_column("economy_accounts", "growth_size_mm")
