"""add api project headers

Revision ID: e6f7a8b9c0d1
Revises: d4e5f6a7b8c9
Create Date: 2026-06-09 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON


revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("apiproject", sa.Column("headers", JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("apiproject", "headers")
