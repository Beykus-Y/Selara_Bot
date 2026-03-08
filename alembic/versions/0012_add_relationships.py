"""add relationships and marriages

Revision ID: 0012_add_relationships
Revises: 0011_add_chat_display_naming
Create Date: 2026-02-14 21:20:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_add_relationships"
down_revision: str | None = "0011_add_chat_display_naming"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "relationship_proposals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("proposer_user_id", sa.BigInteger(), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=False),
        sa.Column("user_low_id", sa.BigInteger(), nullable=False),
        sa.Column("user_high_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'cancelled', 'expired')",
            name="ck_relationship_proposals_status",
        ),
        sa.CheckConstraint("user_low_id < user_high_id", name="ck_relationship_proposals_pair_order"),
        sa.ForeignKeyConstraint(["proposer_user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_low_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_high_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_relationship_proposals_target_status",
        "relationship_proposals",
        ["target_user_id", "status"],
        unique=False,
    )
    op.create_index(
        "idx_relationship_proposals_pair_status",
        "relationship_proposals",
        ["user_low_id", "user_high_id", "status"],
        unique=False,
    )

    op.create_table(
        "marriages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_low_id", sa.BigInteger(), nullable=False),
        sa.Column("user_high_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("married_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("affection_points", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_affection_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_affection_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("user_low_id < user_high_id", name="ck_marriages_pair_order"),
        sa.CheckConstraint("affection_points >= 0", name="ck_marriages_affection_non_negative"),
        sa.ForeignKeyConstraint(["user_low_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_high_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["last_affection_by_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_marriages_pair", "marriages", ["user_low_id", "user_high_id"], unique=True)
    op.create_index("idx_marriages_user_low", "marriages", ["user_low_id"], unique=False)
    op.create_index("idx_marriages_user_high", "marriages", ["user_high_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_marriages_user_high", table_name="marriages")
    op.drop_index("idx_marriages_user_low", table_name="marriages")
    op.drop_index("uq_marriages_pair", table_name="marriages")
    op.drop_table("marriages")

    op.drop_index("idx_relationship_proposals_pair_status", table_name="relationship_proposals")
    op.drop_index("idx_relationship_proposals_target_status", table_name="relationship_proposals")
    op.drop_table("relationship_proposals")
