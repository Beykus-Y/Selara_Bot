"""Group character: call names, Selara's character in a chat and member-mode dialogue

Member mode answers any member who addresses Selara by a chat call name. Its
dialogue lives in its own table so the admin assistant (``?``/``??``) and members
never see each other's questions.

Revision ID: 0090_group_ai_character
Revises: 0089_admin_model_config
Create Date: 2026-10-06 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0090_group_ai_character"
down_revision: str | None = "0089_admin_model_config"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_ai_characters",
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("character_preset", sa.String(32), nullable=False, server_default="default"),
        sa.Column("character_custom", sa.Text(), nullable=True),
        sa.Column("member_mode_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("member_history_access", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "updated_by_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "character_custom IS NULL OR length(character_custom) <= 500",
            name="ck_chat_ai_characters_custom_len",
        ),
    )

    op.create_table(
        "chat_ai_call_names",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name_display", sa.String(24), nullable=False),
        sa.Column("name_norm", sa.String(24), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_by_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("chat_id", "name_norm", name="uq_chat_ai_call_names_chat_name"),
    )
    # Exactly one primary name per chat; switching it happens in one transaction.
    op.create_index(
        "uq_chat_ai_call_names_primary",
        "chat_ai_call_names",
        ["chat_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )

    op.create_table(
        "chat_member_ai_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "author_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_member_ai_messages_role"),
        sa.CheckConstraint("status IN ('pending', 'ok', 'failed')", name="ck_chat_member_ai_messages_status"),
        sa.UniqueConstraint("idempotency_key", name="uq_chat_member_ai_messages_idempotency_key"),
    )
    op.create_index(
        "idx_chat_member_ai_messages_chat_created", "chat_member_ai_messages", ["chat_id", "created_at"]
    )
    op.create_index(
        "idx_chat_member_ai_messages_chat_telegram", "chat_member_ai_messages", ["chat_id", "telegram_message_id"]
    )
    op.create_index(
        "idx_chat_member_ai_messages_author_created",
        "chat_member_ai_messages",
        ["chat_id", "author_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_chat_member_ai_messages_author_created", table_name="chat_member_ai_messages")
    op.drop_index("idx_chat_member_ai_messages_chat_telegram", table_name="chat_member_ai_messages")
    op.drop_index("idx_chat_member_ai_messages_chat_created", table_name="chat_member_ai_messages")
    op.drop_table("chat_member_ai_messages")
    op.drop_index("uq_chat_ai_call_names_primary", table_name="chat_ai_call_names")
    op.drop_table("chat_ai_call_names")
    op.drop_table("chat_ai_characters")
