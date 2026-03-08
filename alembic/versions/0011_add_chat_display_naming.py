"""add per-chat display naming override

Revision ID: 0011_add_chat_display_naming
Revises: 0010_growth_size_zero_default
Create Date: 2026-02-14 20:45:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_add_chat_display_naming"
down_revision: str | None = "0010_growth_size_zero_default"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_chat_activity",
        sa.Column("display_name_override", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_chat_activity", "display_name_override")
