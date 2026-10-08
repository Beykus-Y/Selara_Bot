"""Index archived messages by snapshot time so retention can find expired rows (issue #79).

Revision ID: 0107_message_archive_index
Revises: 0106_activity_event_inbox
"""

from alembic import op

revision = "0107_message_archive_index"
down_revision = "0106_activity_event_inbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A plain index: it blocks writes to messages while it builds, so run the deploy in a quiet window.
    op.create_index("idx_messages_snapshot_at", "messages", ["snapshot_at"])


def downgrade() -> None:
    op.drop_index("idx_messages_snapshot_at", table_name="messages")
