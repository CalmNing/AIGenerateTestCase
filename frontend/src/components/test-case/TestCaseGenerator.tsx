import React, { useRef, useState, useEffect, useCallback } from 'react';
import { Button, Card, Input, Select, Spin, Tag, Tooltip, message as antMessage, Modal } from 'antd';
import { ThunderboltOutlined, MessageOutlined, BulbOutlined, ApiOutlined, FileTextOutlined, EditOutlined, RocketOutlined } from '@ant-design/icons';
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
  onGenerate: () => void;
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
  onGenerate,
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
  const [previewVisible, setPreviewVisible] = useState(false);

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

  const handleGenerateWithPreview = () => {
    if (selectedApiEndpointId && selectedApiEndpointId.length > 0) {
      setPreviewVisible(true);
    } else {
      onGenerate();
    }
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
                className={`tcg-generate-btn ${loading ? 'is-loading' : ''}`}
                onClick={handleGenerateWithPreview}
                disabled={!requirement.trim() || loading}
              >
                {loading ? (
                  <>
                    <Spin size="small" style={{ color: 'var(--color-primary)' }} />
                    AI 正在生成中...
                  </>
                ) : (
                  <>
                    <ThunderboltOutlined />
                    AI 生成测试用例
                  </>
                )}
              </button>
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

        {/* Generation Preview Confirmation Modal */}
        <Modal
          title="确认生成测试用例"
          open={previewVisible}
          onCancel={() => setPreviewVisible(false)}
          width={640}
          footer={[
            <Button key="cancel" onClick={() => setPreviewVisible(false)} disabled={loading}>
              取消
            </Button>,
            <Button
              key="confirm"
              type="primary"
              icon={<RocketOutlined />}
              onClick={() => {
                setPreviewVisible(false);
                onGenerate();
              }}
            >
              确认生成
            </Button>,
          ]}
        >
          <div style={{ marginBottom: 16, fontSize: 13, color: 'var(--color-text-secondary)' }}>
            AI 将基于以下接口信息和您的需求描述，生成包含可执行 API 调用步骤的测试用例。
          </div>
          <Card
            size="small"
            title={
              <span>
                <ApiOutlined style={{ marginRight: 8 }} />
                已选接口（{selectedApiEndpointId?.length || 0} 个）
              </span>
            }
            styles={{ body: { padding: 0 } }}
          >
            {selectedApiEndpointId && selectedApiEndpointId.length > 0 ? (
              selectedApiEndpointId.map((eid, idx) => {
                const ep = apiEndpoints.find(e => e.id === eid);
                if (!ep) return null;
                const methodColor = methodColorMap[ep.method?.toUpperCase()] || 'default';
                const hasOverrides = apiEndpointOverrides?.[eid] && (
                  apiEndpointOverrides[eid]?.body ||
                  (apiEndpointOverrides[eid]?.headers?.length || 0) > 0 ||
                  (apiEndpointOverrides[eid]?.parameters?.length || 0) > 0
                );
                return (
                  <div
                    key={eid}
                    style={{
                      padding: '10px 16px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      borderBottom: idx < (selectedApiEndpointId?.length || 0) - 1
                        ? '1px solid var(--color-border-secondary)'
                        : 'none',
                    }}
                  >
                    <Tag color={methodColor} style={{ margin: 0, minWidth: 52, textAlign: 'center' }}>
                      {ep.method?.toUpperCase()}
                    </Tag>
                    <span style={{ fontWeight: 500, flex: 1 }}>{ep.path}</span>
                    <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>{ep.name}</span>
                    {hasOverrides && <Tag color="warning">已自定义</Tag>}
                  </div>
                );
              })
            ) : (
              <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--color-text-tertiary)' }}>
                未选择接口，将仅基于需求描述生成用例
              </div>
            )}
          </Card>
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
