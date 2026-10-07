"""Record each interesting-fact send as a claim so a chat's slot is reserved before Telegram is called.

Revision ID: 0104_interesting_fact_claims
Revises: 0103_ai_turn_leases
"""

import sqlalchemy as sa
from alembic import op

revision = "0104_interesting_fact_claims"
down_revision = "0103_ai_turn_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_interesting_fact_deliveries",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("chats.telegram_chat_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fact_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("error_summary", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_chat_interesting_fact_deliveries_chat_claimed",
        "chat_interesting_fact_deliveries",
        ["chat_id", "claimed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_interesting_fact_deliveries_chat_claimed", table_name="chat_interesting_fact_deliveries")
    op.drop_table("chat_interesting_fact_deliveries")
