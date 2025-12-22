import React from 'react';
import { Layout, Menu, Typography, Button, Input, Space } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import { Session } from '../types';

const { Sider } = Layout;
const { Title } = Typography;

interface SessionSidebarProps {
  sessions: Session[];
  selectedSession: Session | null;
  newSessionName: string;
  onNewSessionNameChange: (value: string) => void;
  onCreateSession: () => void;
  onSelectSession: (session: Session) => void;
  onDeleteSession: (id: number) => void;
}

const SessionSidebar: React.FC<SessionSidebarProps> = ({
  sessions,
  selectedSession,
  newSessionName,
  onNewSessionNameChange,
  onCreateSession,
  onSelectSession,
  onDeleteSession
}) => {
  const menuItems = sessions.map(session => ({
    key: session.id,
    label: (
      <Space>
        <span>{session.name}</span>
        <Button
          type="text"
          danger
          icon={<DeleteOutlined />}
          size="small"
          onClick={(e) => {
            e.stopPropagation();
            onDeleteSession(session.id);
          }}
        />
      </Space>
    ),
    onClick: () => onSelectSession(session)
  }));

  return (
    <Sider width={240} style={{ background: '#fff', borderRight: '1px solid #f0f0f0', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '16px', borderBottom: '1px solid #f0f0f0' }}>
        <Title level={5} style={{ margin: 0 }}>会话管理</Title>
        <div style={{ marginTop: '8px' }}>
          <Input
            placeholder="输入会话名称"
            value={newSessionName}
            onChange={(e) => onNewSessionNameChange(e.target.value)}
            onPressEnter={onCreateSession}
            style={{ marginBottom: '8px' }}
          />
          <Button type="primary" block onClick={onCreateSession} icon={<PlusOutlined />}>
            创建会话
          </Button>
        </div>
      </div>
      <Menu
        mode="inline"
        selectedKeys={selectedSession ? [String(selectedSession.id)] : []}
        style={{ borderRight: 0, flex: 1, overflow: 'auto' }}
        items={menuItems}
      />
    </Sider>
  );
};

export default SessionSidebar;