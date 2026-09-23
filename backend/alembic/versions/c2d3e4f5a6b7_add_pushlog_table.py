"""add pushlog table

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-09-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON


# revision identifiers, used by Alembic.
revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('pushlog',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('request_method', sa.String(), nullable=False, server_default=''),
        sa.Column('source_url', sa.String(), nullable=False, server_default=''),
        sa.Column('final_url', sa.String(), nullable=False, server_default=''),
        sa.Column('request_headers', JSON(), nullable=False, server_default='{}'),
        sa.Column('final_headers', JSON(), nullable=False, server_default='{}'),
        sa.Column('request_params', JSON(), nullable=False, server_default='{}'),
        sa.Column('final_params', JSON(), nullable=False, server_default='{}'),
        sa.Column('request_body', sa.String(), nullable=True),
        sa.Column('final_body', sa.String(), nullable=True),
        sa.Column('response_status', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('response_headers', JSON(), nullable=False, server_default='{}'),
        sa.Column('response_body', sa.String(), nullable=True),
        sa.Column('mocked', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('error', sa.String(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('environment_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pushlog_request_method'), 'pushlog', ['request_method'], unique=False)
    op.create_index(op.f('ix_pushlog_user_id'), 'pushlog', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_pushlog_user_id'), table_name='pushlog')
    op.drop_index(op.f('ix_pushlog_request_method'), table_name='pushlog')
    op.drop_table('pushlog')
