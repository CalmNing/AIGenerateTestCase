import React from 'react';
import { Layout, Typography, Button, Space, Avatar, Dropdown } from 'antd';
import { UserOutlined, LogoutOutlined, SettingOutlined, ApiOutlined, BookOutlined } from '@ant-design/icons';
import keycloak from '../services/keycloak';

const { Header } = Layout;
const { Title } = Typography;

interface HeaderComponentProps {
  onSettingsOpen: () => void;
  settingButtonStatus: boolean;
  onBackToHome?: () => void;
  onMcpConfigOpen?: () => void;
  onSkillsHubOpen?: () => void;
  canManageSettings?: boolean;
  canManageMcp?: boolean;
  canManageSkills?: boolean;
}

const HeaderComponent: React.FC<HeaderComponentProps> = ({
  onSettingsOpen,
  settingButtonStatus,
  onBackToHome,
  onMcpConfigOpen,
  onSkillsHubOpen,
  canManageSettings = false,
  canManageMcp = false,
  canManageSkills = false,
}) => {
  const username = keycloak.tokenParsed?.preferred_username || keycloak.tokenParsed?.sub || '用户';

  const handleLogout = () => {
    keycloak.logout({
      redirectUri: window.location.origin,
    });
  };

  const userMenuItems = [
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      onClick: handleLogout,
    },
  ];

  return (
    <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: '#fff', padding: '0 24px', borderBottom: '1px solid #f0f0f0' }}>
      <Title onClick={onBackToHome} level={3} style={{ margin: 0, color: '#1890ff', cursor: 'pointer' }}>测试用例生成工具</Title>
      <div>
        <Space size="middle">
          {canManageMcp && <Button type="default" onClick={onMcpConfigOpen} icon={<ApiOutlined />}>MCP</Button>}
          {canManageSkills && <Button type="default" onClick={onSkillsHubOpen} icon={<BookOutlined />}>Skills</Button>}
          {canManageSettings && (
            <Button type="default" onClick={onSettingsOpen} disabled={settingButtonStatus} icon={<SettingOutlined />}>设置</Button>
          )}
          <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
            <Space style={{ cursor: 'pointer' }}>
              <Avatar size="small" icon={<UserOutlined />} style={{ backgroundColor: '#1890ff' }} />
              <span style={{ fontSize: '14px', color: '#333' }}>{username}</span>
            </Space>
          </Dropdown>
        </Space>
      </div>
    </Header>
  );
};

export default HeaderComponent;
