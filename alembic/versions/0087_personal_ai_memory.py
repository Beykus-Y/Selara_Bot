"""Personal AI memory: user facts, auto-extraction opt-in and runtime overrides of memory limits

Revision ID: 0087_personal_ai_memory
Revises: 0086_ai_pet_events
Create Date: 2026-10-06 00:00:04
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0087_personal_ai_memory"
down_revision: str | None = "0086_ai_pet_events"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "personal_ai_memories",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_user_id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("content", sa.String(300), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("source IN ('explicit', 'extracted')", name="ck_personal_ai_memories_source"),
        sa.CheckConstraint("length(content) BETWEEN 1 AND 300", name="ck_personal_ai_memories_content_len"),
    )
    op.create_index("idx_personal_ai_memories_user_created", "personal_ai_memories", ["user_id", "created_at"])
    # One fact once per user, whatever the letter case; the application serialises writers per user as well.
    op.create_index(
        "uq_personal_ai_memories_user_content",
        "personal_ai_memories",
        ["user_id", sa.text("lower(content)")],
        unique=True,
    )

    # New columns are additive with defaults, so the previous image keeps working against this schema.
    op.add_column(
        "personal_ai_profiles",
        sa.Column("auto_memory_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "personal_ai_profiles",
        sa.Column("memory_extract_cursor", sa.BigInteger(), nullable=False, server_default="0"),
    )

    op.add_column("selara_personal_config", sa.Column("memory_free_limit", sa.Integer(), nullable=True))
    op.add_column("selara_personal_config", sa.Column("memory_paid_limit", sa.Integer(), nullable=True))
    op.add_column("selara_personal_config", sa.Column("memory_auto_extract", sa.Boolean(), nullable=True))
    op.add_column("selara_personal_config", sa.Column("memory_extract_every", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_selara_personal_config_memory_free",
        "selara_personal_config",
        "memory_free_limit IS NULL OR memory_free_limit > 0",
    )
    op.create_check_constraint(
        "ck_selara_personal_config_memory_paid",
        "selara_personal_config",
        "memory_paid_limit IS NULL OR memory_paid_limit > 0",
    )
    op.create_check_constraint(
        "ck_selara_personal_config_memory_every",
        "selara_personal_config",
        "memory_extract_every IS NULL OR memory_extract_every BETWEEN 2 AND 40",
    )


def downgrade() -> None:
    # Facts are the user's private data (like the dialogues in 0082): refuse to drop them silently.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM personal_ai_memories LIMIT 1")).first() is not None:
        raise RuntimeError(
            "Cannot downgrade 0087_personal_ai_memory: personal_ai_memories holds users' private data; "
            "export or delete it first"
        )
    op.drop_constraint("ck_selara_personal_config_memory_every", "selara_personal_config", type_="check")
    op.drop_constraint("ck_selara_personal_config_memory_paid", "selara_personal_config", type_="check")
    op.drop_constraint("ck_selara_personal_config_memory_free", "selara_personal_config", type_="check")
    for column in ("memory_extract_every", "memory_auto_extract", "memory_paid_limit", "memory_free_limit"):
        op.drop_column("selara_personal_config", column)
    op.drop_column("personal_ai_profiles", "memory_extract_cursor")
    op.drop_column("personal_ai_profiles", "auto_memory_enabled")
    op.drop_index("uq_personal_ai_memories_user_content", table_name="personal_ai_memories")
    op.drop_index("idx_personal_ai_memories_user_created", table_name="personal_ai_memories")
    op.drop_table("personal_ai_memories")
