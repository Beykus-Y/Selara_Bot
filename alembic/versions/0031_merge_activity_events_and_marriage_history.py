"""merge activity events and marriage history heads

Revision ID: 0031_merge_activity_heads
Revises: 0030_add_activity_event_tables, 0030_marriage_history
Create Date: 2026-03-13 16:10:00
"""

from typing import Sequence

# revision identifiers, used by Alembic.
revision: str = "0031_merge_activity_heads"
down_revision: tuple[str, str] | None = ("0030_add_activity_event_tables", "0030_marriage_history")
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
