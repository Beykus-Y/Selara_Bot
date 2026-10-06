"""Persistent model catalog, exact identifiers and logical profiles.

Revision ID: 0085_model_catalog_router
Revises: 0084_ai_pet_dialogue
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0085_model_catalog_router"
down_revision = "0084_ai_pet_dialogue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_model_catalog",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("model_id", sa.String(255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("prompt_price_usd_per_million", sa.Numeric(16, 9), nullable=True),
        sa.Column("completion_price_usd_per_million", sa.Numeric(16, 9), nullable=True),
        sa.Column("supports_tools", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_structured_output", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_vision", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("length(trim(key)) > 0", name="ck_llm_model_catalog_key"),
        sa.CheckConstraint("length(trim(model_id)) > 0", name="ck_llm_model_catalog_model_id"),
        sa.CheckConstraint("length(trim(display_name)) > 0", name="ck_llm_model_catalog_display_name"),
        sa.CheckConstraint("prompt_price_usd_per_million >= 0 AND prompt_price_usd_per_million <= 1000000",
                           name="ck_llm_model_catalog_prompt_price"),
        sa.CheckConstraint("completion_price_usd_per_million >= 0 AND completion_price_usd_per_million <= 1000000",
                           name="ck_llm_model_catalog_completion_price"),
    )
    op.create_table(
        "llm_model_identifiers",
        sa.Column("model_id", sa.String(255), primary_key=True),
        sa.Column("model_key", sa.String(64), sa.ForeignKey("llm_model_catalog.key", ondelete="CASCADE"), nullable=False),
        sa.CheckConstraint("length(trim(model_id)) > 0", name="ck_llm_model_identifiers_model_id"),
    )
    op.create_index("idx_llm_model_identifiers_model_key", "llm_model_identifiers", ["model_key"])
    profiles = op.create_table(
        "llm_model_profiles",
        sa.Column("profile_key", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("model_key", sa.String(64), sa.ForeignKey("llm_model_catalog.key", ondelete="SET NULL"), nullable=True),
        sa.Column("ail_multiplier", sa.Numeric(13, 9), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("profile_key IN ('basic', 'analytics', 'freeform', 'creative', 'fast')", name="ck_llm_model_profiles_key"),
        sa.CheckConstraint("length(trim(display_name)) > 0", name="ck_llm_model_profiles_display_name"),
        sa.CheckConstraint("ail_multiplier > 0 AND ail_multiplier <= 1000", name="ck_llm_model_profiles_multiplier"),
    )
    op.create_index("idx_llm_model_profiles_model_key", "llm_model_profiles", ["model_key"])
    op.bulk_insert(profiles, [
        {"profile_key": key, "display_name": name, "ail_multiplier": 1, "enabled": True}
        for key, name in (("basic", "Базовая"), ("analytics", "Аналитик"), ("freeform", "Свободная"),
                          ("creative", "Творческая"), ("fast", "Быстрая"))
    ])
    op.add_column("llm_usage_log", sa.Column("model_profile", sa.String(64), nullable=True))
    op.create_index("idx_llm_usage_log_profile_created", "llm_usage_log", ["model_profile", "created_at"])
    op.alter_column("llm_usage_log", "model", existing_type=sa.String(64), type_=sa.String(255), existing_nullable=False)

    op.alter_column("llm_usage_log", "estimated_cost_usd", existing_type=sa.Numeric(14, 9),
                    type_=sa.Numeric(20, 9), existing_nullable=True)

    op.alter_column("daily_summary_runs", "pipeline_cost_usd", existing_type=sa.Numeric(14, 9),
                    type_=sa.Numeric(20, 9), existing_nullable=False)


def downgrade() -> None:
    # Keep guards and narrowing atomic against concurrent accounting writes.
    op.execute("LOCK TABLE llm_usage_log, daily_summary_runs IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(
        "SELECT count(*) FROM llm_usage_log WHERE abs(estimated_cost_usd) >= 100000"
    )):
        raise RuntimeError("Cannot downgrade: usage costs do not fit NUMERIC(14,9)")
    if op.get_bind().scalar(sa.text(
        "SELECT count(*) FROM daily_summary_runs WHERE abs(pipeline_cost_usd) >= 100000"
    )):
        raise RuntimeError("Cannot downgrade: summary costs do not fit NUMERIC(14,9)")
    # Do not silently truncate actual provider identifiers introduced since upgrade.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM llm_usage_log WHERE length(model) > 64")):
        raise RuntimeError("Cannot downgrade: usage contains model identifiers longer than 64 characters")
    op.alter_column("daily_summary_runs", "pipeline_cost_usd", existing_type=sa.Numeric(20, 9),
                    type_=sa.Numeric(14, 9), existing_nullable=False)
    op.alter_column("llm_usage_log", "estimated_cost_usd", existing_type=sa.Numeric(20, 9),
                    type_=sa.Numeric(14, 9), existing_nullable=True)
    op.alter_column("llm_usage_log", "model", existing_type=sa.String(255), type_=sa.String(64), existing_nullable=False)
    op.drop_index("idx_llm_usage_log_profile_created", table_name="llm_usage_log")
    op.drop_column("llm_usage_log", "model_profile")
    op.drop_table("llm_model_profiles")
    op.drop_table("llm_model_identifiers")
    op.drop_table("llm_model_catalog")
