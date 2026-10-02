"""Persist chat-scoped rendered agent artifacts and delivery progress."""
import sqlalchemy as sa
from alembic import op

revision = "0064_llm_artifacts"
down_revision = "0063_glossary_aliases"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "llm_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("creator_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("pages", sa.JSON(), nullable=False),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("delivery", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_llm_artifacts_chat_expiry", "llm_artifacts", ["chat_id", "expires_at"])


def downgrade():
    op.drop_table("llm_artifacts")
