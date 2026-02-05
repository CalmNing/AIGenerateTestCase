import React from 'react';
import { Layout, Typography, Button, Space } from 'antd';


const { Header } = Layout;
const { Title } = Typography;

interface HeaderComponentProps {
  onSettingsOpen: () => void;
  settingButtonStatus: boolean;
  onBackToHome?: () => void;
}

const HeaderComponent: React.FC<HeaderComponentProps> = ({ onSettingsOpen, settingButtonStatus, onBackToHome }) => {
  return (
    <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: '#fff', padding: '0 24px', borderBottom: '1px solid #f0f0f0' }}>
      <Title level={3} style={{ margin: 0, color: '#1890ff' }}>测试用例生成工具</Title>
      <div>
        <Space>
          {onBackToHome && (
            <Button type="default" onClick={onBackToHome}>返回首页</Button>
          )}
          {/* <Button type="primary" icon={<PlusOutlined />}>新建会话</Button> */}
          <Button type="default" onClick={onSettingsOpen} disabled={settingButtonStatus}>设置</Button>
        </Space>
      </div>
    </Header>
  );
};

export default HeaderComponent;