"""add mock_config proxy_urls and mock_proxy_rule service_prefix

Revision ID: d8e9f0a1b2c3
Revises: c7e8f9a0b1d2
Create Date: 2026-10-22 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON


revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "c7e8f9a0b1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("mockconfig", schema=None) as batch_op:
        batch_op.add_column(sa.Column("proxy_urls", JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("hosts", JSON(), nullable=False, server_default="[]"))

    with op.batch_alter_table("mockproxyrule", schema=None) as batch_op:
        batch_op.add_column(sa.Column("service_prefix", sa.String(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("mockproxyrule", schema=None) as batch_op:
        batch_op.drop_column("service_prefix")

    with op.batch_alter_table("mockconfig", schema=None) as batch_op:
        batch_op.drop_column("hosts")
        batch_op.drop_column("proxy_urls")
