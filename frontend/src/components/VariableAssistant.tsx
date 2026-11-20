import React, { useState, useCallback } from 'react';
import { FloatButton, Popover, Tabs, Button, message } from 'antd';
import {
  FunctionOutlined,
  CopyOutlined,
  ThunderboltOutlined,
  EnvironmentOutlined,
} from '@ant-design/icons';
import { proxyApi } from '../services/api';
import { copyTextToClipboard } from '../utils/clipboard';
import './VariableAssistant.css';

interface VariableAssistantProps {
  environmentId?: number;
  environmentVariables?: Array<{ key: string; value: string }>;
  onInsert?: (syntax: string) => void;
}

interface VariableItemDef {
  syntax: string;
  description: string;
  /** 可选分组名；未设置时按现有扁平列表渲染 */
  group?: string;
}

const builtinFunctions: VariableItemDef[] = [
  { syntax: '{{$timestamp}}', description: '毫秒级时间戳（13位）' },
  { syntax: '{{$now}}', description: '秒级时间戳（10位）' },
  { syntax: '{{$date}}', description: '当前日期，格式 YYYY-MM-DD' },
  { syntax: "{{$date('YYYY-MM-DD HH:mm:ss')}}", description: '自定义格式日期' },
  { syntax: '{{$randomInt}}', description: '0 ~ 100 的随机整数' },
  { syntax: '{{$randomInt(1,1000)}}', description: '指定范围的随机整数' },
  { syntax: '{{$randomFloat}}', description: '0 ~ 1 的随机浮点数，保留2位小数' },
  { syntax: '{{$randomFloat(1,10)}}', description: '指定范围的随机浮点数' },
  { syntax: '{{$randomFloat(1,10,4)}}', description: '指定范围与小数位数的随机浮点数' },
  { syntax: '{{$uuid}}', description: 'UUID v4' },
];

const jsExpressions: VariableItemDef[] = [
  // 时间
  { syntax: '{{@Date.now()}}', description: 'JS 时间戳（毫秒）', group: '时间' },
  { syntax: '{{@Math.floor(Date.now()/1000)}}', description: '秒级时间戳', group: '时间' },
  { syntax: '{{@new Date().toISOString()}}', description: 'ISO 8601 日期字符串', group: '时间' },
  // 时间偏移
  { syntax: '{{@Date.now() + 3600000}}', description: '1 小时后时间戳（毫秒）', group: '时间偏移' },
  { syntax: '{{@Date.now() - 86400000}}', description: '1 天前时间戳（毫秒）', group: '时间偏移' },
  { syntax: '{{@new Date(Date.now() + 3600000).toISOString()}}', description: '1 小时后 ISO 8601 日期', group: '时间偏移' },
  { syntax: '{{@new Date(Date.now() - 86400000).toISOString()}}', description: '1 天前 ISO 8601 日期', group: '时间偏移' },
  { syntax: "{{@new Date(Date.now() + 3600000).toLocaleString('sv-SE')}}", description: '1 小时后本地时间 YYYY-MM-DD HH:mm:ss', group: '时间偏移' },
  // 随机
  { syntax: '{{@Math.random().toFixed(4)}}', description: '0~1 随机小数，保留4位', group: '随机' },
  { syntax: '{{@Math.floor(Math.random()*1000)}}', description: '0~999 随机整数', group: '随机' },
  { syntax: "{{@'test_' + Math.floor(Math.random()*1000)}}", description: '拼接随机字符串', group: '随机' },
  { syntax: '{{@crypto.randomUUID()}}', description: 'UUID v4', group: '随机' },
  // 列表随机
  { syntax: "{{@['苹果','香蕉','橘子'][Math.floor(Math.random()*3)]}}", description: '从列表随机取一个', group: '列表随机' },
  { syntax: "{{@[{id:1,name:'张三'},{id:2,name:'李四'}][Math.floor(Math.random()*2)].name}}", description: '从对象列表随机取字段', group: '列表随机' },
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
    async (syntax: string) => {
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
        // 无焦点输入框时降级为复制（http 环境无 navigator.clipboard，用 execCommand 兜底）
        const ok = await copyTextToClipboard(syntax);
        if (ok) {
          message.success('已复制到剪贴板');
        } else {
          message.error('复制失败，请手动选择文本复制');
        }
      }
    },
    [onInsert]
  );

  const handleCopy = useCallback(async (syntax: string) => {
    // http 环境 navigator.clipboard 可能为 undefined（同步抛错导致"点击无响应"），统一走兜底
    const ok = await copyTextToClipboard(syntax);
    if (ok) {
      message.success('已复制到剪贴板');
    } else {
      message.error('复制失败，请手动选择文本复制');
    }
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
        <div className="variable-info">
          <span
            className="variable-syntax"
            onClick={() => handleInsert(item.syntax)}
            title="点击插入"
          >
            {item.syntax}
          </span>
          <span className="variable-description" title={item.description}>{item.description}</span>
          {testResult?.result !== undefined && (
            <div className="variable-test-result">结果: {testResult.result}</div>
          )}
          {testResult?.error && (
            <div className="variable-test-error">错误: {testResult.error}</div>
          )}
        </div>
        <div className="variable-actions">
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
        </div>
      </div>
    );
  };

  // 按 group 字段分组渲染：连续同组项归为一组并加组标题；
  // 若全部项都没有 group（内置函数/环境变量页签），退化为现有扁平列表
  const renderGroupedItems = (items: VariableItemDef[]) => {
    if (!items.some((item) => item.group)) {
      return (
        <div className="variable-list">
          {items.map(renderVariableItem)}
        </div>
      );
    }

    const groups: Array<{ name: string; items: VariableItemDef[] }> = [];
    items.forEach((item) => {
      const name = item.group || '';
      const last = groups[groups.length - 1];
      if (last && last.name === name) {
        last.items.push(item);
      } else {
        groups.push({ name, items: [item] });
      }
    });

    return (
      <div className="variable-grouped">
        {groups.map((group, index) => (
          <div key={`group-${index}-${group.name}`}>
            {group.name && <div className="variable-group-title">{group.name}</div>}
            <div className="variable-list">
              {group.items.map(renderVariableItem)}
            </div>
          </div>
        ))}
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
      children: renderGroupedItems(jsExpressions),
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