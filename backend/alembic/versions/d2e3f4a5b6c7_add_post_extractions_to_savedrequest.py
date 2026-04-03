"""add_post_extractions_to_savedrequest

Revision ID: d2e3f4a5b6c7
Revises: be3a6233c1bd
Create Date: 2026-04-02 18:13:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'be3a6233c1bd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite 不支持 ADD COLUMN 直接设 DEFAULT，需先添加再更新
    op.add_column('savedrequest', sa.Column('post_extractions', sa.JSON(), nullable=False, server_default='[]'))


def downgrade() -> None:
    op.drop_column('savedrequest', 'post_extractions')
