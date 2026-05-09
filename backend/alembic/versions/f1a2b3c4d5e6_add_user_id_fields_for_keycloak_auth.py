"""add user_id fields for keycloak auth

Revision ID: f1a2b3c4d5e6
Revises: 
Create Date: 2026-05-09 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e6'
down_revision = 'add_parent_id_to_module'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 为 Session 表添加 user_id 字段
    op.add_column('session', sa.Column('user_id', sa.String(), nullable=True))
    op.create_index('ix_session_user_id', 'session', ['user_id'])

    # 为 TestCase 表添加 user_id 字段
    op.add_column('testcase', sa.Column('user_id', sa.String(), nullable=True))
    op.create_index('ix_testcase_user_id', 'testcase', ['user_id'])

    # 为 Module 表添加 user_id 字段
    op.add_column('module', sa.Column('user_id', sa.String(), nullable=True))
    op.create_index('ix_module_user_id', 'module', ['user_id'])

    # SavedRequest 表：将已有 user_id (INTEGER) 改为 STRING
    # SQLite 不支持 ALTER COLUMN，需要重建表
    with op.batch_alter_table('savedrequest') as batch_op:
        batch_op.alter_column('user_id', existing_type=sa.Integer(), type_=sa.String(), existing_nullable=True)
    op.create_index('ix_savedrequest_user_id', 'savedrequest', ['user_id'])

    # GlobalParameter 表：将已有 user_id (INTEGER) 改为 STRING
    with op.batch_alter_table('globalparameter') as batch_op:
        batch_op.alter_column('user_id', existing_type=sa.Integer(), type_=sa.String(), existing_nullable=True)
    op.create_index('ix_globalparameter_user_id', 'globalparameter', ['user_id'])

    # 为 HistoryPrompt 表添加 user_id 字段
    op.add_column('historyprompt', sa.Column('user_id', sa.String(), nullable=True))
    op.create_index('ix_historyprompt_user_id', 'historyprompt', ['user_id'])

    # 为 ScheduledTask 表添加 user_id 字段
    op.add_column('scheduledtask', sa.Column('user_id', sa.String(), nullable=True))
    op.create_index('ix_scheduledtask_user_id', 'scheduledtask', ['user_id'])

    # 为 MockConfig 表添加 user_id 字段
    op.add_column('mockconfig', sa.Column('user_id', sa.String(), nullable=True))
    op.create_index('ix_mockconfig_user_id', 'mockconfig', ['user_id'])


def downgrade() -> None:
    # Session
    op.drop_index('ix_session_user_id', 'session')
    op.drop_column('session', 'user_id')

    # TestCase
    op.drop_index('ix_testcase_user_id', 'testcase')
    op.drop_column('testcase', 'user_id')

    # Module
    op.drop_index('ix_module_user_id', 'module')
    op.drop_column('module', 'user_id')

    # SavedRequest: 还原为 INTEGER
    with op.batch_alter_table('savedrequest') as batch_op:
        batch_op.alter_column('user_id', existing_type=sa.String(), type_=sa.Integer(), existing_nullable=True)
    op.drop_index('ix_savedrequest_user_id', 'savedrequest')

    # GlobalParameter: 还原为 INTEGER
    with op.batch_alter_table('globalparameter') as batch_op:
        batch_op.alter_column('user_id', existing_type=sa.String(), type_=sa.Integer(), existing_nullable=True)
    op.drop_index('ix_globalparameter_user_id', 'globalparameter')

    # HistoryPrompt
    op.drop_index('ix_historyprompt_user_id', 'historyprompt')
    op.drop_column('historyprompt', 'user_id')

    # ScheduledTask
    op.drop_index('ix_scheduledtask_user_id', 'scheduledtask')
    op.drop_column('scheduledtask', 'user_id')

    # MockConfig
    op.drop_index('ix_mockconfig_user_id', 'mockconfig')
    op.drop_column('mockconfig', 'user_id')
