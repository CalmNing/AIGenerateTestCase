import React from 'react';
import { Button, Space, Avatar, Dropdown } from 'antd';
import { EnvironmentOutlined, UserOutlined, LogoutOutlined } from '@ant-design/icons';
import keycloak from '../services/keycloak';

interface SubPlatformHeaderProps {
  title: string;
  onBackToHome: () => void;
  environmentName?: string;
  onGlobalParamsOpen?: () => void;
  canManageGlobalParams?: boolean;
}

const SubPlatformHeader: React.FC<SubPlatformHeaderProps> = ({
  title,
  onBackToHome,
  environmentName,
  onGlobalParamsOpen,
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
    <div style={{
      padding: '0 var(--space-6)',
      background: 'var(--color-bg-elevated)',
      borderBottom: '1px solid var(--color-border)',
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      height: 'var(--header-height)',
      minHeight: 'var(--header-height)',
    }}>
      <h2
        onClick={onBackToHome}
        style={{
          margin: 0,
          cursor: 'pointer',
          fontSize: 'var(--font-size-lg)',
          fontWeight: 'var(--font-weight-semibold)',
          color: 'var(--color-text)',
          letterSpacing: 'var(--letter-spacing-tight)',
        }}
      >
        {title}
      </h2>
      <Space size="middle">
        {environmentName && (
          <span style={{ color: 'var(--color-text-secondary)', fontSize: 'var(--font-size-base)' }}>
            当前环境: <strong>{environmentName}</strong>
          </span>
        )}
        {onGlobalParamsOpen && canManageGlobalParams && (
          <Button
            icon={<EnvironmentOutlined />}
            onClick={onGlobalParamsOpen}
          >
            全局参数
          </Button>
        )}
        <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
          <Space style={{ cursor: 'pointer' }}>
            <Avatar size="small" icon={<UserOutlined />} style={{ backgroundColor: 'var(--color-primary)' }} />
            <span style={{ fontSize: 'var(--font-size-base)', color: 'var(--color-text)' }}>{username}</span>
          </Space>
        </Dropdown>
      </Space>
    </div>
  );
};

export default SubPlatformHeader;
