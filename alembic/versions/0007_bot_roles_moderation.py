"""add bot roles and moderation states

Revision ID: 0007_bot_roles_mod
Revises: 0006_announce_prefs
Create Date: 2026-02-13 21:10:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_bot_roles_mod"
down_revision: str | None = "0006_announce_prefs"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_chat_bot_roles",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("assigned_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("role IN ('owner', 'admin', 'moderator', 'helper')", name="ck_user_chat_bot_roles_role"),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id", "user_id"),
    )
    op.create_index(
        "idx_user_chat_bot_roles_chat_role",
        "user_chat_bot_roles",
        ["chat_id", "role"],
        unique=False,
    )

    op.create_table(
        "user_chat_moderation_states",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("pending_preds", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("warn_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_preds", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_warns", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_bans", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("is_banned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_reason", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id", "user_id"),
    )
    op.create_index(
        "idx_user_chat_moderation_states_chat_banned",
        "user_chat_moderation_states",
        ["chat_id", "is_banned"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_user_chat_moderation_states_chat_banned", table_name="user_chat_moderation_states")
    op.drop_table("user_chat_moderation_states")
    op.drop_index("idx_user_chat_bot_roles_chat_role", table_name="user_chat_bot_roles")
    op.drop_table("user_chat_bot_roles")

