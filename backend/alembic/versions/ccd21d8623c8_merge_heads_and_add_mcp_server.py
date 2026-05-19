"""merge heads and add mcp_server table

Revision ID: ccd21d8623c8
Revises: a1b2c3d4e5f6, f1a2b3c4d5e6, e5f6a7b8c9d0, d2e3f4a5b6c7
Create Date: 2026-05-19 14:24:07.523491

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON


# revision identifiers, used by Alembic.
revision: str = 'ccd21d8623c8'
down_revision: Union[str, Sequence[str], None] = ('a1b2c3d4e5f6', 'f1a2b3c4d5e6', 'e5f6a7b8c9d0', 'd2e3f4a5b6c7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'mcpserver',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('type', sa.String(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('url', sa.String(), nullable=True),
        sa.Column('command', sa.String(), nullable=True),
        sa.Column('args', JSON(), nullable=False),
        sa.Column('timeout', sa.Integer(), nullable=False),
        sa.Column('env', JSON(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('mcpserver') as batch_op:
        batch_op.create_index('ix_mcpserver_user_id', ['user_id'])


def downgrade() -> None:
    op.drop_table('mcpserver')
