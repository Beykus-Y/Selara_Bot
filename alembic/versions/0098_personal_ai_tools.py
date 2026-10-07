"""Personal AI tools: per-user switches and the web-tainted mark on stored answers

Revision ID: 0098_personal_ai_tools
Revises: 0097_entitlement_grants
Create Date: 2026-10-08 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0098_personal_ai_tools"
down_revision: str | None = "0097_entitlement_grants"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Everything is off for everybody: nobody gets a tool without switching it on explicitly.
    op.add_column(
        "personal_ai_profiles",
        sa.Column("tools_web_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "personal_ai_profiles",
        sa.Column("tools_artifacts_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "personal_ai_messages",
        sa.Column("web_tainted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("personal_ai_messages", "web_tainted")
    op.drop_column("personal_ai_profiles", "tools_artifacts_enabled")
    op.drop_column("personal_ai_profiles", "tools_web_enabled")
