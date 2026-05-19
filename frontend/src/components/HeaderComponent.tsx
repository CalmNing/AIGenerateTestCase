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
}

const HeaderComponent: React.FC<HeaderComponentProps> = ({ onSettingsOpen, settingButtonStatus, onBackToHome, onMcpConfigOpen, onSkillsHubOpen }) => {
  // 获取当前用户名
  const username = keycloak.tokenParsed?.preferred_username || keycloak.tokenParsed?.sub || '用户';

  // 登出处理
  const handleLogout = () => {
    keycloak.logout({
      redirectUri: window.location.origin,
    });
  };

  // 用户下拉菜单
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
          <Button type="default" onClick={onMcpConfigOpen} icon={<ApiOutlined />}>MCP</Button>
          <Button type="default" onClick={onSkillsHubOpen} icon={<BookOutlined />}>Skills</Button>
          <Button type="default" onClick={onSettingsOpen} disabled={settingButtonStatus} icon={<SettingOutlined />}>设置</Button>
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
