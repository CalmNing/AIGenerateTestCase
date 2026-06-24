import React, { useState, useCallback } from 'react';
import { FloatButton, Popover, Tabs, Button, Space, message, Spin } from 'antd';
import {
  FunctionOutlined,
  CopyOutlined,
  ThunderboltOutlined,
  EnvironmentOutlined,
} from '@ant-design/icons';
import { proxyApi } from '../services/api';
import './VariableAssistant.css';

interface VariableAssistantProps {
  environmentId?: number;
  environmentVariables?: Array<{ key: string; value: string }>;
  onInsert?: (syntax: string) => void;
}

interface VariableItemDef {
  syntax: string;
  description: string;
}

const builtinFunctions: VariableItemDef[] = [
  { syntax: '{{$timestamp}}', description: '毫秒级时间戳（13位）' },
  { syntax: '{{$now}}', description: '秒级时间戳（10位）' },
  { syntax: '{{$date}}', description: '当前日期，格式 YYYY-MM-DD' },
  { syntax: "{{$date('YYYY-MM-DD HH:mm:ss')}}", description: '自定义格式日期' },
  { syntax: '{{$randomInt}}', description: '0 ~ 100 的随机整数' },
  { syntax: '{{$randomInt(1,1000)}}', description: '指定范围的随机整数' },
  { syntax: '{{$uuid}}', description: 'UUID v4' },
];

const jsExpressions: VariableItemDef[] = [
  { syntax: '{{@Date.now()}}', description: 'JS 时间戳（毫秒）' },
  { syntax: '{{@Math.random().toFixed(4)}}', description: '0~1 随机小数，保留4位' },
  { syntax: '{{@new Date().toISOString()}}', description: 'ISO 8601 日期字符串' },
  { syntax: "{{@'test_' + Math.floor(Math.random()*1000)}}", description: '拼接随机字符串' },
];

interface TestResult {
  result?: string;
  error?: string;
  loading?: boolean;
}

const VariableAssistant: React.FC<VariableAssistantProps> = ({
  environmentId,
  environmentVariables,
  onInsert,
}) => {
  const [open, setOpen] = useState(false);
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});

  const handleInsert = useCallback(
    (syntax: string) => {
      const activeElement = document.activeElement as
        | HTMLInputElement
        | HTMLTextAreaElement;
      if (
        activeElement &&
        (activeElement.tagName === 'INPUT' ||
          activeElement.tagName === 'TEXTAREA')
      ) {
        const start = activeElement.selectionStart || 0;
        const end = activeElement.selectionEnd || 0;
        const value = activeElement.value;
        activeElement.value =
          value.slice(0, start) + syntax + value.slice(end);
        // Trigger React onChange - use correct prototype based on element type
        const prototype =
          activeElement.tagName === 'INPUT'
            ? window.HTMLInputElement.prototype
            : window.HTMLTextAreaElement.prototype;
        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
          prototype,
          'value'
        )?.set;
        if (nativeInputValueSetter) {
          nativeInputValueSetter.call(activeElement, activeElement.value);
        }
        activeElement.dispatchEvent(new Event('input', { bubbles: true }));
        activeElement.dispatchEvent(new Event('change', { bubbles: true }));
        activeElement.selectionStart = start + syntax.length;
        activeElement.selectionEnd = start + syntax.length;
        activeElement.focus();
        message.success(`已插入: ${syntax}`);
        onInsert?.(syntax);
      } else {
        navigator.clipboard.writeText(syntax).catch(() => {
          message.error('复制失败');
        });
        message.success('已复制到剪贴板');
      }
    },
    [onInsert]
  );

  const handleCopy = useCallback((syntax: string) => {
    navigator.clipboard.writeText(syntax).catch(() => {
      message.error('复制失败');
    });
    message.success('已复制到剪贴板');
  }, []);

  const handleTest = useCallback(
    async (syntax: string) => {
      setTestResults((prev) => ({
        ...prev,
        [syntax]: { loading: true },
      }));
      try {
        const resp = await proxyApi.testVariable({
          expression: syntax,
          environment_id: environmentId,
        });
        if (resp.code === 200 && resp.data) {
          setTestResults((prev) => ({
            ...prev,
            [syntax]: { result: resp.data!.result, loading: false },
          }));
        } else {
          setTestResults((prev) => ({
            ...prev,
            [syntax]: {
              error: resp.message || '测试失败',
              loading: false,
            },
          }));
        }
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : '测试失败';
        setTestResults((prev) => ({
          ...prev,
          [syntax]: { error: msg, loading: false },
        }));
      }
    },
    [environmentId]
  );

  const renderVariableItem = (item: VariableItemDef) => {
    const testResult = testResults[item.syntax];
    return (
      <div key={item.syntax} className="variable-item">
        <span
          className="variable-syntax"
          onClick={() => handleInsert(item.syntax)}
          title="点击插入"
        >
          {item.syntax}
        </span>
        <span className="variable-description">{item.description}</span>
        <div className="variable-actions">
          <Space size={4}>
            <Button
              type="link"
              size="small"
              icon={<ThunderboltOutlined />}
              onClick={() => handleTest(item.syntax)}
              loading={testResult?.loading}
            >
              测试
            </Button>
            <Button
              type="link"
              size="small"
              icon={<CopyOutlined />}
              onClick={() => handleCopy(item.syntax)}
            >
              复制
            </Button>
          </Space>
        </div>
        {testResult?.result !== undefined && (
          <div className="variable-test-result">结果: {testResult.result}</div>
        )}
        {testResult?.error && (
          <div className="variable-test-error">错误: {testResult.error}</div>
        )}
      </div>
    );
  };

  const envVariables =
    environmentVariables?.map((v) => ({
      syntax: `{{${v.key}}}`,
      description: v.value || '(空值)',
    })) ?? [];

  const tabItems = [
    {
      key: 'builtin',
      label: (
        <span>
          <FunctionOutlined /> 内置函数
        </span>
      ),
      children: (
        <div className="variable-list">
          {builtinFunctions.map(renderVariableItem)}
        </div>
      ),
    },
    {
      key: 'js',
      label: (
        <span>
          <FunctionOutlined /> JS 表达式
        </span>
      ),
      children: (
        <div className="variable-list">
          {jsExpressions.map(renderVariableItem)}
        </div>
      ),
    },
    {
      key: 'env',
      label: (
        <span>
          <EnvironmentOutlined /> 环境变量
        </span>
      ),
      children:
        envVariables.length > 0 ? (
          <div className="variable-list">
            {envVariables.map(renderVariableItem)}
          </div>
        ) : (
          <div style={{ color: 'var(--color-text-tertiary)', textAlign: 'center', padding: 'var(--space-6)' }}>
            暂无环境变量
          </div>
        ),
    },
  ];

  return (
    <Popover
      content={
        <div className="variable-assistant-popover">
          <Tabs
            className="variable-assistant-tabs"
            items={tabItems}
            size="small"
          />
        </div>
      }
      title="变量助手"
      trigger="click"
      open={open}
      onOpenChange={setOpen}
      placement="leftTop"
      overlayStyle={{ maxWidth: 420 }}
    >
      <FloatButton
        className="variable-assistant-float-btn"
        icon={<FunctionOutlined />}
        type="primary"
        style={{ right: 24, bottom: 80 }}
        tooltip="变量助手"
      />
    </Popover>
  );
};

export default VariableAssistant;