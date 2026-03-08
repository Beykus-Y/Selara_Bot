"""set growth size default to zero

Revision ID: 0010_growth_size_zero_default
Revises: 0009_add_growth_mechanic
Create Date: 2026-02-14 18:10:00
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_growth_size_zero_default"
down_revision: str | None = "0009_add_growth_mechanic"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_economy_accounts_growth_size_min", "economy_accounts", type_="check")
    op.create_check_constraint(
        "ck_economy_accounts_growth_size_min",
        "economy_accounts",
        "growth_size_mm >= 0",
    )

    op.execute("ALTER TABLE economy_accounts ALTER COLUMN growth_size_mm SET DEFAULT 0")
    op.execute("UPDATE economy_accounts SET growth_size_mm = 0 WHERE growth_actions = 0")


def downgrade() -> None:
    op.drop_constraint("ck_economy_accounts_growth_size_min", "economy_accounts", type_="check")
    op.create_check_constraint(
        "ck_economy_accounts_growth_size_min",
        "economy_accounts",
        "growth_size_mm >= 0",
    )
    op.execute("ALTER TABLE economy_accounts ALTER COLUMN growth_size_mm SET DEFAULT 0")
