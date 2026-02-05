"""Fix database schema inconsistencies"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite


revision = 'fix_database_schema'
down_revision = 'be3a6233c1bd'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fix testcase table: session_id type issue
    # SQLite doesn't support ALTER COLUMN, so we need to recreate the table
    op.execute('PRAGMA foreign_keys=OFF')
    
    # Create temporary testcase table with correct schema
    op.create_table('testcase_temp',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('case_name', sa.String(), nullable=True),
        sa.Column('case_level', sa.Integer(), nullable=True),
        sa.Column('preset_conditions', sqlite.JSON(), nullable=True),
        sa.Column('steps', sqlite.JSON(), nullable=True),
        sa.Column('expected_results', sqlite.JSON(), nullable=True),
        sa.Column('session_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('bug_id', sa.Integer(), nullable=True),
        sa.Column('module_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Copy data from old testcase table to temp table
    op.execute('''
        INSERT INTO testcase_temp (id, created_at, updated_at, case_name, case_level, 
                                 preset_conditions, steps, expected_results, session_id, 
                                 status, bug_id, module_id)
        SELECT id, created_at, updated_at, case_name, case_level, 
               preset_conditions, steps, expected_results, 
               CASE WHEN session_id IS NOT NULL AND session_id != '' THEN CAST(session_id AS INTEGER) ELSE NULL END, 
               status, bug_id, module_id
        FROM testcase
    ''')
    
    # Drop old testcase table
    op.drop_table('testcase')
    
    # Rename temp table to testcase
    op.rename_table('testcase_temp', 'testcase')
    
    # Fix savedrequest table: add id column and correct field types
    # First check if savedrequest table exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'savedrequest' in inspector.get_table_names():
        # Create temporary savedrequest table with correct schema
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
            SELECT rowid, 
                   CASE WHEN created_at IS NOT NULL THEN datetime(created_at) ELSE datetime('now') END, 
                   CASE WHEN updated_at IS NOT NULL THEN datetime(updated_at) ELSE datetime('now') END, 
                   name, method, url, 
                   CASE WHEN headers IS NOT NULL THEN json(headers) ELSE '[]' END, 
                   CASE WHEN parameters IS NOT NULL THEN json(parameters) ELSE '[]' END, 
                   body, user_id
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
