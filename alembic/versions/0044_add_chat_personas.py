"""add chat personas

Revision ID: 0044_add_chat_personas
Revises: 0043_add_admin_broadcasts
Create Date: 2026-04-13 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_add_chat_personas"
down_revision: str | None = "0043_add_admin_broadcasts"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_chat_activity", sa.Column("persona_label", sa.String(length=96), nullable=True))
    op.add_column("user_chat_activity", sa.Column("persona_label_norm", sa.String(length=96), nullable=True))
    op.add_column(
        "user_chat_activity",
        sa.Column(
            "persona_granted_by_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.telegram_user_id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("user_chat_activity", sa.Column("persona_granted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "uq_user_chat_activity_chat_persona_norm",
        "user_chat_activity",
        ["chat_id", "persona_label_norm"],
        unique=True,
    )

    op.add_column(
        "chat_settings",
        sa.Column("persona_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "chat_settings",
        sa.Column("persona_display_mode", sa.String(length=24), nullable=False, server_default="image_name"),
    )


def downgrade() -> None:
    op.drop_column("chat_settings", "persona_display_mode")
    op.drop_column("chat_settings", "persona_enabled")

    op.drop_index("uq_user_chat_activity_chat_persona_norm", table_name="user_chat_activity")
    op.drop_column("user_chat_activity", "persona_granted_at")
    op.drop_column("user_chat_activity", "persona_granted_by_user_id")
    op.drop_column("user_chat_activity", "persona_label_norm")
    op.drop_column("user_chat_activity", "persona_label")
