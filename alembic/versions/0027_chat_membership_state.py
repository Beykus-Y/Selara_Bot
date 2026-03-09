"""add membership state to user chat activity

Revision ID: 0027_chat_membership_state
Revises: 0026_family_web_econ
Create Date: 2026-03-09 12:00:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027_chat_membership_state"
down_revision: str | None = "0026_family_web_econ"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_chat_activity",
        sa.Column("is_active_member", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index(
        "idx_user_chat_activity_chat_active",
        "user_chat_activity",
        ["chat_id", "is_active_member"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_user_chat_activity_chat_active", table_name="user_chat_activity")
    op.drop_column("user_chat_activity", "is_active_member")
