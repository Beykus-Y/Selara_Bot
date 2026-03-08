"""add per-chat user profiles and awards

Revision ID: 0025_user_profiles_awards
Revises: 0024_hybrid_top_buttons
Create Date: 2026-03-08 18:20:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025_user_profiles_awards"
down_revision: str | None = "0024_hybrid_top_buttons"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_chat_profiles",
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("description_text", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("chat_id", "user_id"),
    )
    op.create_index("idx_user_chat_profiles_chat_user", "user_chat_profiles", ["chat_id", "user_id"])

    op.create_table(
        "user_chat_awards",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("granted_by_user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_user_chat_awards_chat_user_created",
        "user_chat_awards",
        ["chat_id", "user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_user_chat_awards_chat_user_created", table_name="user_chat_awards")
    op.drop_table("user_chat_awards")
    op.drop_index("idx_user_chat_profiles_chat_user", table_name="user_chat_profiles")
    op.drop_table("user_chat_profiles")
