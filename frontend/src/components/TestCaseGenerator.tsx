import React, { useRef, useState, useEffect, useCallback } from 'react';
import { Card, Typography, Input, Button, Select, Space, Spin, Tag, Tooltip, Empty, message as antMessage } from 'antd';
import { ThunderboltOutlined, MessageOutlined } from '@ant-design/icons';
import { Session, Module, ApiProject, ApiEndpoint } from '../types';
import HistoryPromptSidebar from './HistoryPromptSidebar';

const { Text } = Typography;

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

  return (
    <div style={{ display: 'flex', height: '100%' }}>
      <div style={{ flex: 1, overflow: 'auto' }}>
        <Card title='生成测试用例' variant='borderless'>
          {!selectedSession ? (
            <Empty
              image={<MessageOutlined style={{ fontSize: 48, color: 'var(--color-text-disabled)' }} />}
              description="请先选择或创建会话"
              style={{ padding: '60px 0' }}
            />
          ) : (
            <div>
              <div style={{ marginBottom: '16px' }}>
                <Text strong>会话:</Text> <Text>{selectedSession.name}</Text>
              </div>
              {!selectedModule ? (
                <div style={{ marginBottom: '16px' }}>
                  <Text strong>模块:</Text> <Text>无</Text>
                </div>
              ) : (
                <div style={{ marginBottom: '16px' }}>
                  <Text strong>模块:</Text> <Text>{modules.find(m => m.id === selectedModule)?.module_name || '无'}</Text>
                </div>
              )}

              <div style={{ marginBottom: 16, padding: 12, border: '1px solid #f0f0f0', borderRadius: 8, background: '#fafafa'}}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <Text strong>关联API接口:</Text>
                  <Tooltip title='根据需求文本自动匹配API接口'>
                    <Button
                      size='small'
                      icon={<ThunderboltOutlined />}
                      onClick={handleSmartMatch}
                      loading={smartMatching}
                      disabled={!requirement.trim()}
                    >
                      {' '}智能匹配
                    </Button>
                  </Tooltip>
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <Select
                    style={{ flex: 1 }}
                    placeholder='选择API项目'
                    allowClear
                    value={selectedApiProjectId}
                    onChange={handleProjectChange}
                    options={apiProjects.map(p => ({ label: p.name, value: p.id }))}
                  />
                  <Select
                    mode='multiple'
                    style={{ flex: 2 }}
                    placeholder='选择接口（可多选）'
                    value={selectedApiEndpointId || []}
                    onChange={handleEndpointChange}
                    options={apiEndpoints.map(e => ({
                      label: endpointLabel(e),
                      value: e.id,
                    }))}
                    notFoundContent={loadingApis ? <Spin size='small' /> : '暂无API接口'}
                    loading={loadingApis}
                    maxTagCount={3}
                    maxTagTextLength={20}
                    tagRender={(props) => {
                      const ep = apiEndpoints.find(e => e.id === props.value);
                      const colorMap: Record<string, string> = { GET: 'green', POST: 'blue', PUT: 'orange', DELETE: 'red', PATCH: 'purple' };
                      const method = ep?.method?.toUpperCase() || '';
                      const color = colorMap[method] || 'default';
                      return (
                        <Tag color={color} closable={props.closable} onClose={props.onClose} style={{ margin: 2 }}>
                          {method} {ep?.path || props.value}
                        </Tag>
                      );
                    }}
                  />
                </div>
                <div style={{ fontSize: 12, color: '#888', marginTop: 4 }}>
                  AI 会将接口 Schema 注入提示词，生成可执行的 api_call 测试步骤
                </div>
              </div>

              <div style={{ marginBottom: '16px' }}>
                <Text strong>需求描述:</Text>
                <div style={{ marginTop: '8px' }}>
                  <Input.TextArea
                    ref={textAreaRef}
                    rows={14}
                    placeholder='请输入需求描述... 已关联的 API 接口将作为参考，生成包含可执行 api_call 步骤的测试用例。'
                    value={requirement}
                    onChange={(e) => onRequirementChange(e.target.value)}
                    style={{
                      border: '1px solid #d9d9d9',
                      borderRadius: '8px',
                      fontSize: '14px',
                    }}
                  />
                </div>
              </div>

              <Button
                type='primary'
                size='large'
                onClick={onGenerate}
                loading={loading}
                disabled={!requirement.trim()}
                block
                className={loading ? 'generate-btn-loading' : ''}
                style={{ borderRadius: '8px', fontSize: '16px', height: '48px' }}
              >
                AI 生成测试用例
              </Button>
            </div>
          )}
        </Card>
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
