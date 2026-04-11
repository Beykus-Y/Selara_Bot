"""add iris_view setting

Revision ID: 0042_add_iris_view_setting
Revises: 0041_add_messages_archive
Create Date: 2026-04-11 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_add_iris_view_setting"
down_revision: str | None = "0041_add_messages_archive"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column(
            "iris_view",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_settings", "iris_view")
