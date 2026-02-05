import React from 'react';
import { Card, Row, Col, Typography, Space } from 'antd';
import { RocketOutlined, ApiOutlined } from '@ant-design/icons';

const { Title, Paragraph } = Typography;

interface HomePageProps {
  onNavigateToAI: () => void;
  onNavigateToIoT: () => void;
}

const HomePage: React.FC<HomePageProps> = ({ onNavigateToAI, onNavigateToIoT }) => {
  return (
    <div style={{ 
      padding: '40px', 
      minHeight: '100vh', 
      background: '#f0f2f5',
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'center'
    }}>
      <div style={{ textAlign: 'center', marginBottom: '60px' }}>
        <Title level={2} style={{ 
          color: '#1890ff',
          marginBottom: '16px'
        }}>
          测试工具平台
        </Title>
        <Paragraph style={{ 
          fontSize: '16px',
          color: '#666'
        }}>
          选择您需要的工具平台
        </Paragraph>
      </div>
      
      <Row 
        gutter={[48, 48]} 
        justify="center"
        style={{ flex: 1, alignItems: 'center' }}
      >
        <Col xs={24} sm={12} md={8}>
          <Card
            hoverable
            cover={
              <div style={{ 
                height: '220px', 
                background: '#e6f7ff', 
                display: 'flex', 
                alignItems: 'center', 
                justifyContent: 'center',
                fontSize: '90px',
                transition: 'all 0.3s ease'
              }}>
                <RocketOutlined style={{ color: '#1890ff' }} />
              </div>
            }
            onClick={onNavigateToAI}
            style={{ 
              cursor: 'pointer',
              boxShadow: '0 4px 12px rgba(0, 0, 0, 0.1)',
              borderRadius: '12px',
              transition: 'all 0.3s ease',
              border: '1px solid #e8e8e8'
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.transform = 'translateY(-4px)';
              e.currentTarget.style.boxShadow = '0 8px 24px rgba(0, 0, 0, 0.15)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.transform = 'translateY(0)';
              e.currentTarget.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.1)';
            }}
          >
            <Space direction="vertical" size="middle" style={{ width: '100%', padding: '0 16px' }}>
              <Title level={4} style={{ 
                textAlign: 'center',
                marginBottom: '12px',
                color: '#333'
              }}>
                AI测试用例平台
              </Title>
              <Paragraph style={{ 
                textAlign: 'center',
                color: '#666',
                lineHeight: '1.6'
              }}>
                使用AI智能生成测试用例，提高测试效率和覆盖率。支持文本和图片输入，自动生成符合标准的测试用例。
              </Paragraph>
            </Space>
          </Card>
        </Col>
        
        <Col xs={24} sm={12} md={8}>
          <Card
            hoverable
            cover={
              <div style={{ 
                height: '220px', 
                background: '#f6ffed', 
                display: 'flex', 
                alignItems: 'center', 
                justifyContent: 'center',
                fontSize: '90px',
                transition: 'all 0.3s ease'
              }}>
                <ApiOutlined style={{ color: '#52c41a' }} />
              </div>
            }
            onClick={onNavigateToIoT}
            style={{ 
              cursor: 'pointer',
              boxShadow: '0 4px 12px rgba(0, 0, 0, 0.1)',
              borderRadius: '12px',
              transition: 'all 0.3s ease',
              border: '1px solid #e8e8e8'
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.transform = 'translateY(-4px)';
              e.currentTarget.style.boxShadow = '0 8px 24px rgba(0, 0, 0, 0.15)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.transform = 'translateY(0)';
              e.currentTarget.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.1)';
            }}
          >
            <Space direction="vertical" size="middle" style={{ width: '100%', padding: '0 16px' }}>
              <Title level={4} style={{ 
                textAlign: 'center',
                marginBottom: '12px',
                color: '#333'
              }}>
                IoT Mock平台
              </Title>
              <Paragraph style={{ 
                textAlign: 'center',
                color: '#666',
                lineHeight: '1.6'
              }}>
                类似Postman的接口调用工具，支持各种HTTP方法，可用于测试和模拟IoT设备接口。
              </Paragraph>
            </Space>
          </Card>
        </Col>
      </Row>
      
      <div style={{ 
        textAlign: 'center', 
        marginTop: '60px',
        paddingBottom: '40px'
      }}>
        <Paragraph style={{ color: '#999', fontSize: '14px' }}>
          © 2026 测试工具平台 | 版本 1.0.0
        </Paragraph>
      </div>
    </div>
  );
};

export default HomePage;