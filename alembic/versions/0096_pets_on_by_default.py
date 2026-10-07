"""AI pets are allowed in chats by default

Revision ID: 0096_pets_on_by_default
Revises: 0095_group_member_actions
Create Date: 2026-10-07 00:00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0096_pets_on_by_default"
down_revision: str | None = "0095_group_member_actions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("chat_settings", "pets_enabled", server_default=sa.true())
    # Rows hold ``false`` both for "never touched" (the old default) and for "an admin turned it off".
    # An explicit off needs an earlier on, so a chat that ever had a pet was switched on by its admins:
    # its ``false`` is a decision and stays. Every other ``false`` is the old default and becomes on.
    op.execute(
        """
        UPDATE chat_settings
        SET pets_enabled = TRUE
        WHERE pets_enabled = FALSE
          AND NOT EXISTS (
              SELECT 1 FROM ai_pets
              WHERE ai_pets.home_chat_id = chat_settings.chat_id
                 OR ai_pets.current_chat_id = chat_settings.chat_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM ai_pet_relationships
              WHERE ai_pet_relationships.chat_id = chat_settings.chat_id
          )
        """
    )


def downgrade() -> None:
    # Data is not reverted: the previous image reads the column as an explicit value either way.
    op.alter_column("chat_settings", "pets_enabled", server_default=sa.false())
