import React, { useRef, useState, useEffect, useCallback } from 'react';
import { Input, Select, Spin, Tag, Tooltip, message as antMessage } from 'antd';
import { ThunderboltOutlined, MessageOutlined, BulbOutlined, ApiOutlined, FileTextOutlined } from '@ant-design/icons';
import { Session, Module, ApiProject, ApiEndpoint } from '../types';
import HistoryPromptSidebar from './HistoryPromptSidebar';
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
}) => {
  const textAreaRef = useRef<HTMLTextAreaElement>(null);
  const [apiProjects, setApiProjects] = useState<ApiProject[]>([]);
  const [apiEndpoints, setApiEndpoints] = useState<ApiEndpoint[]>([]);
  const [loadingApis, setLoadingApis] = useState(false);
  const [smartMatching, setSmartMatching] = useState(false);

  const loadApiProjects = useCallback(async () => {
    try {
      const { apiTestApi } = await import('../services/api');
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
      const { apiTestApi } = await import('../services/api');
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

  const handleSmartMatch = async () => {
    if (!requirement.trim()) {
      antMessage.warning('请先输入需求文本');
      return;
    }
    setSmartMatching(true);
    try {
      const { apiTestApi } = await import('../services/api');
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
    return tag + ep.method?.toUpperCase() + ' ' + ep.path;
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
                onClick={onGenerate}
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
