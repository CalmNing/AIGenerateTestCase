import React, { useRef, useState, useEffect, useCallback } from 'react';
import { Button, Card, Input, Select, Spin, Tag, Tooltip, message as antMessage, Modal, Progress, Steps } from 'antd';
import {
  ThunderboltOutlined, MessageOutlined, BulbOutlined, ApiOutlined,
  FileTextOutlined, EditOutlined, ExperimentOutlined,
  CheckCircleOutlined, LoadingOutlined, CloseCircleOutlined,
} from '@ant-design/icons';
import { Session, Module, ApiProject, ApiEndpoint } from '../../types';
import HistoryPromptSidebar from '../HistoryPromptSidebar';
import './TestCaseGenerator.css';

interface TestCaseGeneratorProps {
  selectedSession: Session | null;
  modules: Module[];
  selectedModule: number | string;
  requirement: string;
  loading: boolean;
  onRequirementChange: (value: string) => void;
  historyPromptRefreshKey?: number;
  selectedApiEndpointId?: number[];
  onApiEndpointChange?: (ids: number[]) => void;
  selectedApiProjectId?: number | null;
  onApiProjectChange?: (id: number | null) => void;
  apiEndpointOverrides?: Record<number, { body?: string; headers?: any[]; parameters?: any[] }>;
  onApiEndpointOverridesChange?: (overrides: Record<number, { body?: string; headers?: any[]; parameters?: any[] }>) => void;
}

const TestCaseGenerator: React.FC<TestCaseGeneratorProps> = ({
  selectedSession,
  modules,
  selectedModule,
  requirement,
  loading,
  onRequirementChange,
  historyPromptRefreshKey,
  selectedApiEndpointId,
  onApiEndpointChange,
  selectedApiProjectId,
  onApiProjectChange,
  apiEndpointOverrides,
  onApiEndpointOverridesChange,
}) => {
  const textAreaRef = useRef<HTMLTextAreaElement>(null);
  const [apiProjects, setApiProjects] = useState<ApiProject[]>([]);
  const [apiEndpoints, setApiEndpoints] = useState<ApiEndpoint[]>([]);
  const [loadingApis, setLoadingApis] = useState(false);
  const [smartMatching, setSmartMatching] = useState(false);
  const [overrideEditorVisible, setOverrideEditorVisible] = useState(false);
  const [editingOverrides, setEditingOverrides] = useState<Record<number, { body?: string; headers?: any[]; parameters?: any[] }>>({});

  // 4阶段 Pipeline 状态
  const [pipelineTaskId, setPipelineTaskId] = useState<string | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<{
    status: string;
    current_phase: number;
    phase_name: string;
    progress: number;
    message: string;
    result: any;
    error: string | null;
  } | null>(null);
  const [pipelinePolling, setPipelinePolling] = useState(false);

  // 更新指定接口的 override 字段
  const updateOverride = useCallback((eid: number, field: string, value: any) => {
    setEditingOverrides(prev => ({
      ...prev,
      [eid]: { ...(prev[eid] || {}), [field]: value },
    }));
  }, []);

  // 保存 overrides 并关闭弹窗
  const handleSaveOverrides = useCallback(() => {
    if (onApiEndpointOverridesChange) {
      onApiEndpointOverridesChange(editingOverrides);
    }
    setOverrideEditorVisible(false);
    antMessage.success('接口参数已保存');
  }, [editingOverrides, onApiEndpointOverridesChange]);

  const loadApiProjects = useCallback(async () => {
    try {
      const { apiTestApi } = await import('../../services/api');
      const res = await apiTestApi.getProjects();
      if (res.code === 200 && res.data) {
        setApiProjects(res.data);
      }
    } catch (e) {
      console.error('Load API projects failed:', e);
    }
  }, []);

  const loadApiEndpoints = useCallback(async (projectId: number) => {
    setLoadingApis(true);
    try {
      const { apiTestApi } = await import('../../services/api');
      const res = await apiTestApi.getEndpoints(projectId);
      if (res.code === 200 && res.data) {
        setApiEndpoints(res.data);
      }
    } catch (e) {
      console.error('Load API endpoints failed:', e);
    } finally {
      setLoadingApis(false);
    }
  }, []);

  useEffect(() => {
    loadApiProjects();
  }, [loadApiProjects]);

  const handleSelectPrompt = (content: string) => {
    onRequirementChange(content);
  };

  const handlePromptsChange = () => {};

  const handleProjectChange = (val: number | null) => {
    if (onApiProjectChange) onApiProjectChange(val);
    if (onApiEndpointChange) onApiEndpointChange([]);
    setApiEndpoints([]);
    if (val) loadApiEndpoints(val);
  };

  const handleEndpointChange = (vals: number[]) => {
    if (onApiEndpointChange) onApiEndpointChange(vals);
  };

  // 全选 / 取消全选 当前项目下的所有接口
  const allSelected = apiEndpoints.length > 0 &&
    apiEndpoints.every(ep => (selectedApiEndpointId || []).includes(ep.id));

  const handleToggleSelectAll = () => {
    if (!onApiEndpointChange) return;
    if (allSelected) {
      onApiEndpointChange([]);
    } else {
      onApiEndpointChange(apiEndpoints.map(ep => ep.id));
    }
  };

  // Pipeline 进度轮询
  useEffect(() => {
    if (!pipelineTaskId || !pipelinePolling) return;

    const pollInterval = setInterval(async () => {
      try {
        const { testcaseApi } = await import('../../services/api');
        const res = await testcaseApi.getPipelineStatus(pipelineTaskId);
        if (res.code === 200 && res.data) {
          setPipelineStatus(res.data);

          if (res.data.status === 'completed' || res.data.status === 'failed') {
            setPipelinePolling(false);
            clearInterval(pollInterval);

            if (res.data.status === 'completed') {
              antMessage.success('深度生成完成！');
              // 通过自定义事件通知父组件刷新测试用例列表
              window.dispatchEvent(new CustomEvent('pipeline-completed', {
                detail: { sessionId: selectedSession?.id, result: res.data.result }
              }));
            } else if (res.data.status === 'failed') {
              antMessage.error(`深度生成失败：${res.data.error || res.data.message}`);
            }
          }
        }
      } catch (e) {
        console.error('Poll pipeline status failed:', e);
      }
    }, 2000);

    return () => clearInterval(pollInterval);
  }, [pipelineTaskId, pipelinePolling, selectedSession?.id]);

  const handleDeepGenerate = async () => {
    if (!selectedSession || !requirement.trim()) return;

    setPipelinePolling(true);
    setPipelineStatus({
      status: 'running',
      current_phase: 0,
      phase_name: '初始化',
      progress: 0,
      message: '正在启动深度生成...',
      result: null,
      error: null,
    });

    try {
      const { testcaseApi } = await import('../../services/api');
      const res = await testcaseApi.generateV2(selectedSession.id, requirement.trim(), {
        apiEndpointIds: selectedApiEndpointId,
        apiProjectId: selectedApiProjectId,
        apiEndpointOverrides: apiEndpointOverrides,
        moduleId: selectedModule,
      });
      if (res.code === 200 && res.data?.task_id) {
        setPipelineTaskId(res.data.task_id);
      } else {
        throw new Error(res.message || '启动失败');
      }
    } catch (e: any) {
      console.error('Deep generate failed:', e);
      antMessage.error('深度生成启动失败：' + (e?.message || '未知错误'));
      setPipelinePolling(false);
      setPipelineStatus(null);
    }
  };

  const handleGenerateWithPreview = () => {
    handleDeepGenerate();
  };

  const handleSmartMatch = async () => {
    if (!requirement.trim()) {
      antMessage.warning('请先输入需求文本');
      return;
    }
    setSmartMatching(true);
    try {
      const { apiTestApi } = await import('../../services/api');
      const res = await apiTestApi.matchEndpoint({
        requirement: requirement.trim(),
        project_id: selectedApiProjectId || undefined,
      });
      if (res.code === 200 && res.data?.matches) {
        const matchedIds = res.data.matches.map((m: any) => m.endpoint_id);
        if (matchedIds.length === 0) {
          antMessage.info('未找到匹配的API接口');
          return;
        }
        if (onApiEndpointChange) onApiEndpointChange(matchedIds);
        if (!selectedApiProjectId && res.data.matches.length > 0) {
          const projId = res.data.matches[0].project_id;
          if (onApiProjectChange) onApiProjectChange(projId);
          if (!apiEndpoints.length) loadApiEndpoints(projId);
        }
        antMessage.success('智能匹配到 ' + matchedIds.length + ' 个API接口');
      } else {
        antMessage.info('未找到匹配的API接口');
      }
    } catch (e: any) {
      console.error('Smart match failed:', e);
      antMessage.error('智能匹配失败: ' + (e?.response?.data?.message || e.message));
    } finally {
      setSmartMatching(false);
    }
  };

  const endpointLabel = (ep: ApiEndpoint) => {
    const tag = ep.tags && ep.tags.length > 0 ? ep.tags[0] + ' ' : '';
    const namePart = ep.name ? ` - ${ep.name}` : '';
    return tag + ep.method?.toUpperCase() + ' ' + ep.path + namePart;
  };

  const endpointSearchText = (ep: ApiEndpoint) => {
    return [ep.name, ep.path, ep.method, ...(ep.tags || [])]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
  };

  const moduleName = selectedModule
    ? modules.find(m => m.id === selectedModule)?.module_name || '无'
    : '无';

  const methodColorMap: Record<string, string> = {
    GET: 'green', POST: 'blue', PUT: 'orange', DELETE: 'red', PATCH: 'purple',
  };

  return (
    <div className="tcg-container">
      <div className="tcg-main">
        {!selectedSession ? (
          <div className="tcg-card">
            <div className="tcg-empty">
              <MessageOutlined className="tcg-empty-icon" />
              <span className="tcg-empty-text">请先选择或创建会话</span>
            </div>
          </div>
        ) : (
          <div className="tcg-card">
            <div className="tcg-card-header">
              <BulbOutlined style={{ color: 'var(--color-primary)' }} />
              生成测试用例
              <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--color-text-tertiary)', fontWeight: 400 }}>
                4阶段深度生成：需求拆分→测试点→用例设计→优化
              </span>
            </div>
            <div className="tcg-card-body" style={{ display: 'flex', flexDirection: 'column' }}>
              {/* Context Info */}
              <div className="tcg-context-bar">
                <div className="tcg-context-item">
                  <span className="tcg-context-label">会话</span>
                  <span className="tcg-context-value">{selectedSession.name}</span>
                </div>
                <div className="tcg-context-divider" />
                <div className="tcg-context-item">
                  <span className="tcg-context-label">模块</span>
                  <span className="tcg-context-value">{moduleName}</span>
                </div>
              </div>

              {/* API Section */}
              <div className="tcg-api-section">
                <div className="tcg-api-header">
                  <span className="tcg-api-label">
                    <span className="tcg-api-label-icon">
                      <ApiOutlined />
                    </span>
                    关联 API 接口
                  </span>
                  <div className="tcg-api-actions">
                    <button
                      className="tcg-smart-btn"
                      onClick={handleToggleSelectAll}
                      disabled={!selectedApiProjectId || apiEndpoints.length === 0 || loadingApis}
                    >
                      {allSelected ? '取消全选' : '全选'}
                      {apiEndpoints.length > 0 && (
                        <span className="tcg-select-all-count">
                          ({selectedApiEndpointId?.length || 0}/{apiEndpoints.length})
                        </span>
                      )}
                    </button>
                    <Tooltip title="根据需求文本自动匹配API接口">
                      <button
                        className={`tcg-smart-btn ${smartMatching ? 'is-loading' : ''}`}
                        onClick={handleSmartMatch}
                        disabled={!requirement.trim() || smartMatching}
                      >
                        <ThunderboltOutlined style={{ fontSize: 12 }} />
                        {smartMatching ? '匹配中...' : '智能匹配'}
                      </button>
                    </Tooltip>
                  </div>
                </div>
                <div className="tcg-api-selects">
                  <Select
                    style={{ flex: 1 }}
                    placeholder="选择 API 项目"
                    allowClear
                    value={selectedApiProjectId}
                    onChange={handleProjectChange}
                    options={apiProjects.map(p => ({ label: p.name, value: p.id }))}
                    size="middle"
                  />
                  <Select
                    mode="multiple"
                    style={{ flex: 2 }}
                    placeholder="选择接口（可多选）"
                    value={selectedApiEndpointId || []}
                    onChange={handleEndpointChange}
                    showSearch
                    autoClearSearchValue={false}
                    filterOption={(inputValue, option) => {
                      const ep = apiEndpoints.find(e => e.id === option?.value);
                      if (!ep) return false;
                      return endpointSearchText(ep).includes(inputValue.toLowerCase());
                    }}
                    options={apiEndpoints.map(e => ({
                      label: endpointLabel(e),
                      value: e.id,
                    }))}
                    notFoundContent={loadingApis ? <Spin size="small" /> : '暂无API接口'}
                    loading={loadingApis}
                    maxTagCount={3}
                    maxTagTextLength={20}
                    tagRender={(props) => {
                      const ep = apiEndpoints.find(e => e.id === props.value);
                      const method = ep?.method?.toUpperCase() || '';
                      const color = methodColorMap[method] || 'default';
                      return (
                        <Tag color={color} closable={props.closable} onClose={props.onClose} style={{ margin: 2, fontSize: 12 }}>
                          {method} {ep?.path || props.value}
                        </Tag>
                      );
                    }}
                  />
                </div>
                <div className="tcg-api-hint">
                  AI 会将接口 Schema 注入提示词，生成可执行的 api_call 测试步骤
                </div>
                {selectedApiEndpointId && selectedApiEndpointId.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <button
                      className="tcg-smart-btn"
                      onClick={() => {
                        // Initialize editing overrides with current values or DB defaults
                        const initial: Record<number, any> = {};
                        for (const eid of selectedApiEndpointId) {
                          const ep = apiEndpoints.find(e => e.id === eid);
                          const saved = apiEndpointOverrides?.[eid];
                          initial[eid] = {
                            body: saved?.body ?? ep?.body ?? '',
                            headers: saved?.headers ?? ep?.headers ?? [],
                            parameters: saved?.parameters ?? ep?.parameters ?? [],
                          };
                        }
                        setEditingOverrides(initial);
                        setOverrideEditorVisible(true);
                      }}
                    >
                      <EditOutlined style={{ fontSize: 12 }} />
                      编辑接口参数
                    </button>
                    {apiEndpointOverrides && Object.keys(apiEndpointOverrides).length > 0 && (
                      <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--color-text-tertiary)' }}>
                        已自定义 {Object.keys(apiEndpointOverrides).length} 个接口
                      </span>
                    )}
                  </div>
                )}
              </div>

              {/* Requirement Input */}
              <div className="tcg-requirement-section">
                <div className="tcg-requirement-label">
                  <FileTextOutlined style={{ color: 'var(--color-text-tertiary)' }} />
                  需求描述
                </div>
                <div className="tcg-textarea-wrapper">
                  <Input.TextArea
                    ref={textAreaRef}
                    placeholder="请输入需求描述... 已关联的 API 接口将作为参考，生成包含可执行 api_call 步骤的测试用例。"
                    value={requirement}
                    onChange={(e) => onRequirementChange(e.target.value)}
                    className="tcg-textarea"
                  />
                </div>
              </div>

              {/* Generate Button */}
              <button
                className={`tcg-generate-btn ${loading || pipelinePolling ? 'is-loading' : ''}`}
                onClick={handleGenerateWithPreview}
                disabled={!requirement.trim() || loading || pipelinePolling}
              >
                {loading || pipelinePolling ? (
                  <>
                    <Spin size="small" style={{ color: 'var(--color-primary)' }} />
                    {pipelinePolling ? `深度生成中... ${pipelineStatus?.progress || 0}%` : 'AI 正在生成中...'}
                  </>
                ) : (
                  <>
                    <ExperimentOutlined />
                    深度生成（4阶段）
                  </>
                )}
              </button>

              {/* Pipeline 进度面板 */}
              {pipelinePolling && pipelineStatus && (
                <div className="tcg-pipeline-panel" style={{
                  marginTop: 12,
                  padding: '12px 16px',
                  background: 'var(--color-bg-elevated)',
                  borderRadius: 8,
                  border: '1px solid var(--color-border-secondary)',
                }}>
                  <Steps
                    current={pipelineStatus.current_phase - 1}
                    size="small"
                    direction="vertical"
                    style={{ marginBottom: 8 }}
                    items={[
                      {
                        title: '需求拆分',
                        icon: pipelineStatus.current_phase === 1
                          ? <LoadingOutlined />
                          : pipelineStatus.current_phase > 1
                            ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
                            : undefined,
                        description: pipelineStatus.current_phase === 1 ? pipelineStatus.message : undefined,
                      },
                      {
                        title: '测试点生成',
                        icon: pipelineStatus.current_phase === 2
                          ? <LoadingOutlined />
                          : pipelineStatus.current_phase > 2
                            ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
                            : undefined,
                        description: pipelineStatus.current_phase === 2 ? pipelineStatus.message : undefined,
                      },
                      {
                        title: '用例设计',
                        icon: pipelineStatus.current_phase === 3
                          ? <LoadingOutlined />
                          : pipelineStatus.current_phase > 3
                            ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
                            : undefined,
                        description: pipelineStatus.current_phase === 3 ? pipelineStatus.message : undefined,
                      },
                      {
                        title: '用例优化',
                        icon: pipelineStatus.current_phase === 4
                          ? <LoadingOutlined />
                          : pipelineStatus.current_phase > 4
                            ? <CheckCircleOutlined style={{ color: '#52c41a' }} />
                            : undefined,
                        description: pipelineStatus.current_phase === 4 ? pipelineStatus.message : undefined,
                      },
                    ]}
                  />
                  <Progress
                    percent={pipelineStatus.progress}
                    size="small"
                    status={pipelineStatus.status === 'failed' ? 'exception' : 'active'}
                    strokeColor={pipelineStatus.status === 'failed' ? '#ff4d4f' : undefined}
                  />
                  {pipelineStatus.status === 'failed' && pipelineStatus.error && (
                    <div style={{ marginTop: 8, color: '#ff4d4f', fontSize: 12 }}>
                      <CloseCircleOutlined /> {pipelineStatus.error}
                    </div>
                  )}
                  {pipelineStatus.status === 'completed' && pipelineStatus.result && (
                    <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-tertiary)' }}>
                      <CheckCircleOutlined style={{ color: '#52c41a', marginRight: 4 }} />
                      生成完成：{pipelineStatus.result.requirement_points_count} 个需求点，
                      {pipelineStatus.result.test_points_count} 个测试点，
                      {pipelineStatus.result.test_cases_count} 个测试用例
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* API Endpoint Override Editor Modal */}
        <Modal
          title="编辑接口参数"
          open={overrideEditorVisible}
          onCancel={() => setOverrideEditorVisible(false)}
          width={800}
          footer={[
            <Button key="cancel" onClick={() => setOverrideEditorVisible(false)}>
              取消
            </Button>,
            <Button key="save" type="primary" onClick={handleSaveOverrides}>
              保存
            </Button>,
          ]}
        >
          {selectedApiEndpointId?.map((eid) => {
            const ep = apiEndpoints.find(e => e.id === eid);
            if (!ep) return null;
            const override = editingOverrides[eid] || {};
            const methodColor = methodColorMap[ep.method?.toUpperCase()] || 'default';
            return (
              <div key={eid} style={{ marginBottom: 24, borderBottom: '1px solid var(--color-border-secondary)', paddingBottom: 16 }}>
                <div style={{ fontWeight: 600, marginBottom: 12 }}>
                  <Tag color={methodColor}>{ep.method?.toUpperCase()}</Tag>
                  <span>{ep.path}</span>
                  <span style={{ marginLeft: 8, fontWeight: 400, color: 'var(--color-text-tertiary)', fontSize: 13 }}>{ep.name}</span>
                </div>

                {/* Headers */}
                <div style={{ marginBottom: 12 }}>
                  <div className="tcg-override-section-label">请求头 (Headers)</div>
                  {(override.headers || []).length > 0 ? (
                    (override.headers || []).map((h: any, hi: number) => (
                      <div key={hi} style={{ display: 'flex', gap: 8, marginBottom: 4 }}>
                        <Input
                          size="small"
                          style={{ width: 120 }}
                          placeholder="Key"
                          value={h.key}
                          onChange={(e) => {
                            const headers = [...(override.headers || [])];
                            headers[hi] = { ...headers[hi], key: e.target.value };
                            updateOverride(eid, 'headers', headers);
                          }}
                        />
                        <Input
                          size="small"
                          style={{ flex: 1 }}
                          placeholder="Value"
                          value={h.value}
                          onChange={(e) => {
                            const headers = [...(override.headers || [])];
                            headers[hi] = { ...headers[hi], value: e.target.value };
                            updateOverride(eid, 'headers', headers);
                          }}
                        />
                        <Button
                          size="small"
                          danger
                          type="text"
                          onClick={() => {
                            const headers = (override.headers || []).filter((_: any, i: number) => i !== hi);
                            updateOverride(eid, 'headers', headers);
                          }}
                        >
                          删除
                        </Button>
                      </div>
                    ))
                  ) : (
                    <div style={{ fontSize: 12, color: 'var(--color-text-disabled)', marginBottom: 4 }}>暂无请求头</div>
                  )}
                  <Button
                    size="small"
                    type="dashed"
                    onClick={() => {
                      const headers = [...(override.headers || []), { key: '', value: '' }];
                      updateOverride(eid, 'headers', headers);
                    }}
                    style={{ marginTop: 4 }}
                  >
                    + 添加请求头
                  </Button>
                </div>

                {/* Parameters */}
                <div style={{ marginBottom: 12 }}>
                  <div className="tcg-override-section-label">请求参数 (Parameters)</div>
                  {(override.parameters || []).length > 0 ? (
                    (override.parameters || []).map((p: any, pi: number) => (
                      <div key={pi} style={{ display: 'flex', gap: 8, marginBottom: 4 }}>
                        <Input
                          size="small"
                          style={{ width: 120 }}
                          placeholder="Key"
                          value={p.key}
                          onChange={(e) => {
                            const params = [...(override.parameters || [])];
                            params[pi] = { ...params[pi], key: e.target.value };
                            updateOverride(eid, 'parameters', params);
                          }}
                        />
                        <Input
                          size="small"
                          style={{ flex: 1 }}
                          placeholder="Value"
                          value={p.value}
                          onChange={(e) => {
                            const params = [...(override.parameters || [])];
                            params[pi] = { ...params[pi], value: e.target.value };
                            updateOverride(eid, 'parameters', params);
                          }}
                        />
                        <Button
                          size="small"
                          danger
                          type="text"
                          onClick={() => {
                            const params = (override.parameters || []).filter((_: any, i: number) => i !== pi);
                            updateOverride(eid, 'parameters', params);
                          }}
                        >
                          删除
                        </Button>
                      </div>
                    ))
                  ) : (
                    <div style={{ fontSize: 12, color: 'var(--color-text-disabled)', marginBottom: 4 }}>暂无请求参数</div>
                  )}
                  <Button
                    size="small"
                    type="dashed"
                    onClick={() => {
                      const params = [...(override.parameters || []), { key: '', value: '' }];
                      updateOverride(eid, 'parameters', params);
                    }}
                    style={{ marginTop: 4 }}
                  >
                    + 添加参数
                  </Button>
                </div>

                {/* Body */}
                <div>
                  <div className="tcg-override-section-label">请求体 (Body)</div>
                  <Input.TextArea
                    rows={4}
                    value={override.body || ''}
                    onChange={(e) => updateOverride(eid, 'body', e.target.value)}
                    placeholder='JSON 格式，如 {"key": "value"}'
                    style={{ fontFamily: 'monospace', fontSize: 13 }}
                  />
                </div>
              </div>
            );
          })}
        </Modal>
      </div>

      <HistoryPromptSidebar
        moduleId={selectedModule}
        onSelectPrompt={handleSelectPrompt}
        onPromptsChange={handlePromptsChange}
        refreshKey={historyPromptRefreshKey}
      />
    </div>
  );
};

export default TestCaseGenerator;
