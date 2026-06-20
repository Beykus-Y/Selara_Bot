"""add llm admin assistant tables

Revision ID: 0048_add_llm_tables
Revises: 0047_add_child_role
Create Date: 2026-06-20 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_add_llm_tables"
down_revision: str | None = "0047_add_child_role"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_settings", sa.Column("llm_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("chat_settings", sa.Column("llm_context_threshold", sa.BigInteger(), nullable=False, server_default="30"))

    op.create_table(
        "llm_context_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("admin_user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("tool_call_id", sa.String(64), nullable=True),
        sa.Column("compressed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_context", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("role IN ('user', 'assistant', 'tool')", name="ck_llm_context_messages_role"),
    )
    op.create_index("idx_llm_ctx_msgs_chat_created", "llm_context_messages", ["chat_id", "created_at"])
    op.create_index("idx_llm_ctx_msgs_chat_compressed", "llm_context_messages", ["chat_id", "compressed"])
    op.create_index("idx_llm_ctx_msgs_chat_is_context_compressed", "llm_context_messages", ["chat_id", "is_context", "compressed"])

    op.create_table(
        "llm_context_summaries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("messages_count", sa.Integer(), nullable=False),
        sa.Column("level", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_llm_ctx_summaries_chat_period", "llm_context_summaries", ["chat_id", "period_end"])
    op.create_index("idx_llm_ctx_summaries_chat_level", "llm_context_summaries", ["chat_id", "level"])

    op.create_table(
        "llm_admin_actions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("admin_user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("action_description", sa.Text(), nullable=False),
        sa.Column("undo_payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_by_user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_user_id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("idx_llm_admin_actions_chat_created", "llm_admin_actions", ["chat_id", "created_at"])
    op.create_index("idx_llm_admin_actions_chat_admin", "llm_admin_actions", ["chat_id", "admin_user_id"])


def downgrade() -> None:
    op.drop_table("llm_admin_actions")
    op.drop_table("llm_context_summaries")
    op.drop_table("llm_context_messages")
    op.drop_column("chat_settings", "llm_context_threshold")
    op.drop_column("chat_settings", "llm_enabled")
