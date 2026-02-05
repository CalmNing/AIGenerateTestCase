"""Add id column to savedrequest table"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite


revision = 'add_id_to_savedrequest'
down_revision = 'fix_database_schema'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fix savedrequest table: add id column
    # SQLite doesn't support ALTER TABLE ADD COLUMN with PRIMARY KEY
    # So we need to recreate the table
    op.execute('PRAGMA foreign_keys=OFF')
    
    # Create temporary savedrequest table with correct schema including id
    op.create_table('savedrequest_temp',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('method', sa.String(), nullable=True),
        sa.Column('url', sa.String(), nullable=True),
        sa.Column('headers', sqlite.JSON(), nullable=True),
        sa.Column('parameters', sqlite.JSON(), nullable=True),
        sa.Column('body', sa.String(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Copy data from old savedrequest table to temp table
    op.execute('''
        INSERT INTO savedrequest_temp (id, created_at, updated_at, name, method, 
                                     url, headers, parameters, body, user_id)
        SELECT rowid, created_at, updated_at, name, method, url, 
               headers, parameters, body, user_id
        FROM savedrequest
    ''')
    
    # Drop old savedrequest table
    op.drop_table('savedrequest')
    
    # Rename temp table to savedrequest
    op.rename_table('savedrequest_temp', 'savedrequest')
    
    op.execute('PRAGMA foreign_keys=ON')


def downgrade() -> None:
    # Revert changes if needed
    pass
