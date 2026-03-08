"""add pair stage and relationship action cooldowns

Revision ID: 0015_relationships_pair_actions
Revises: 0014_group_text_aliases
Create Date: 2026-02-16 10:30:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015_relationships_pair_actions"
down_revision: str | None = "0014_group_text_aliases"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "relationship_proposals",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="marriage"),
    )
    op.create_check_constraint(
        "ck_relationship_proposals_kind",
        "relationship_proposals",
        "kind IN ('pair', 'marriage')",
    )
    op.create_index(
        "idx_relationship_proposals_pair_kind_status",
        "relationship_proposals",
        ["user_low_id", "user_high_id", "kind", "status"],
        unique=False,
    )

    op.create_table(
        "pairs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_low_id", sa.BigInteger(), nullable=False),
        sa.Column("user_high_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("paired_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("affection_points", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_affection_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_affection_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("user_low_id < user_high_id", name="ck_pairs_pair_order"),
        sa.CheckConstraint("affection_points >= 0", name="ck_pairs_affection_non_negative"),
        sa.ForeignKeyConstraint(["user_low_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_high_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["last_affection_by_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_pairs_pair", "pairs", ["user_low_id", "user_high_id"], unique=True)
    op.create_index("idx_pairs_user_low", "pairs", ["user_low_id"], unique=False)
    op.create_index("idx_pairs_user_high", "pairs", ["user_high_id"], unique=False)

    op.create_table(
        "relationship_action_usage",
        sa.Column("relationship_kind", sa.String(length=16), nullable=False),
        sa.Column("relationship_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("action_code", sa.String(length=32), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "relationship_kind IN ('pair', 'marriage')",
            name="ck_relationship_action_usage_kind",
        ),
        sa.CheckConstraint(
            "action_code IN ('care', 'date', 'gift', 'support', 'love')",
            name="ck_relationship_action_usage_code",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("relationship_kind", "relationship_id", "actor_user_id", "action_code"),
    )
    op.create_index(
        "idx_relationship_action_usage_lookup",
        "relationship_action_usage",
        ["relationship_kind", "relationship_id", "actor_user_id", "action_code"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_relationship_action_usage_lookup", table_name="relationship_action_usage")
    op.drop_table("relationship_action_usage")

    op.drop_index("idx_pairs_user_high", table_name="pairs")
    op.drop_index("idx_pairs_user_low", table_name="pairs")
    op.drop_index("uq_pairs_pair", table_name="pairs")
    op.drop_table("pairs")

    op.drop_index("idx_relationship_proposals_pair_kind_status", table_name="relationship_proposals")
    op.drop_constraint("ck_relationship_proposals_kind", "relationship_proposals", type_="check")
    op.drop_column("relationship_proposals", "kind")
