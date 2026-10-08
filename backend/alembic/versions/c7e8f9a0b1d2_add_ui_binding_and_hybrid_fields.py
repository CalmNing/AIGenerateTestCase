"""add ui_binding and hybrid execution fields

Revision ID: c7e8f9a0b1d2
Revises: c2d3e4f5a6b7
Create Date: 2026-09-24 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON


revision: str = "c7e8f9a0b1d2"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("testcase", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ui_binding", JSON(), nullable=True))

    with op.batch_alter_table("uiexecutionrecord", schema=None) as batch_op:
        batch_op.add_column(sa.Column("execution_mode", sa.String(), nullable=False, server_default="agent"))
        batch_op.add_column(sa.Column("page_object_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("binding_snapshot", JSON(), nullable=False, server_default="[]"))
        batch_op.create_foreign_key(
            "fk_uiexecutionrecord_page_object_id", "pageobject", ["page_object_id"], ["id"]
        )
        batch_op.create_index("ix_uiexecutionrecord_page_object_id", ["page_object_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("uiexecutionrecord", schema=None) as batch_op:
        batch_op.drop_index("ix_uiexecutionrecord_page_object_id")
        batch_op.drop_constraint("fk_uiexecutionrecord_page_object_id", type_="foreignkey")
        batch_op.drop_column("binding_snapshot")
        batch_op.drop_column("page_object_id")
        batch_op.drop_column("execution_mode")

    with op.batch_alter_table("testcase", schema=None) as batch_op:
        batch_op.drop_column("ui_binding")
