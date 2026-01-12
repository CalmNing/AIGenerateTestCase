import React, { useEffect, useState } from 'react';
import { Layout, Menu, Typography, Button, Input, Space, Tooltip } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
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
}

const SessionSidebar: React.FC<SessionSidebarProps> = ({
  sessions,
  testcases,
  selectedSession,
  newSessionName,
  onNewSessionNameChange,
  onCreateSession,
  onSelectSession,
  onDeleteSession
}) => {
  const disableTipText = "存在「已执行」的测试用例，无法删除";
  const [disableTip, setDisableTip] = useState('')
  const [buttonSelectedSession, setButtonSelectedSession] = useState(false)
  const [selectedSessionId, setSelectedSessionId] = useState<Number | undefined>(0)
  useEffect(() => {
    if (testcases){
      setButtonSelectedSession(false)
      setDisableTip('')
    }
    if (testcases.some(tc => tc.status === TestCaseStatus.PASSED|| tc.status===TestCaseStatus.FAILED)) {
      setButtonSelectedSession(true)
      setDisableTip(disableTipText)
    }
    else {
      setButtonSelectedSession(false)
      setDisableTip('')
    }
    setSelectedSessionId(selectedSession?.id)
  }, [testcases])

  const menuItems = sessions.map(session => ({
    key: session.id,
    label: (
      <Space>
        <span style={{
          fontSize: "16px",
          lineHeight: "1.5",
          width: "160px", // 1. 设置固定宽度（必填，可根据需求调整，如 300px、50% 等）
          height: "40px", // 固定容器高度
          display: "flex", // 开启 flex 布局
          alignItems: "center", // 垂直方向（上下）居中对齐
          whiteSpace: "nowrap", // 3. 禁止文本换行，保持单行显示
          overflow: "hidden", // 4. 隐藏超出容器宽度的内容
          textOverflow: "ellipsis", // 5. 可选：超出部分显示省略号（...），优化用户体验
        }}>{session.name}</span>
       <Tooltip
          title={disableTip}
          mouseEnterDelay={0.2} // 可选：设置 hover 延迟，避免误触
          placement="top" // 可选：设置提示显示位置（top/bottom/left/right）
        >
          {selectedSessionId === session?.id && <Button
            type="text"
            danger
            icon={<DeleteOutlined />}
            size="small"
            disabled={buttonSelectedSession}
            onClick={(e) => {
              e.stopPropagation();
              onDeleteSession(session.id);
              setSelectedSessionId(session.id)
            }}
          />}
        </Tooltip>
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