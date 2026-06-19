import React from 'react';
import { Layout, Typography, Button, Input, Popover, Space } from 'antd';
import { PlusOutlined, MessageOutlined, EllipsisOutlined, EditOutlined, FolderAddOutlined, DeleteOutlined } from '@ant-design/icons';
import { Session, TestCase, TestCaseStatus } from '../types';

const { Sider } = Layout;
const { Title } = Typography;

interface SessionSidebarProps {
  sessions: Session[];
  testcases: TestCase[];
  selectedSession: Session | null;
  newSessionName: string;
  onNewSessionNameChange: (value: string) => void;
  onCreateSession: () => void;
  onSelectSession: (session: Session) => void;
  onDeleteSession: (id: number) => void;
  onOpenAddModuleModal: () => void;
  onOpenEditSessionModal: (session: Session) => void;
}

const SessionSidebar: React.FC<SessionSidebarProps> = ({
  sessions,
  testcases,
  selectedSession,
  newSessionName,
  onNewSessionNameChange,
  onCreateSession,
  onSelectSession,
  onDeleteSession,
  onOpenAddModuleModal,
  onOpenEditSessionModal
}) => {
  return (
    <Sider width={240} className="session-sidebar" style={{ background: 'var(--color-bg-elevated)', borderRight: '1px solid var(--color-border)' }}>
      <div style={{ padding: 'var(--space-4)', borderBottom: '1px solid var(--color-border)' }}>
        <Title level={5} style={{ margin: 0, fontSize: 'var(--font-size-base)', fontWeight: 'var(--font-weight-semibold)' }}>会话管理</Title>
        <div style={{ marginTop: 'var(--space-2)' }}>
          <Input
            placeholder="输入会话名称"
            value={newSessionName}
            onChange={(e) => onNewSessionNameChange(e.target.value)}
            onPressEnter={onCreateSession}
            style={{ marginBottom: 'var(--space-2)' }}
          />
          <Button type="primary" block onClick={onCreateSession} icon={<PlusOutlined />}>
            创建会话
          </Button>
        </div>
      </div>
      <div style={{ maxHeight: 'calc(100vh - 210px)', overflowY: 'auto', padding: '4px 0' }}>
        {sessions.map(session => {
          const isSelected = selectedSession?.id === session.id;
          return (
            <div
              key={session.id}
              className="session-item"
              onClick={() => onSelectSession(session)}
              style={{
                display: 'flex',
                alignItems: 'center',
                height: 44,
                padding: '0 12px',
                margin: '2px 8px',
                borderRadius: 'var(--radius-lg)',
                cursor: 'pointer',
                transition: 'background-color 200ms ease, box-shadow 200ms ease',
                background: isSelected ? 'var(--color-primary-bg)' : 'transparent',
                boxShadow: isSelected ? 'inset 3px 0 0 0 var(--color-primary)' : 'none',
              }}
              onMouseEnter={(e) => {
                if (!isSelected) e.currentTarget.style.background = 'var(--color-border-light)';
              }}
              onMouseLeave={(e) => {
                if (!isSelected) e.currentTarget.style.background = 'transparent';
              }}
            >
              <span style={{
                flex: '0 0 auto',
                fontSize: 16,
                color: isSelected ? 'var(--color-primary)' : 'var(--color-text-tertiary)',
                transition: 'color 200ms ease',
                marginRight: 8,
              }}>
                <MessageOutlined />
              </span>
              <span style={{
                flex: '1 1 0',
                minWidth: 0,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                fontSize: 'var(--font-size-base)',
                color: isSelected ? 'var(--color-primary)' : 'var(--color-text)',
                fontWeight: isSelected ? 'var(--font-weight-medium)' : 'var(--font-weight-normal)',
                transition: 'color 200ms ease',
              }}>
                {session.name}
              </span>
              <div className="session-item-actions" style={{
                flex: '0 0 auto',
                opacity: 0,
                transition: 'opacity 150ms ease',
              }}>
                <Popover
                  content={(
                    <Space direction="vertical" size={4} style={{ minWidth: 100 }}>
                      <Button
                        type="text"
                        size="small"
                        block
                        icon={<EditOutlined />}
                        onClick={(e) => {
                          e.stopPropagation();
                          onOpenEditSessionModal(session);
                        }}
                      >
                        编辑
                      </Button>
                      <Button
                        type="text"
                        size="small"
                        block
                        icon={<FolderAddOutlined />}
                        onClick={(e) => {
                          e.stopPropagation();
                          onOpenAddModuleModal();
                        }}
                      >
                        新增模块
                      </Button>
                      <Button
                        type="text"
                        danger
                        size="small"
                        block
                        icon={<DeleteOutlined />}
                        disabled={testcases.some(tc => tc.session_id === session.id && (tc.status === TestCaseStatus.PASSED || tc.status === TestCaseStatus.FAILED))}
                        onClick={(e) => {
                          e.stopPropagation();
                          onDeleteSession(session.id);
                        }}
                      >
                        删除
                      </Button>
                    </Space>
                  )}
                  trigger="click"
                  placement="bottomRight"
                >
                  <Button
                    type="text"
                    icon={<EllipsisOutlined />}
                    size="small"
                    onClick={(e) => e.stopPropagation()}
                    style={{ padding: 0, height: 24, width: 24, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                  />
                </Popover>
              </div>
            </div>
          );
        })}
      </div>
    </Sider>
  );
};

export default SessionSidebar;
