"""isolate relationships per chat

Revision ID: 0022_rel_per_chat
Revises: 0021_web_panel_auth
Create Date: 2026-03-06 23:40:00
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022_rel_per_chat"
down_revision: str | None = "0021_web_panel_auth"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_pairs_pair", table_name="pairs")
    op.drop_index("uq_marriages_pair", table_name="marriages")

    op.create_index(
        "uq_pairs_chat_pair",
        "pairs",
        ["chat_id", "user_low_id", "user_high_id"],
        unique=True,
    )
    op.create_index(
        "uq_marriages_chat_pair",
        "marriages",
        ["chat_id", "user_low_id", "user_high_id"],
        unique=True,
    )

    op.drop_index("idx_relationship_proposals_pair_status", table_name="relationship_proposals")
    op.drop_index("idx_relationship_proposals_pair_kind_status", table_name="relationship_proposals")
    op.create_index(
        "idx_relationship_proposals_chat_pair_status",
        "relationship_proposals",
        ["chat_id", "user_low_id", "user_high_id", "status"],
        unique=False,
    )
    op.create_index(
        "idx_relationship_proposals_chat_pair_kind_status",
        "relationship_proposals",
        ["chat_id", "user_low_id", "user_high_id", "kind", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_relationship_proposals_chat_pair_kind_status", table_name="relationship_proposals")
    op.drop_index("idx_relationship_proposals_chat_pair_status", table_name="relationship_proposals")
    op.create_index(
        "idx_relationship_proposals_pair_kind_status",
        "relationship_proposals",
        ["user_low_id", "user_high_id", "kind", "status"],
        unique=False,
    )
    op.create_index(
        "idx_relationship_proposals_pair_status",
        "relationship_proposals",
        ["user_low_id", "user_high_id", "status"],
        unique=False,
    )

    op.drop_index("uq_marriages_chat_pair", table_name="marriages")
    op.drop_index("uq_pairs_chat_pair", table_name="pairs")
    op.create_index("uq_marriages_pair", "marriages", ["user_low_id", "user_high_id"], unique=True)
    op.create_index("uq_pairs_pair", "pairs", ["user_low_id", "user_high_id"], unique=True)
