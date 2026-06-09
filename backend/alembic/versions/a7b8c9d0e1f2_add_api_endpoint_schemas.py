"""add api endpoint schemas

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-08 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("apiendpoint", sa.Column("request_schema", JSON(), nullable=False, server_default="{}"))
    op.add_column("apiendpoint", sa.Column("response_schema", JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("apiendpoint", "response_schema")
    op.drop_column("apiendpoint", "request_schema")
