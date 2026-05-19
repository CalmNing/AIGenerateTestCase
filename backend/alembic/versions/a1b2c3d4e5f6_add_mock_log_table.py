"""add mock_log table

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-05-15 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'add_parent_id_module'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('mocklog',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('config_id', sa.Integer(), sa.ForeignKey('mockconfig.id'), nullable=True),
        sa.Column('config_name', sa.String(), nullable=False, server_default=''),
        sa.Column('request_method', sa.String(), nullable=False, server_default=''),
        sa.Column('request_path', sa.String(), nullable=False, server_default=''),
        sa.Column('request_headers', JSON(), nullable=False, server_default='[]'),
        sa.Column('request_query_params', sa.String(), nullable=True),
        sa.Column('request_body', sa.String(), nullable=True),
        sa.Column('response_status_code', sa.Integer(), nullable=False, server_default='200'),
        sa.Column('response_headers', JSON(), nullable=False, server_default='[]'),
        sa.Column('response_body', sa.String(), nullable=True),
        sa.Column('matched', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('user_id', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_mocklog_user_id'), 'mocklog', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_mocklog_user_id'), table_name='mocklog')
    op.drop_table('mocklog')
