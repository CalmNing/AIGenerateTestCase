"""Add global parameter table

Revision ID: a14a297f448d
Revises: add_id_to_savedrequest
Create Date: 2026-02-06 11:31:32.110127

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a14a297f448d'
down_revision: Union[str, Sequence[str], None] = 'add_id_to_savedrequest'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 创建 globalparameter 表
    op.create_table('globalparameter',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('name', sa.String(), nullable=False, default=""),
        sa.Column('parameters', sa.JSON(), nullable=True, default=[]),
        sa.Column('is_default', sa.Boolean(), nullable=False, default=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 删除 globalparameter 表
    op.drop_table('globalparameter')
