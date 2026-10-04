"""Cache Telegram group member count snapshots."""
import sqlalchemy as sa
from alembic import op


revision = "0067_chat_member_count_snapshots"
down_revision = "0066_admin_runtime_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chat_member_count_snapshots",
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("member_count", sa.BigInteger(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_table("chat_member_count_snapshots")
