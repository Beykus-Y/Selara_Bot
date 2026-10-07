"""Pending admin confirmations for high-impact LLM tool calls (#51).

ban_user / set_rank proposed by the model are stored here first (exact
arguments + payload hash + initiating admin + TTL); the side effect runs only
after the same admin approves via the chat button, so a misunderstood prompt
cannot ban anyone or hand out roles without an explicit click.

Revision ID: 0096_llm_tool_confirmations
Revises: 0095_group_member_actions
Create Date: 2026-10-07 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0096_llm_tool_confirmations"
down_revision: str | None = "0095_group_member_actions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_tool_confirmations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.telegram_user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("arguments_json", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("action_description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'rejected', 'expired')",
            name="ck_llm_tool_confirmations_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_llm_tool_confirmations_token"),
    )
    op.create_index("idx_llm_tool_confirmations_chat_status", "llm_tool_confirmations", ["chat_id", "status"])


def downgrade() -> None:
    op.drop_index("idx_llm_tool_confirmations_chat_status", table_name="llm_tool_confirmations")
    op.drop_table("llm_tool_confirmations")
