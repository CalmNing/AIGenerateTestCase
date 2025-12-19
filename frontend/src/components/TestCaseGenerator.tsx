import React from 'react';
import { Card, Typography, Input, Button } from 'antd';
import { Session } from '../types';

const { Text } = Typography;

interface TestCaseGeneratorProps {
  selectedSession: Session | null;
  requirement: string;
  loading: boolean;
  onRequirementChange: (value: string) => void;
  onGenerate: () => void;
}

const TestCaseGenerator: React.FC<TestCaseGeneratorProps> = ({
  selectedSession,
  requirement,
  loading,
  onRequirementChange,
  onGenerate
}) => {
  return (
    <Card title="生成测试用例" variant="borderless">
      {!selectedSession ? (
        <div style={{ textAlign: 'center', padding: '40px 0' }}>
          <Text type="secondary">请先从左侧选择或创建一个会话</Text>
        </div>
      ) : (
        <div>
          <div style={{ marginBottom: '16px' }}>
            <Text strong>当前会话:</Text> <Text>{selectedSession.name}</Text>
          </div>
          <div style={{ marginBottom: '16px' }}>
            <Input.TextArea
              rows={6}
              placeholder="请输入测试用例设计需求，例如：设计一个登录功能的测试用例，包括正常登录、异常登录等场景..."
              value={requirement}
              onChange={(e) => onRequirementChange(e.target.value)}
            />
          </div>
          <Button
            type="primary"
            size="large"
            onClick={onGenerate}
            loading={loading}
            disabled={!requirement.trim()}
            block
          >
            生成测试用例
          </Button>
        </div>
      )}
    </Card>
  );
};

export default TestCaseGenerator;