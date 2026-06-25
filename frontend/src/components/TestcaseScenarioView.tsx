import React, { useState, useEffect, useCallback } from 'react';
import { Button, Space, Collapse, Tag, message, Spin, Empty } from 'antd';
import {
  PlayCircleOutlined,
  EditOutlined,
  EyeOutlined,
} from '@ant-design/icons';
import { apiTestApi } from '../services/api';
import { ApiScenario, ApiScenarioStep } from '../types';
import './TestcaseScenarioView.css';

interface TestcaseScenarioViewProps {
  scenarioId?: number;
  readOnly?: boolean;
}

const methodColors: Record<string, string> = {
  GET: 'green',
  POST: 'blue',
  PUT: 'orange',
  DELETE: 'red',
  PATCH: 'purple',
};

const TestcaseScenarioView: React.FC<TestcaseScenarioViewProps> = ({
  scenarioId,
  readOnly: initialReadOnly = true,
}) => {
  const [scenario, setScenario] = useState<ApiScenario | null>(null);
  const [loading, setLoading] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [latestResult, setLatestResult] = useState<any>(null);

  const [editing, setEditing] = useState(!initialReadOnly);

  // 加载场景数据
  const loadScenario = useCallback(async () => {
    if (!scenarioId) return;
    setLoading(true);
    try {
      const res = await apiTestApi.getScenario(scenarioId);
      if (res.code === 200 && res.data) {
        setScenario(res.data);
      }
    } catch (error) {
      console.error('加载场景失败:', error);
    } finally {
      setLoading(false);
    }
  }, [scenarioId]);

  useEffect(() => {
    loadScenario();
  }, [loadScenario]);

  // 加载最近一次执行结果
  const loadLatestResult = useCallback(async () => {
    if (!scenarioId) return;
    try {
      const res = await apiTestApi.getScenarioResults(scenarioId, 1);
      if (res.code === 200 && res.data && res.data.length > 0) {
        setLatestResult(res.data[0]);
      }
    } catch (error) {
      console.error('加载历史结果失败:', error);
    }
  }, [scenarioId]);

  useEffect(() => {
    loadLatestResult();
  }, [loadLatestResult]);

  // 执行场景
  const handleExecute = async () => {
    if (!scenarioId) return;
    setExecuting(true);
    try {
      const res = await apiTestApi.runScenario(scenarioId);
      if (res.code === 200 && res.data) {
        setResult(res.data);
        setLatestResult(res.data);
        message.success(res.data.passed ? '场景执行通过' : '场景执行失败');
      } else {
        message.error(res.message || '执行失败');
      }
    } catch (error: any) {
      message.error(error.message || '执行失败');
    } finally {
      setExecuting(false);
    }
  };

  // 渲染步骤
  const renderStep = (step: ApiScenarioStep, index: number) => {
    const method = step.method || 'GET';
    const stepName = step.name || step.url || `步骤 ${index + 1}`;

    return (
      <Collapse
        key={index}
        className="scenario-step-item"
        items={[
          {
            key: String(index),
            label: (
              <Space>
                <span className="step-number">{index + 1}</span>
                <Tag color={methodColors[method] || 'default'}>{method}</Tag>
                <span>{stepName}</span>
                {step.enabled === false && <Tag color="red">已禁用</Tag>}
              </Space>
            ),
            children: (
              <div>
                {/* 基本信息 */}
                <div style={{ marginBottom: 8 }}>
                  <strong>URL:</strong> {step.url || '-'}
                </div>

                {/* 请求体 */}
                {step.body && (
                  <div style={{ marginBottom: 8 }}>
                    <strong>请求体:</strong>
                    <pre
                      style={{
                        background: 'var(--color-bg)',
                        padding: 8,
                        borderRadius: 'var(--radius-sm)',
                        fontSize: 12,
                        maxHeight: 200,
                        overflow: 'auto',
                      }}
                    >
                      {step.body}
                    </pre>
                  </div>
                )}

                {/* 后置提取 */}
                {step.post_actions && step.post_actions.length > 0 && (
                  <div style={{ marginBottom: 8 }}>
                    <strong>变量提取:</strong>
                    <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                      {step.post_actions.map((action, i) => (
                        <li key={i}>
                          <code>{action.key}</code> ={' '}
                          <code>{action.jsonpath}</code>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* 断言 */}
                {step.assertions && step.assertions.length > 0 && (
                  <div>
                    <strong>断言:</strong>
                    <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                      {step.assertions.map((assertion, i) => (
                        <li key={i}>
                          {assertion.type}: {assertion.jsonpath || ''}{' '}
                          {assertion.value !== undefined
                            ? `= ${assertion.value}`
                            : ''}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ),
          },
        ]}
      />
    );
  };

  // 渲染执行结果
  const effectiveResult = result || latestResult;
  const effectiveSource = result ? 'current' : (latestResult ? 'history' : null);

  const renderResult = () => {
    if (!effectiveResult) return null;

    return (
      <div className="scenario-result">
        <div style={{ marginBottom: 8 }}>
          {effectiveSource && (
            <Tag color={effectiveSource === 'current' ? 'blue' : 'default'} style={{ marginRight: 8 }}>
              {effectiveSource === 'current' ? '当前执行' : '上次结果'}
            </Tag>
          )}
          <Tag
            color={effectiveResult.passed ? 'success' : 'error'}
            style={{ fontSize: 14 }}
          >
            {effectiveResult.passed ? '执行通过' : '执行失败'}
          </Tag>
        </div>

        {effectiveResult.steps && effectiveResult.steps.length > 0 && (
          <div>
            <strong>步骤详情:</strong>
            {effectiveResult.steps.map((step: any, index: number) => (
              <div
                key={index}
                style={{
                  padding: 8,
                  marginTop: 4,
                  background:
                    step.status === 'passed'
                      ? 'var(--color-success-bg)'
                      : 'var(--color-danger-bg)',
                  borderRadius: 'var(--radius-sm)',
                }}
              >
                <Space>
                  <span>{index + 1}.</span>
                  <span>{step.name || `步骤 ${index + 1}`}</span>
                  <Tag
                    color={step.status === 'passed' ? 'success' : 'error'}
                  >
                    {step.status === 'passed' ? '通过' : '失败'}
                  </Tag>
                  {step.response?.elapsed_ms && (
                    <span style={{ color: 'var(--color-text-tertiary)' }}>
                      {step.response.elapsed_ms}ms
                    </span>
                  )}
                </Space>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  if (loading) {
    return (
      <div className="scenario-empty">
        <Spin />
      </div>
    );
  }

  if (!scenarioId) {
    return (
      <div className="scenario-empty">
        <Empty description="未关联接口场景" />
      </div>
    );
  }

  if (!scenario) {
    return (
      <div className="scenario-empty">
        <Empty description="场景不存在" />
      </div>
    );
  }

  return (
    <div className="testcase-scenario-view">
      {/* 工具栏 */}
      <div className="scenario-toolbar">
        <Space>
          <Button
            icon={editing ? <EyeOutlined /> : <EditOutlined />}
            onClick={() => setEditing(!editing)}
          >
            {editing ? '查看' : '编辑'}
          </Button>
        </Space>
        <Button
          type="primary"
          icon={<PlayCircleOutlined />}
          loading={executing}
          onClick={handleExecute}
        >
          执行
        </Button>
      </div>

      {/* 场景信息 */}
      <div style={{ padding: '8px 0' }}>
        <strong>场景名称:</strong> {scenario.name}
        {scenario.description && (
          <div style={{ color: 'var(--color-text-tertiary)', marginTop: 4 }}>
            {scenario.description}
          </div>
        )}
      </div>

      {/* 步骤列表 */}
      <div className="scenario-steps">
        {scenario.steps && scenario.steps.length > 0 ? (
          scenario.steps.map((step, index) => renderStep(step, index))
        ) : (
          <Empty description="暂无步骤" />
        )}
      </div>

      {/* 执行结果 */}
      {renderResult()}
    </div>
  );
};

export default TestcaseScenarioView;
