"""add iris import tables

Revision ID: 0029_add_iris_import_tables
Revises: 0028_add_achievements
Create Date: 2026-03-12 12:00:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029_add_iris_import_tables"
down_revision: str | None = "0028_add_achievements"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_chat_iris_import_state",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("source_bot_username", sa.String(length=64), nullable=False),
        sa.Column("source_target_username", sa.String(length=255), nullable=False),
        sa.Column("karma_base_all_time", sa.BigInteger(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["imported_by_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chat_id", "user_id"),
    )
    op.create_index(
        "idx_user_chat_iris_import_state_chat_user",
        "user_chat_iris_import_state",
        ["chat_id", "user_id"],
        unique=False,
    )

    op.create_table(
        "user_chat_iris_import_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("imported_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("raw_profile_text", sa.Text(), nullable=False),
        sa.Column("raw_awards_text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_user_chat_iris_import_history_chat_user_imported",
        "user_chat_iris_import_history",
        ["chat_id", "user_id", "imported_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_user_chat_iris_import_history_chat_user_imported", table_name="user_chat_iris_import_history")
    op.drop_table("user_chat_iris_import_history")
    op.drop_index("idx_user_chat_iris_import_state_chat_user", table_name="user_chat_iris_import_state")
    op.drop_table("user_chat_iris_import_state")
