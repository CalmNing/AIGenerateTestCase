"""add api project mock switch

Revision ID: b1c2d3e4f5a6
Revises: 9f1a2b3c4d5e
Create Date: 2026-08-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "9f1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("apiproject", sa.Column("mock_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("apiproject", sa.Column("mock_prefix", sa.String(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("apiproject", "mock_prefix")
    op.drop_column("apiproject", "mock_enabled")
