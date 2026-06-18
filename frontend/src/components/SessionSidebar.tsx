import React from 'react';
import { Layout, Menu, Typography, Button, Input, Space, Popover } from 'antd';
import { PlusOutlined, MessageTwoTone, EllipsisOutlined } from '@ant-design/icons';
import { Session, TestCase, TestCaseStatus } from '../types';

const { Sider } = Layout;
const { Title } = Typography;

// 让会话名称可以完整显示
const sessionMenuStyle = `
  .session-sidebar .ant-menu-title-content {
    flex: 1 1 0% !important;
    min-width: 0 !important;
  }
`;

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
  // 组件现在不再需要这些状态变量，直接在Popover中计算禁用状态

  const menuItems = sessions.map(session => ({
    key: session.id,
    label: (
      <Space size={4}>
        <span style={{
          fontSize: "var(--font-size-base)",
          lineHeight: "40px",
          maxWidth: "140px",
          height: "40px",
          display: "block",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}>{session.name}</span>

        <Popover
          content={(
            <Space direction="vertical" size="small">
              <Button
                type="text"
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenEditSessionModal(session);
                }}
              >
                编辑会话
              </Button>
              <Button
                type="text"
                size="small"
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
                disabled={testcases.some(tc => tc.session_id === session.id && (tc.status === TestCaseStatus.PASSED || tc.status === TestCaseStatus.FAILED))}
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteSession(session.id);
                }}
              >
                删除会话
              </Button>
            </Space>
          )}
          // title="操作"
          trigger="click"
          placement="bottom"
        >
          <Button
            type="text"
            icon={<EllipsisOutlined />}
            size="small"
            onClick={(e) => e.stopPropagation()}
          />
        </Popover>
      </Space>
    ),
    icon: <MessageTwoTone />,
    onClick: () => onSelectSession(session)
  }));

  return (
    <Sider width={240} className="session-sidebar" style={{ background: 'var(--color-bg-elevated)', borderRight: '1px solid var(--color-border)', display: 'flex', flexDirection: 'column' }}>
      <style>{sessionMenuStyle}</style>
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
      <Menu
        mode="vertical"
        style={{
          margin: '0',
          maxHeight: 'calc(100vh - 210px)',
          overflow: 'auto',
          borderRight: 'none',
        }}
        selectedKeys={selectedSession ? [String(selectedSession.id)] : []}
        items={menuItems}
      />
    </Sider>
  );
};

export default SessionSidebar;