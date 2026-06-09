"""add api test tool tables

Revision ID: f6a7b8c9d0e1
Revises: ccd21d8623c8
Create Date: 2026-06-08 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "ccd21d8623c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "apiproject",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("base_url", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("raw_spec", sa.String(), nullable=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_apiproject_user_id", "apiproject", ["user_id"])

    op.create_table(
        "apiendpoint",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("tags", JSON(), nullable=False),
        sa.Column("headers", JSON(), nullable=False),
        sa.Column("parameters", JSON(), nullable=False),
        sa.Column("body", sa.String(), nullable=True),
        sa.Column("pre_actions", JSON(), nullable=False),
        sa.Column("post_actions", JSON(), nullable=False),
        sa.Column("assertions", JSON(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["apiproject.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_apiendpoint_user_id", "apiendpoint", ["user_id"])

    op.create_table(
        "apiscenario",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("base_url", sa.String(), nullable=True),
        sa.Column("environment_id", sa.Integer(), nullable=True),
        sa.Column("variables", JSON(), nullable=False),
        sa.Column("steps", JSON(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["apiproject.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_apiscenario_user_id", "apiscenario", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_apiscenario_user_id", table_name="apiscenario")
    op.drop_table("apiscenario")
    op.drop_index("ix_apiendpoint_user_id", table_name="apiendpoint")
    op.drop_table("apiendpoint")
    op.drop_index("ix_apiproject_user_id", table_name="apiproject")
    op.drop_table("apiproject")
