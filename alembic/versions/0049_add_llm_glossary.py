"""add llm chat glossary table

Revision ID: 0049_add_llm_glossary
Revises: 0048_add_llm_tables
Create Date: 2026-06-20 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_add_llm_glossary"
down_revision: str | None = "0048_add_llm_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_chat_glossary",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("term", sa.String(256), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.telegram_chat_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "term", name="uq_llm_glossary_chat_term"),
    )
    op.create_index("idx_llm_glossary_chat", "llm_chat_glossary", ["chat_id"])


def downgrade() -> None:
    op.drop_index("idx_llm_glossary_chat", table_name="llm_chat_glossary")
    op.drop_table("llm_chat_glossary")
