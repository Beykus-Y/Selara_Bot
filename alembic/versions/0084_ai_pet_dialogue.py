"""AI pet dialogue: per-chat talk history and dialogue memory notes

Behaviour aggregates ("Лиза гладила меня 5 раз") are computed from
``ai_pet_events`` when the pet speaks, so only dialogue notes are stored.
Both tables are scoped to (pet, chat): a pet never recalls one chat in another.

Revision ID: 0084_ai_pet_dialogue
Revises: 0083_ai_pets
Create Date: 2026-10-06 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0084_ai_pet_dialogue"
down_revision: str | None = "0083_ai_pets"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_pet_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pet_id", sa.BigInteger(), sa.ForeignKey("ai_pets.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "author_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("author_is_owner", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_ai_pet_messages_role"),
        sa.CheckConstraint("status IN ('pending', 'ok', 'failed')", name="ck_ai_pet_messages_status"),
        sa.UniqueConstraint("idempotency_key", name="uq_ai_pet_messages_idempotency_key"),
    )
    op.create_index("idx_ai_pet_messages_pet_chat_created", "ai_pet_messages", ["pet_id", "chat_id", "created_at"])
    op.create_index("idx_ai_pet_messages_chat_telegram", "ai_pet_messages", ["chat_id", "telegram_message_id"])

    op.create_table(
        "ai_pet_memories",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pet_id", sa.BigInteger(), sa.ForeignKey("ai_pets.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "subject_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("content", sa.String(200), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="dialogue"),
        sa.Column("weight", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source IN ('aggregate', 'dialogue')", name="ck_ai_pet_memories_source"),
    )
    op.create_index("idx_ai_pet_memories_pet_chat_created", "ai_pet_memories", ["pet_id", "chat_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_ai_pet_memories_pet_chat_created", table_name="ai_pet_memories")
    op.drop_table("ai_pet_memories")
    op.drop_index("idx_ai_pet_messages_chat_telegram", table_name="ai_pet_messages")
    op.drop_index("idx_ai_pet_messages_pet_chat_created", table_name="ai_pet_messages")
    op.drop_table("ai_pet_messages")
