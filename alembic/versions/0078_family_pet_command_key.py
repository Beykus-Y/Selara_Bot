"""rename the role-play pet command key from pet to family_pet

The old /pet ("стать питомцем") becomes /bepet, and /pet is later handed over
to AI pets. Per-chat rank rules and text aliases keep working under the new key.

Revision ID: 0078_family_pet_command_key
Revises: 0077_selara_ai_payment_refunds
Create Date: 2026-10-06 00:00:00
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0078_family_pet_command_key"
down_revision: str | None = "0077_selara_ai_payment_refunds"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _rename_access_rules(*, old_key: str, new_key: str) -> None:
    # PK (chat_id, command_key): a rule already stored under the new key wins,
    # the colliding old row is dropped so the old key is fully released.
    op.execute(
        f"""
        UPDATE chat_command_access_rules
        SET command_key = '{new_key}'
        WHERE command_key = '{old_key}'
          AND NOT EXISTS (
            SELECT 1 FROM chat_command_access_rules AS existing
            WHERE existing.chat_id = chat_command_access_rules.chat_id
              AND existing.command_key = '{new_key}'
          )
        """
    )
    op.execute(f"DELETE FROM chat_command_access_rules WHERE command_key = '{old_key}'")


def upgrade() -> None:
    _rename_access_rules(old_key="pet", new_key="family_pet")
    # Aliases are unique by (chat_id, alias_text_norm), command_key is not part of the key.
    op.execute("UPDATE chat_text_aliases SET command_key = 'family_pet' WHERE command_key = 'pet'")


def downgrade() -> None:
    _rename_access_rules(old_key="family_pet", new_key="pet")
    op.execute("UPDATE chat_text_aliases SET command_key = 'pet' WHERE command_key = 'family_pet'")
