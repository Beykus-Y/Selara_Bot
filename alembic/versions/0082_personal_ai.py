"""Personal AI in private chats: character profile, dialogue history and summaries

Revision ID: 0082_personal_ai
Revises: 0081_selara_personal_config
Create Date: 2026-10-06 00:00:03
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0082_personal_ai"
down_revision: str | None = "0081_selara_personal_config"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_USER_FK = "users.telegram_user_id"


def upgrade() -> None:
    op.create_table(
        "personal_ai_profiles",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey(_USER_FK, ondelete="CASCADE"), primary_key=True),
        sa.Column("display_name", sa.String(32), nullable=False, server_default="Selara"),
        sa.Column("character_preset", sa.String(32), nullable=False, server_default="assistant"),
        sa.Column("character_custom", sa.Text(), nullable=True),
        sa.Column("address_form", sa.String(64), nullable=True),
        sa.Column("formality", sa.String(8), nullable=False, server_default="ty"),
        sa.Column("reply_length", sa.String(8), nullable=False, server_default="medium"),
        sa.Column("emoji_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("mode", sa.String(16), nullable=False, server_default="assistant"),
        sa.Column("memory_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "character_custom IS NULL OR length(character_custom) <= 500",
            name="ck_personal_ai_profiles_custom_len",
        ),
        sa.CheckConstraint("formality IN ('ty', 'vy')", name="ck_personal_ai_profiles_formality"),
        sa.CheckConstraint("reply_length IN ('short', 'medium', 'long')", name="ck_personal_ai_profiles_reply_length"),
        sa.CheckConstraint("mode IN ('assistant', 'roleplay')", name="ck_personal_ai_profiles_mode"),
    )
    op.create_table(
        "personal_ai_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey(_USER_FK, ondelete="CASCADE"), nullable=False),
        sa.Column("thread", sa.String(16), nullable=False, server_default="assistant"),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("compressed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_personal_ai_messages_role"),
        sa.CheckConstraint("thread IN ('assistant', 'roleplay')", name="ck_personal_ai_messages_thread"),
    )
    op.create_index(
        "idx_personal_ai_messages_user_thread_created",
        "personal_ai_messages",
        ["user_id", "thread", "created_at"],
    )
    op.create_table(
        "personal_ai_summaries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey(_USER_FK, ondelete="CASCADE"), nullable=False),
        sa.Column("thread", sa.String(16), nullable=False, server_default="assistant"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("messages_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("thread IN ('assistant', 'roleplay')", name="ck_personal_ai_summaries_thread"),
    )
    op.create_index(
        "idx_personal_ai_summaries_user_thread_period",
        "personal_ai_summaries",
        ["user_id", "thread", "period_end"],
    )


def downgrade() -> None:
    # The tables hold users' private dialogue; refuse to drop them silently (like 0079 does for paid data).
    bind = op.get_bind()
    for table in ("personal_ai_profiles", "personal_ai_messages", "personal_ai_summaries"):
        if bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError(
                f"Cannot downgrade 0082_personal_ai: {table} holds users' private data; export or delete it first"
            )
    op.drop_index("idx_personal_ai_summaries_user_thread_period", table_name="personal_ai_summaries")
    op.drop_table("personal_ai_summaries")
    op.drop_index("idx_personal_ai_messages_user_thread_created", table_name="personal_ai_messages")
    op.drop_table("personal_ai_messages")
    op.drop_table("personal_ai_profiles")
