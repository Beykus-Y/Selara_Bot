"""add announcement subscriptions

Revision ID: 0006_announce_prefs
Revises: 0005_mafia_reveal_role
Create Date: 2026-02-13 20:20:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_announce_prefs"
down_revision: str | None = "0005_mafia_reveal_role"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_chat_announce_subscriptions",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id", "user_id"),
    )

    op.create_index(
        "idx_user_chat_announce_subscriptions_chat_enabled",
        "user_chat_announce_subscriptions",
        ["chat_id", "is_enabled"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_user_chat_announce_subscriptions_chat_enabled", table_name="user_chat_announce_subscriptions")
    op.drop_table("user_chat_announce_subscriptions")

