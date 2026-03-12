"""add marriage history fields

Revision ID: 0030_marriage_history
Revises: 0029_add_iris_import_tables
Create Date: 2026-03-12 18:10:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030_marriage_history"
down_revision: str | None = "0029_add_iris_import_tables"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "marriages",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "marriages",
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "marriages",
        sa.Column("ended_by_user_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "marriages",
        sa.Column("ended_reason", sa.String(length=32), nullable=True),
    )
    op.create_foreign_key(
        "fk_marriages_ended_by_user_id_users",
        "marriages",
        "users",
        ["ended_by_user_id"],
        ["telegram_user_id"],
        ondelete="SET NULL",
    )

    op.drop_index("uq_marriages_chat_pair", table_name="marriages")
    op.create_index(
        "uq_marriages_chat_pair",
        "marriages",
        ["chat_id", "user_low_id", "user_high_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM marriages WHERE is_active = false")
    op.drop_index("uq_marriages_chat_pair", table_name="marriages")
    op.create_index(
        "uq_marriages_chat_pair",
        "marriages",
        ["chat_id", "user_low_id", "user_high_id"],
        unique=True,
    )
    op.drop_constraint("fk_marriages_ended_by_user_id_users", "marriages", type_="foreignkey")
    op.drop_column("marriages", "ended_reason")
    op.drop_column("marriages", "ended_by_user_id")
    op.drop_column("marriages", "ended_at")
    op.drop_column("marriages", "is_active")
