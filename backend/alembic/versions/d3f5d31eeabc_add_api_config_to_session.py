"""add_api_config_to_session

Revision ID: d3f5d31eeabc
Revises: 3e134d966d22
Create Date: 2026-06-18 17:53:53.376069

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON

# revision identifiers, used by Alembic.
revision: str = 'd3f5d31eeabc'
down_revision: Union[str, Sequence[str], None] = '3e134d966d22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('session', schema=None) as batch_op:
        batch_op.add_column(sa.Column('api_config', JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('session', schema=None) as batch_op:
        batch_op.drop_column('api_config')
