"""strip iris award prefixes from stored chat awards

Revision ID: 0032_strip_iris_awards
Revises: 0031_merge_activity_heads
Create Date: 2026-03-13 22:45:00
"""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0032_strip_iris_awards"
down_revision = "0031_merge_activity_heads"
branch_labels = None
depends_on = None

_IRIS_AWARD_PREFIX_RE = re.compile(r"^\s*🎗(?:\ufe0f)?[₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹]*\s*")


def _strip_iris_award_prefix(value: str) -> str:
    normalized = " ".join((value or "").split()).strip()
    if not normalized:
        return ""
    return _IRIS_AWARD_PREFIX_RE.sub("", normalized).strip()


def upgrade() -> None:
    bind = op.get_bind()
    awards = sa.table(
        "user_chat_awards",
        sa.column("id", sa.BigInteger()),
        sa.column("title", sa.String(length=160)),
    )

    rows = bind.execute(sa.select(awards.c.id, awards.c.title)).all()
    for award_id, raw_title in rows:
        cleaned_title = _strip_iris_award_prefix(raw_title or "")
        if not cleaned_title or cleaned_title == raw_title:
            continue
        bind.execute(
            awards.update()
            .where(awards.c.id == award_id)
            .values(title=cleaned_title)
        )


def downgrade() -> None:
    # Prefix stripping is intentionally irreversible.
    pass
