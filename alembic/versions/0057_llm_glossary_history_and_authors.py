"""add glossary author tracking and version history (#17, #18)

Revision ID: 0057_glossary_history_auth
Revises: 0056_gacha_variant_cache_version
Create Date: 2026-08-20 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0057_glossary_history_auth"
down_revision: str | None = "0056_gacha_variant_cache_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_chat_glossary",
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "llm_chat_glossary",
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_llm_chat_glossary_created_by",
        "llm_chat_glossary", "users",
        ["created_by_user_id"], ["telegram_user_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_llm_chat_glossary_updated_by",
        "llm_chat_glossary", "users",
        ["updated_by_user_id"], ["telegram_user_id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "llm_chat_glossary_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("term", sa.String(256), nullable=False),
        sa.Column("previous_definition", sa.Text(), nullable=False),
        sa.Column("changed_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["users.telegram_user_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_llm_glossary_history_chat_term", "llm_chat_glossary_history", ["chat_id", "term"],
    )


def downgrade() -> None:
    op.drop_index("idx_llm_glossary_history_chat_term", table_name="llm_chat_glossary_history")
    op.drop_table("llm_chat_glossary_history")
    op.drop_constraint("fk_llm_chat_glossary_updated_by", "llm_chat_glossary", type_="foreignkey")
    op.drop_constraint("fk_llm_chat_glossary_created_by", "llm_chat_glossary", type_="foreignkey")
    op.drop_column("llm_chat_glossary", "updated_by_user_id")
    op.drop_column("llm_chat_glossary", "created_by_user_id")
