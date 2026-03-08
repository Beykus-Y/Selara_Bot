"""extend relationship action codes

Revision ID: 0016_rel_action_codes
Revises: 0015_relationships_pair_actions
Create Date: 2026-02-16 21:30:00
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016_rel_action_codes"
down_revision: str | None = "0015_relationships_pair_actions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_relationship_action_usage_code", "relationship_action_usage", type_="check")
    op.create_check_constraint(
        "ck_relationship_action_usage_code",
        "relationship_action_usage",
        "action_code IN ('care', 'date', 'gift', 'support', 'love', 'flirt', 'surprise', 'vow')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_relationship_action_usage_code", "relationship_action_usage", type_="check")
    op.create_check_constraint(
        "ck_relationship_action_usage_code",
        "relationship_action_usage",
        "action_code IN ('care', 'date', 'gift', 'support', 'love')",
    )
