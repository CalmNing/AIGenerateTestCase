import React, { useState, useEffect, useCallback } from 'react';
import { Button, Space, Collapse, Tag, message, Spin, Empty, Input, Switch, Select } from 'antd';
import {
  PlayCircleOutlined,
  EditOutlined,
  EyeOutlined,
  DeleteOutlined,
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

// Key-Value 行列表编辑器
const KeyValueEditor: React.FC<{
  value: Record<string, string> | Array<{key: string; value: string}>;
  onChange: (val: any) => void;
  keyLabel?: string;
  valueLabel?: string;
}> = ({ value, onChange, keyLabel = 'Key', valueLabel = 'Value' }) => {
  const entries = Array.isArray(value)
    ? value
    : Object.entries(value || {}).map(([k, v]) => ({ key: k, value: String(v) }));

  const updateEntry = (index: number, field: string, val: string) => {
    const newEntries = [...entries];
    newEntries[index] = { ...newEntries[index], [field]: val };
    onChange(newEntries);
  };

  const addEntry = () => {
    onChange([...entries, { key: '', value: '' }]);
  };

  const removeEntry = (index: number) => {
    onChange(entries.filter((_: any, i: number) => i !== index));
  };

  return (
    <div className="kv-editor">
      {entries.map((entry: any, i: number) => (
        <Space key={i} style={{ marginBottom: 4 }}>
          <Input
            size="small"
            placeholder={keyLabel}
            value={entry.key || ''}
            onChange={e => updateEntry(i, 'key', e.target.value)}
            style={{ width: 120 }}
          />
          <Input
            size="small"
            placeholder={valueLabel}
            value={entry.value || ''}
            onChange={e => updateEntry(i, 'value', e.target.value)}
            style={{ width: 200 }}
          />
          <Button size="small" danger type="text" icon={<DeleteOutlined />} onClick={() => removeEntry(i)} />
        </Space>
      ))}
      <Button size="small" type="dashed" onClick={addEntry} style={{ marginTop: 4 }}>
        + 添加
      </Button>
    </div>
  );
};

// PostActions 编辑器
const PostActionsEditor: React.FC<{
  value: any[];
  onChange: (val: any[]) => void;
}> = ({ value, onChange }) => {
  const update = (index: number, field: string, val: string) => {
    const newActions = [...value];
    newActions[index] = { ...newActions[index], [field]: val };
    onChange(newActions);
  };

  return (
    <div className="post-actions-editor">
      {(value || []).map((action: any, i: number) => (
        <Space key={i} style={{ marginBottom: 4 }}>
          <Input
            size="small"
            placeholder="变量名"
            value={action.key || ''}
            onChange={e => update(i, 'key', e.target.value)}
            style={{ width: 120 }}
          />
          <Input
            size="small"
            placeholder="$.jsonpath"
            value={action.jsonpath || ''}
            onChange={e => update(i, 'jsonpath', e.target.value)}
            style={{ width: 200 }}
          />
          <Button size="small" danger type="text" icon={<DeleteOutlined />} onClick={() => onChange(value.filter((_: any, idx: number) => idx !== i))} />
        </Space>
      ))}
      <Button size="small" type="dashed" onClick={() => onChange([...(value || []), { type: 'extract_jsonpath', key: '', jsonpath: '' }])} style={{ marginTop: 4 }}>
        + 添加提取
      </Button>
    </div>
  );
};

// Assertions 编辑器
const AssertionsEditor: React.FC<{
  value: any[];
  onChange: (val: any[]) => void;
}> = ({ value, onChange }) => {
  const update = (index: number, field: string, val: string) => {
    const newAssertions = [...value];
    newAssertions[index] = { ...newAssertions[index], [field]: val };
    onChange(newAssertions);
  };

  const assertionTypes = [
    { label: '状态码', value: 'status_code' },
    { label: '状态码范围', value: 'status_code_range' },
    { label: '响应时间<', value: 'response_time_lt' },
    { label: 'JSONPath 存在', value: 'jsonpath_exists' },
    { label: 'JSONPath 等于', value: 'jsonpath_equals' },
  ];

  return (
    <div className="assertions-editor">
      {(value || []).map((assertion: any, i: number) => (
        <Space key={i} style={{ marginBottom: 4 }}>
          <Select
            size="small"
            value={assertion.type || 'status_code'}
            onChange={val => update(i, 'type', val)}
            options={assertionTypes}
            style={{ width: 130 }}
          />
          <Input
            size="small"
            placeholder="jsonpath"
            value={assertion.jsonpath || ''}
            onChange={e => update(i, 'jsonpath', e.target.value)}
            style={{ width: 150 }}
          />
          <Input
            size="small"
            placeholder="期望值"
            value={assertion.value !== undefined ? String(assertion.value) : ''}
            onChange={e => update(i, 'value', e.target.value)}
            style={{ width: 100 }}
          />
          <Button size="small" danger type="text" icon={<DeleteOutlined />} onClick={() => onChange(value.filter((_: any, idx: number) => idx !== i))} />
        </Space>
      ))}
      <Button size="small" type="dashed" onClick={() => onChange([...(value || []), { type: 'status_code', jsonpath: '', value: '' }])} style={{ marginTop: 4 }}>
        + 添加断言
      </Button>
    </div>
  );
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
  const [editScenario, setEditScenario] = useState<ApiScenario | null>(null);
  const [saving, setSaving] = useState(false);
  const [endpoints, setEndpoints] = useState<any[]>([]);

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

  // 加载接口列表
  const loadEndpoints = useCallback(async () => {
    if (!scenario?.project_id) return;
    try {
      const res = await apiTestApi.getEndpoints(scenario.project_id);
      if (res.code === 200 && res.data) {
        setEndpoints(res.data);
      }
    } catch (error) {
      console.error('加载接口列表失败:', error);
    }
  }, [scenario?.project_id]);

  // 编辑模式切换时复制场景数据
  useEffect(() => {
    if (editing && scenario) {
      setEditScenario(JSON.parse(JSON.stringify(scenario)));
      loadEndpoints();
    }
  }, [editing, scenario, loadEndpoints]);

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

  // 保存场景
  const handleSave = async () => {
    if (!editScenario || !scenarioId) return;
    setSaving(true);
    try {
      const res = await apiTestApi.updateScenario(scenarioId, editScenario);
      if (res.code === 200 && res.data) {
        setScenario(res.data);
        setEditScenario(null);
        setEditing(false);
        message.success('场景保存成功');
      } else {
        message.error(res.message || '保存失败');
      }
    } catch (error: any) {
      message.error(error.message || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  // 步骤编辑辅助函数
  const updateStep = (index: number, field: string, value: any) => {
    if (!editScenario) return;
    const newSteps = [...editScenario.steps];
    newSteps[index] = { ...newSteps[index], [field]: value };
    setEditScenario({ ...editScenario, steps: newSteps });
  };

  const addStep = (endpointId?: number) => {
    if (!editScenario) return;
    const ep = endpoints.find(e => e.id === endpointId);
    const newStep: any = {
      endpoint_id: endpointId || null,
      name: ep?.name || '新步骤',
      enabled: true,
      continue_on_failure: false,
      method: ep?.method || 'GET',
      path: ep?.path || '',
      url: ep?.url || '',
      headers: ep?.headers || [],
      parameters: ep?.parameters || [],
      body: ep?.body || '',
      pre_actions: ep?.pre_actions || [],
      post_actions: ep?.post_actions || [],
      assertions: ep?.assertions || [],
    };
    setEditScenario({
      ...editScenario,
      steps: [...editScenario.steps, newStep],
    });
  };

  const removeStep = (index: number) => {
    if (!editScenario) return;
    const newSteps = editScenario.steps.filter((_: any, i: number) => i !== index);
    setEditScenario({ ...editScenario, steps: newSteps });
  };

  const moveStep = (index: number, direction: 'up' | 'down') => {
    if (!editScenario) return;
    const newSteps = [...editScenario.steps];
    const targetIndex = direction === 'up' ? index - 1 : index + 1;
    if (targetIndex < 0 || targetIndex >= newSteps.length) return;
    [newSteps[index], newSteps[targetIndex]] = [newSteps[targetIndex], newSteps[index]];
    setEditScenario({ ...editScenario, steps: newSteps });
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

  // 渲染可编辑步骤
  const renderEditStep = (step: any, index: number) => {
    const method = step.method || 'GET';
    const stepName = step.name || `步骤 ${index + 1}`;
    const totalSteps = editScenario?.steps.length || 0;

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
              <div className="step-edit-form">
                {/* 基本信息 */}
                <div className="step-edit-row">
                  <label>步骤名称:</label>
                  <Input
                    size="small"
                    value={step.name || ''}
                    onChange={e => updateStep(index, 'name', e.target.value)}
                    style={{ width: 300 }}
                  />
                </div>

                <div className="step-edit-row">
                  <Space>
                    <span>启用:</span>
                    <Switch
                      size="small"
                      checked={step.enabled !== false}
                      onChange={checked => updateStep(index, 'enabled', checked)}
                    />
                    <span style={{ marginLeft: 16 }}>失败继续:</span>
                    <Switch
                      size="small"
                      checked={step.continue_on_failure === true}
                      onChange={checked => updateStep(index, 'continue_on_failure', checked)}
                    />
                  </Space>
                </div>

                {/* URL */}
                <div className="step-edit-row">
                  <label>URL:</label>
                  <Input
                    size="small"
                    value={step.url || ''}
                    onChange={e => updateStep(index, 'url', e.target.value)}
                    style={{ flex: 1 }}
                  />
                </div>

                {/* 请求头 */}
                <div className="step-edit-row">
                  <label>请求头:</label>
                  <KeyValueEditor
                    value={step.headers || []}
                    onChange={val => updateStep(index, 'headers', val)}
                  />
                </div>

                {/* 请求参数 */}
                <div className="step-edit-row">
                  <label>请求参数:</label>
                  <KeyValueEditor
                    value={step.parameters || []}
                    onChange={val => updateStep(index, 'parameters', val)}
                  />
                </div>

                {/* 请求体 */}
                <div className="step-edit-row">
                  <label>请求体:</label>
                  <Input.TextArea
                    value={step.body || ''}
                    onChange={e => updateStep(index, 'body', e.target.value)}
                    rows={4}
                    style={{ fontFamily: 'monospace', fontSize: 12 }}
                  />
                </div>

                {/* 变量提取 */}
                <div className="step-edit-row">
                  <label>变量提取:</label>
                  <PostActionsEditor
                    value={step.post_actions || []}
                    onChange={val => updateStep(index, 'post_actions', val)}
                  />
                </div>

                {/* 断言 */}
                <div className="step-edit-row">
                  <label>断言:</label>
                  <AssertionsEditor
                    value={step.assertions || []}
                    onChange={val => updateStep(index, 'assertions', val)}
                  />
                </div>

                {/* 排序和删除 */}
                <div className="step-edit-actions">
                  <Space>
                    <Button
                      size="small"
                      disabled={index === 0}
                      onClick={() => moveStep(index, 'up')}
                    >
                      ↑ 上移
                    </Button>
                    <Button
                      size="small"
                      disabled={index === totalSteps - 1}
                      onClick={() => moveStep(index, 'down')}
                    >
                      ↓ 下移
                    </Button>
                    <Button
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={() => removeStep(index)}
                    >
                      删除
                    </Button>
                  </Space>
                </div>
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
          {editing && (
            <>
              <Button
                type="primary"
                loading={saving}
                onClick={handleSave}
              >
                保存
              </Button>
              <Select
                placeholder="添加步骤"
                style={{ width: 200 }}
                size="small"
                showSearch
                optionFilterProp="label"
                value={undefined}
                onChange={(endpointId) => {
                  addStep(endpointId);
                }}
                options={endpoints.map(ep => ({
                  label: `${ep.method} ${ep.name || ep.path}`,
                  value: ep.id,
                }))}
              />
            </>
          )}
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
        {editing ? (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Input
              value={editScenario?.name || ''}
              onChange={e => editScenario && setEditScenario({ ...editScenario, name: e.target.value })}
              placeholder="场景名称"
              style={{ fontWeight: 'bold' }}
            />
            <Input.TextArea
              value={editScenario?.description || ''}
              onChange={e => editScenario && setEditScenario({ ...editScenario, description: e.target.value })}
              placeholder="场景描述"
              rows={2}
            />
          </Space>
        ) : (
          <>
            <strong>场景名称:</strong> {scenario.name}
            {scenario.description && (
              <div style={{ color: 'var(--color-text-tertiary)', marginTop: 4 }}>
                {scenario.description}
              </div>
            )}
          </>
        )}
      </div>

      {/* 步骤列表 */}
      <div className="scenario-steps">
        {(() => {
          const currentSteps = editing ? editScenario?.steps : scenario.steps;
          return currentSteps && currentSteps.length > 0 ? (
            currentSteps.map((step: any, index: number) =>
              editing ? renderEditStep(step, index) : renderStep(step, index)
            )
          ) : (
            <Empty description="暂无步骤" />
          );
        })()}
      </div>

      {/* 执行结果 */}
      {renderResult()}
    </div>
  );
};

export default TestcaseScenarioView;
