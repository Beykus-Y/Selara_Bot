"""Durable per-key leases so one Personal AI or LLM admin turn runs at a time across bot instances.

Revision ID: 0103_ai_turn_leases
Revises: 0102_backup_job_claims
"""

import sqlalchemy as sa
from alembic import op

revision = "0103_ai_turn_leases"
down_revision = "0102_backup_job_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_turn_leases",
        sa.Column("lease_key", sa.String(length=128), primary_key=True),
        sa.Column("owner_token", sa.String(length=64), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ai_turn_leases")
