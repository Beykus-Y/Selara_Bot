"""Persist runtime destinations for operational alerts."""
import sqlalchemy as sa
from alembic import op


revision = "0066_admin_runtime_settings"
down_revision = "0065_autoconfig_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admin_runtime_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("error_alert_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("error_alerts_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("admin_runtime_settings")
