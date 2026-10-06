"""Optimistic revisions and actor audit for owner model configuration."""
import sqlalchemy as sa
from alembic import op

revision = "0088_admin_model_config"
down_revision = "0087_personal_ai_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("llm_model_catalog", "llm_model_profiles"):
        op.add_column(table, sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        op.add_column(table, sa.Column("updated_by", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    for table in ("llm_model_profiles", "llm_model_catalog"):
        op.drop_column(table, "updated_by")
        op.drop_column(table, "revision")
