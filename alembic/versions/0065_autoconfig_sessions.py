"""Private AI configuration drafts and durable request leases."""
import sqlalchemy as sa
from alembic import op

revision = '0065_autoconfig_sessions'
down_revision = '0064_llm_artifacts'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('autoconfig_sessions',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('user_id', sa.BigInteger(), nullable=False, unique=True),
        sa.Column('chat_id', sa.BigInteger(), sa.ForeignKey('chats.telegram_chat_id', ondelete='CASCADE'), nullable=True),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        *[sa.Column(k, sa.JSON(), nullable=False) for k in ('candidates', 'baseline', 'draft', 'touched', 'history')],
        sa.Column('turns', sa.Integer(), nullable=False),
        sa.Column('last_turn_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lease_token', sa.String(32), nullable=True),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table('autoconfig_sessions')
