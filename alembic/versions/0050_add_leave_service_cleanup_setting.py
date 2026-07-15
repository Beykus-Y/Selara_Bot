"""add separate leave service cleanup setting

Revision ID: 0050_leave_service_cleanup
Revises: 0049_add_llm_glossary
Create Date: 2026-07-15 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_leave_service_cleanup"
down_revision: str | None = "0049_add_llm_glossary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column("cleanup_leave_service_messages", sa.Boolean(), nullable=True),
    )
    op.execute(
        "UPDATE chat_settings "
        "SET cleanup_leave_service_messages = welcome_cleanup_service_messages"
    )
    op.alter_column(
        "chat_settings",
        "cleanup_leave_service_messages",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.text("false"),
    )


def downgrade() -> None:
    op.drop_column("chat_settings", "cleanup_leave_service_messages")
