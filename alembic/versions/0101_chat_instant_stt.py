"""Allow each chat to disable instant voice/video-note replies.

Revision ID: 0101_chat_instant_stt
Revises: 0100_stt_budget_reservations
"""

import sqlalchemy as sa
from alembic import op

revision = "0101_chat_instant_stt"
down_revision = "0100_stt_budget_reservations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing chats retain their previous automatic-reply behavior.
    op.add_column("chat_settings", sa.Column("instant_stt_enabled", sa.Boolean(),
                                            nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("chat_settings", "instant_stt_enabled")
