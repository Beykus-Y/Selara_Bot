"""Add idempotency key to admin broadcasts."""
import sqlalchemy as sa
from alembic import op


revision = "0069_broadcast_idempotency_key"
down_revision = "0068_operational_alert_history"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admin_broadcasts", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_admin_broadcast_idempotency_key", "admin_broadcasts", ["idempotency_key"])


def downgrade():
    op.drop_constraint("uq_admin_broadcast_idempotency_key", "admin_broadcasts", type_="unique")
    op.drop_column("admin_broadcasts", "idempotency_key")
