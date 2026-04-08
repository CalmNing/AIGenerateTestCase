"""add_parameters_to_scheduled_task

Revision ID: e5f6a7b8c9d0
Revises: be3a6233c1bd
Create Date: 2026-04-07

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers
revision = 'e5f6a7b8c9d0'
down_revision = 'be3a6233c1bd'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('scheduledtask', sa.Column('parameters', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('scheduledtask', 'parameters')
