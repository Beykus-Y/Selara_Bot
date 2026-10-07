"""Record scheduled daily backup slots so only one bot instance runs each dump.

Revision ID: 0102_backup_job_claims
Revises: 0101_chat_instant_stt
"""

import sqlalchemy as sa
from alembic import op

revision = "0102_backup_job_claims"
down_revision = "0101_chat_instant_stt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_job_claims",
        sa.Column("slot_key", sa.String(length=64), primary_key=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("owner_token", sa.String(length=64), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("backup_job_claims")
