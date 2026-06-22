import React from 'react';
import { Layout, Typography, Button, Space, Avatar, Dropdown } from 'antd';
import { UserOutlined, LogoutOutlined, SettingOutlined, ApiOutlined, BookOutlined, ControlOutlined } from '@ant-design/icons';
import keycloak from '../services/keycloak';

const { Header } = Layout;
const { Title } = Typography;

interface HeaderComponentProps {
  onSettingsOpen: () => void;
  settingButtonStatus: boolean;
  onBackToHome?: () => void;
  onMcpConfigOpen?: () => void;
  onSkillsHubOpen?: () => void;
  onGlobalParamsOpen?: () => void;
  canManageSettings?: boolean;
  canManageMcp?: boolean;
  canManageSkills?: boolean;
  canManageGlobalParams?: boolean;
}

const HeaderComponent: React.FC<HeaderComponentProps> = ({
  onSettingsOpen,
  settingButtonStatus,
  onBackToHome,
  onMcpConfigOpen,
  onSkillsHubOpen,
  onGlobalParamsOpen,
  canManageSettings = false,
  canManageMcp = false,
  canManageSkills = false,
  canManageGlobalParams = false,
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
    <Header style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      background: 'var(--color-bg-elevated)',
      padding: '0 var(--space-6)',
      borderBottom: '1px solid var(--color-border)',
      height: 'var(--header-height)',
      lineHeight: 'var(--header-height)',
    }}>
      <Title
        onClick={onBackToHome}
        level={3}
        style={{
          margin: 0,
          color: 'var(--color-primary)',
          cursor: 'pointer',
          letterSpacing: 'var(--letter-spacing-tight)',
          fontWeight: 'var(--font-weight-bold)',
          transition: 'color 200ms ease, opacity 200ms ease',
        }}
        onMouseEnter={(e) => { e.currentTarget.style.opacity = '0.8'; }}
        onMouseLeave={(e) => { e.currentTarget.style.opacity = '1'; }}
      >
        测试用例生成工具
      </Title>
      <div>
        <Space size="middle">
          {canManageMcp && <Button type="default" onClick={onMcpConfigOpen} icon={<ApiOutlined />}>MCP</Button>}
          {canManageGlobalParams && <Button type="default" onClick={onGlobalParamsOpen} icon={<ControlOutlined />}>全局参数</Button>}
          {canManageSkills && <Button type="default" onClick={onSkillsHubOpen} icon={<BookOutlined />}>Skills</Button>}
          {canManageSettings && (
            <Button type="default" onClick={onSettingsOpen} disabled={settingButtonStatus} icon={<SettingOutlined />}>设置</Button>
          )}
          <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
            <Space style={{ cursor: 'pointer' }}>
              <Avatar size="small" icon={<UserOutlined />} style={{ backgroundColor: 'var(--color-primary)' }} />
              <span style={{ fontSize: 'var(--font-size-base)', color: 'var(--color-text)' }}>{username}</span>
            </Space>
          </Dropdown>
        </Space>
      </div>
    </Header>
  );
};

export default HeaderComponent;
