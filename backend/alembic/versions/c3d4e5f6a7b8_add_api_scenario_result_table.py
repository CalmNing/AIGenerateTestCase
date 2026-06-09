"""add api scenario result table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-09 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "apiscenarioresult",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("scenario_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("scenario_name", sa.String(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("result", JSON(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["apiproject.id"]),
        sa.ForeignKeyConstraint(["scenario_id"], ["apiscenario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_apiscenarioresult_project_id", "apiscenarioresult", ["project_id"])
    op.create_index("ix_apiscenarioresult_scenario_id", "apiscenarioresult", ["scenario_id"])
    op.create_index("ix_apiscenarioresult_user_id", "apiscenarioresult", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_apiscenarioresult_user_id", table_name="apiscenarioresult")
    op.drop_index("ix_apiscenarioresult_scenario_id", table_name="apiscenarioresult")
    op.drop_index("ix_apiscenarioresult_project_id", table_name="apiscenarioresult")
    op.drop_table("apiscenarioresult")
