"""add inline private messages storage

Revision ID: 0017_inline_private_messages
Revises: 0016_rel_action_codes
Create Date: 2026-02-18 21:10:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017_inline_private_messages"
down_revision: str | None = "0016_rel_action_codes"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inline_private_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("chat_instance", sa.String(length=255), nullable=True),
        sa.Column("sender_id", sa.BigInteger(), nullable=False),
        sa.Column("receiver_ids", sa.JSON(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sender_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_inline_private_messages_sender_created",
        "inline_private_messages",
        ["sender_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_inline_private_messages_chat_instance",
        "inline_private_messages",
        ["chat_instance"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_inline_private_messages_chat_instance", table_name="inline_private_messages")
    op.drop_index("idx_inline_private_messages_sender_created", table_name="inline_private_messages")
    op.drop_table("inline_private_messages")
