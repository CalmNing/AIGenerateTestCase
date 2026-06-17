"""add_api_endpoint_fields_to_testcase

Revision ID: 3e134d966d22
Revises: e6f7a8b9c0d1
Create Date: 2026-06-17 14:19:31.353328

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = '3e134d966d22'
down_revision: Union[str, Sequence[str], None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('testcase', schema=None) as batch_op:
        batch_op.add_column(sa.Column('api_endpoint_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('api_project_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_testcase_api_endpoint_id'), ['api_endpoint_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('testcase', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_testcase_api_endpoint_id'))
        batch_op.drop_column('api_project_id')
        batch_op.drop_column('api_endpoint_id')
