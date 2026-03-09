"""add_history_prompt_table

Revision ID: c1a2b3c4d5e6
Revises: 2b1977bd1d35
Create Date: 2026-03-05 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite
import logging


# revision identifiers, used by Alembic.
revision: str = 'c1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = '2b1977bd1d35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 获取日志记录器
log = logging.getLogger(__name__)


def upgrade() -> None:
    """Upgrade schema."""
    inspector = sa.inspect(op.get_bind())

    # 检查 historyprompt 表是否已存在
    existing_tables = inspector.get_table_names()
    if 'historyprompt' not in existing_tables:
        op.create_table('historyprompt',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('content', sa.String(), nullable=False, server_default=""),
            sa.Column('module_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('session_id', sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['module_id'], ['module.id']),
            sa.ForeignKeyConstraint(['session_id'], ['session.id'])
        )
        log.info("Table 'historyprompt' created successfully.")
    else:
        log.info("Table 'historyprompt' already exists, skipping creation.")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('historyprompt')
