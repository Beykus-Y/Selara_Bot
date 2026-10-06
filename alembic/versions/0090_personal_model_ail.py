"""Personal AI model profile selection and the AI Limits (AIL) quota mode

Revision ID: 0090_personal_model_ail
Revises: 0089_admin_model_config
Create Date: 2026-10-06 00:00:06
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0090_personal_model_ail"
down_revision: str | None = "0089_admin_model_config"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_PROFILE_KEYS = "('basic', 'analytics', 'freeform', 'creative', 'fast')"


def upgrade() -> None:
    # Every column is additive with a server default or NULL, so the previous image keeps working:
    # its INSERTs into personal_ai_profiles get 'basic', and NULL quota_mode means requests (5/150).
    op.add_column(
        "personal_ai_profiles",
        sa.Column("model_profile_key", sa.String(64), nullable=False, server_default="basic"),
    )
    op.create_check_constraint(
        "ck_personal_ai_profiles_model_profile",
        "personal_ai_profiles",
        f"model_profile_key IN {_PROFILE_KEYS}",
    )

    op.add_column("selara_personal_config", sa.Column("quota_mode", sa.String(16), nullable=True))
    op.add_column("selara_personal_config", sa.Column("free_daily_ail", sa.Integer(), nullable=True))
    op.add_column("selara_personal_config", sa.Column("paid_daily_ail", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_selara_personal_config_quota_mode",
        "selara_personal_config",
        "quota_mode IS NULL OR quota_mode IN ('requests', 'ail')",
    )
    op.create_check_constraint(
        "ck_selara_personal_config_free_ail", "selara_personal_config", "free_daily_ail IS NULL OR free_daily_ail > 0"
    )
    op.create_check_constraint(
        "ck_selara_personal_config_paid_ail", "selara_personal_config", "paid_daily_ail IS NULL OR paid_daily_ail > 0"
    )
    # AIL mode cannot be stored without both budgets: the runtime would otherwise fail closed to requests.
    op.create_check_constraint(
        "ck_selara_personal_config_ail_budgets",
        "selara_personal_config",
        "quota_mode IS DISTINCT FROM 'ail' OR (free_daily_ail IS NOT NULL AND paid_daily_ail > free_daily_ail)",
    )

    # Which model profile priced an AIL reservation; historical text, never joined to current config.
    op.add_column("ai_feature_quota_usage", sa.Column("model_profile", sa.String(64), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    checks = (
        (
            "SELECT 1 FROM personal_ai_profiles WHERE model_profile_key <> 'basic' LIMIT 1",
            "personal_ai_profiles holds users' model profile choices",
        ),
        (
            "SELECT 1 FROM selara_personal_config WHERE quota_mode = 'ail' "
            "OR free_daily_ail IS NOT NULL OR paid_daily_ail IS NOT NULL LIMIT 1",
            "selara_personal_config holds the AI Limits mode or budgets",
        ),
        (
            "SELECT 1 FROM ai_feature_quota_usage WHERE model_profile IS NOT NULL OR pool_key = 'personal_ail_daily' "
            "LIMIT 1",
            "ai_feature_quota_usage holds AI Limits reservations",
        ),
    )
    for query, reason in checks:
        if bind.execute(sa.text(query)).first() is not None:
            raise RuntimeError(
                f"Cannot downgrade 0090_personal_model_ail: {reason}. "
                "Switch Personal back to requests mode, clear the AIL budgets and resolve this data explicitly first."
            )
    op.drop_column("ai_feature_quota_usage", "model_profile")
    op.drop_constraint("ck_selara_personal_config_ail_budgets", "selara_personal_config", type_="check")
    op.drop_constraint("ck_selara_personal_config_paid_ail", "selara_personal_config", type_="check")
    op.drop_constraint("ck_selara_personal_config_free_ail", "selara_personal_config", type_="check")
    op.drop_constraint("ck_selara_personal_config_quota_mode", "selara_personal_config", type_="check")
    op.drop_column("selara_personal_config", "paid_daily_ail")
    op.drop_column("selara_personal_config", "free_daily_ail")
    op.drop_column("selara_personal_config", "quota_mode")
    op.drop_constraint("ck_personal_ai_profiles_model_profile", "personal_ai_profiles", type_="check")
    op.drop_column("personal_ai_profiles", "model_profile_key")
