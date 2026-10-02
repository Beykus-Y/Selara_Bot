"""Add glossary aliases and preserve aliases in revision history."""
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0063_glossary_aliases"
down_revision: str | None = "0062_daily_summary_diag"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_chat_glossary_aliases",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("glossary_id", sa.BigInteger(), sa.ForeignKey("llm_chat_glossary.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(256), nullable=False),
        sa.UniqueConstraint("chat_id", "alias", name="uq_llm_glossary_chat_alias"),
    )
    op.create_index("idx_llm_glossary_alias_entry", "llm_chat_glossary_aliases", ["glossary_id"])
    op.add_column("llm_chat_glossary_history", sa.Column("previous_aliases", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_chat_glossary_history", "previous_aliases")
    op.drop_table("llm_chat_glossary_aliases")
