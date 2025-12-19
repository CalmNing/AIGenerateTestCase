import React from 'react';
import { Layout, Typography, Button, Space } from 'antd';
import { PlusOutlined } from '@ant-design/icons';

const { Header } = Layout;
const { Title } = Typography;

interface HeaderComponentProps {
  onSettingsOpen: () => void;
}

const HeaderComponent: React.FC<HeaderComponentProps> = ({ onSettingsOpen }) => {
  return (
    <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: '#fff', padding: '0 24px', borderBottom: '1px solid #f0f0f0' }}>
      <Title level={3} style={{ margin: 0, color: '#1890ff' }}>测试用例生成工具</Title>
      <div>
        <Space>
          {/* <Button type="primary" icon={<PlusOutlined />}>新建会话</Button> */}
          <Button type="default" onClick={onSettingsOpen}>设置</Button>
        </Space>
      </div>
    </Header>
  );
};

export default HeaderComponent;