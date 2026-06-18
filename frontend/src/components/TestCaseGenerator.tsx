import React, { useRef, useState, useEffect, useCallback } from 'react';
import { Card, Typography, Input, Button, Select, Space, Spin, Tag, Tooltip, message as antMessage } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
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
      antMessage.warning('Please enter requirement text first');
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
          antMessage.info('No matching API endpoints found');
          return;
        }
        if (onApiEndpointChange) onApiEndpointChange(matchedIds);
        if (!selectedApiProjectId && res.data.matches.length > 0) {
          const projId = res.data.matches[0].project_id;
          if (onApiProjectChange) onApiProjectChange(projId);
          if (!apiEndpoints.length) loadApiEndpoints(projId);
        }
        antMessage.success('Smart matched ' + matchedIds.length + ' API endpoints');
      } else {
        antMessage.info('No matching API endpoints found');
      }
    } catch (e: any) {
      console.error('Smart match failed:', e);
      antMessage.error('Smart match failed: ' + (e?.response?.data?.message || e.message));
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
        <Card title='Generate Test Cases' variant='borderless'>
          {!selectedSession ? (
            <div style={{ textAlign: 'center', padding: '40px 0' }}>
              <Text type='secondary'>Please select or create a session first</Text>
            </div>
          ) : (
            <div>
              <div style={{ marginBottom: '16px' }}>
                <Text strong>Session:</Text> <Text>{selectedSession.name}</Text>
              </div>
              {!selectedModule ? (
                <div style={{ marginBottom: '16px' }}>
                  <Text strong>Module:</Text> <Text>None</Text>
                </div>
              ) : (
                <div style={{ marginBottom: '16px' }}>
                  <Text strong>Module:</Text> <Text>{modules.find(m => m.id === selectedModule)?.module_name || 'None'}</Text>
                </div>
              )}

              <div style={{ marginBottom: 16, padding: 12, border: '1px solid #f0f0f0', borderRadius: 8, background: '#fafafa'}}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <Text strong>Link API Endpoints:</Text>
                  <Tooltip title='Auto-match API endpoints by requirement text'>
                    <Button
                      size='small'
                      icon={<ThunderboltOutlined />}
                      onClick={handleSmartMatch}
                      loading={smartMatching}
                      disabled={!requirement.trim()}
                    >
                      {' '}Smart Match
                    </Button>
                  </Tooltip>
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <Select
                    style={{ flex: 1 }}
                    placeholder='Select API project'
                    allowClear
                    value={selectedApiProjectId}
                    onChange={handleProjectChange}
                    options={apiProjects.map(p => ({ label: p.name, value: p.id }))}
                  />
                  <Select
                    mode='multiple'
                    style={{ flex: 2 }}
                    placeholder='Select endpoints (multi)'
                    value={selectedApiEndpointId || []}
                    onChange={handleEndpointChange}
                    options={apiEndpoints.map(e => ({
                      label: endpointLabel(e),
                      value: e.id,
                    }))}
                    notFoundContent={loadingApis ? <Spin size='small' /> : 'No API endpoints'}
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
                  AI will inject endpoint schemas into the prompt and generate executable api_call steps
                </div>
              </div>

              <div style={{ marginBottom: '16px' }}>
                <Text strong>Requirement:</Text>
                <div style={{ marginTop: '8px' }}>
                  <Input.TextArea
                    ref={textAreaRef}
                    rows={14}
                    placeholder='Describe your requirements... Linked API endpoints will be used as reference for generating test steps with executable api_call blocks.'
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
                style={{ borderRadius: '8px', fontSize: '16px', height: '48px' }}
              >
                AI Generate Test Cases
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
