"""add testcase execution log

Revision ID: 9f1a2b3c4d5e
Revises: f238d258afcc
Create Date: 2026-06-23 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON


revision: str = "9f1a2b3c4d5e"
down_revision: Union[str, Sequence[str], None] = "f238d258afcc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("testcase", schema=None) as batch_op:
        batch_op.alter_column(
            "api_endpoint_id",
            existing_type=sa.Integer(),
            type_=sa.String(),
            existing_nullable=True,
        )

    op.create_table(
        "testcaseexecutionlog",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("testcase_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("case_name", sa.String(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("result", JSON(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.ForeignKeyConstraint(["testcase_id"], ["testcase.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_testcaseexecutionlog_session_id", "testcaseexecutionlog", ["session_id"])
    op.create_index("ix_testcaseexecutionlog_testcase_id", "testcaseexecutionlog", ["testcase_id"])
    op.create_index("ix_testcaseexecutionlog_user_id", "testcaseexecutionlog", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_testcaseexecutionlog_user_id", table_name="testcaseexecutionlog")
    op.drop_index("ix_testcaseexecutionlog_testcase_id", table_name="testcaseexecutionlog")
    op.drop_index("ix_testcaseexecutionlog_session_id", table_name="testcaseexecutionlog")
    op.drop_table("testcaseexecutionlog")

    with op.batch_alter_table("testcase", schema=None) as batch_op:
        batch_op.alter_column(
            "api_endpoint_id",
            existing_type=sa.String(),
            type_=sa.Integer(),
            existing_nullable=True,
        )
