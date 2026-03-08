"""add group text aliases

Revision ID: 0014_group_text_aliases
Revises: 0013_actions_18_setting
Create Date: 2026-02-15 22:40:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_group_text_aliases"
down_revision: str | None = "0013_actions_18_setting"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_text_alias_settings",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="both"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "mode IN ('aliases_if_exists', 'both', 'standard_only')",
            name="ck_chat_text_alias_settings_mode",
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id"),
    )

    op.create_table(
        "chat_text_aliases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("command_key", sa.String(length=64), nullable=False),
        sa.Column("alias_text_norm", sa.String(length=128), nullable=False),
        sa.Column("source_trigger_norm", sa.String(length=128), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_chat_text_aliases_chat_alias",
        "chat_text_aliases",
        ["chat_id", "alias_text_norm"],
        unique=True,
    )
    op.create_index(
        "idx_chat_text_aliases_chat_command",
        "chat_text_aliases",
        ["chat_id", "command_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_chat_text_aliases_chat_command", table_name="chat_text_aliases")
    op.drop_index("uq_chat_text_aliases_chat_alias", table_name="chat_text_aliases")
    op.drop_table("chat_text_aliases")
    op.drop_table("chat_text_alias_settings")
