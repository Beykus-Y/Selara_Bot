"""web_tainted flag on llm_context_messages: keep poisoned web output out of get_history

Rows written by an ?/?? invocation whose model saw web_search/fetch_page output
are flagged; get_history's range query (which has no is_context filter) excludes
them, so page-controlled content cannot re-enter a fresh invocation that starts
with a full tool set.

Revision ID: 0090_web_tainted_history
Revises: 0089_admin_model_config
Create Date: 2026-10-06 00:00:05
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0090_web_tainted_history"
down_revision: str | None = "0089_admin_model_config"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_context_messages",
        sa.Column("web_tainted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("llm_context_messages", "web_tainted")
