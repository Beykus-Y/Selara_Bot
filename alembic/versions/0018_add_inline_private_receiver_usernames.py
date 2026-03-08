"""add receiver usernames to inline private messages

Revision ID: 0018_inline_pm_rcv_usernames
Revises: 0017_inline_private_messages
Create Date: 2026-02-18 22:30:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018_inline_pm_rcv_usernames"
down_revision: str | None = "0017_inline_private_messages"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "inline_private_messages",
        sa.Column(
            "receiver_usernames",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.alter_column("inline_private_messages", "receiver_usernames", server_default=None)


def downgrade() -> None:
    op.drop_column("inline_private_messages", "receiver_usernames")
