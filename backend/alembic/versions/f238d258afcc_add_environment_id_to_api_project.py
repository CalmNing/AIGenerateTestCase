"""add environment_id to api_project

Revision ID: f238d258afcc
Revises: d3f5d31eeabc
Create Date: 2026-06-18 23:54:27.508684

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f238d258afcc'
down_revision: Union[str, Sequence[str], None] = 'd3f5d31eeabc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('apiproject', schema=None) as batch_op:
        batch_op.add_column(sa.Column('environment_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('apiproject', schema=None) as batch_op:
        batch_op.drop_column('environment_id')
