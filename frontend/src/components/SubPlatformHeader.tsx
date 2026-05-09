import React from 'react';
import { Button, Space, Avatar, Dropdown } from 'antd';
import { EnvironmentOutlined, UserOutlined, LogoutOutlined } from '@ant-design/icons';
import keycloak from '../services/keycloak';

interface SubPlatformHeaderProps {
  title: string;
  onBackToHome: () => void;
  environmentName?: string;
  onGlobalParamsOpen?: () => void;
}

const SubPlatformHeader: React.FC<SubPlatformHeaderProps> = ({
  title,
  onBackToHome,
  environmentName,
  onGlobalParamsOpen,
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
      padding: '16px',
      background: '#fff',
      borderBottom: '1px solid #e8e8e8',
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
    }}>
      <h2 onClick={onBackToHome} style={{ margin: 0, cursor: 'pointer' }}>{title}</h2>
      <Space size="middle">
        {environmentName && (
          <span style={{ color: '#666', fontSize: '14px' }}>
            当前环境: <strong>{environmentName}</strong>
          </span>
        )}
        {onGlobalParamsOpen && (
          <Button
            icon={<EnvironmentOutlined />}
            onClick={onGlobalParamsOpen}
          >
            全局参数
          </Button>
        )}
        <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
          <Space style={{ cursor: 'pointer' }}>
            <Avatar size="small" icon={<UserOutlined />} style={{ backgroundColor: '#1890ff' }} />
            <span style={{ fontSize: '14px', color: '#333' }}>{username}</span>
          </Space>
        </Dropdown>
      </Space>
    </div>
  );
};

export default SubPlatformHeader;
