"""add parent_id to module

Revision ID: add_parent_id_module
Revises: 
Create Date: 2026-03-09 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_parent_id_module'
down_revision: Union[str, Sequence[str], None] = 'a14a297f448d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 添加 parent_id 字段，支持模块树形结构
    op.add_column('module', sa.Column('parent_id', sa.Integer(), nullable=True))
    # 添加外键约束
    op.create_foreign_key('fk_module_parent_id', 'module', 'module', ['parent_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    # 删除外键约束
    op.drop_constraint('fk_module_parent_id', 'module', type_='foreignkey')
    # 删除 parent_id 字段
    op.drop_column('module', 'parent_id')
