import React, { useRef, useState, useEffect } from 'react';
import { Card, Typography, Input, Button, Select, Space, Spin } from 'antd';
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
  selectedApiEndpointId?: number | null;
  onApiEndpointChange?: (id: number | null) => void;
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

  // ?? API ????
  const loadApiProjects = async () => {
    try {
      const { apiTestApi } = await import("../services/api");
      const res = await apiTestApi.getProjects();
      if (res.code === 200 && res.data) {
        setApiProjects(res.data);
      }
    } catch (e) {
      console.error("?? API ????:", e);
    }
  };

  // ?? API Endpoint ??
  const loadApiEndpoints = async (projectId: number) => {
    setLoadingApis(true);
    try {
      const { apiTestApi } = await import("../services/api");
      const res = await apiTestApi.getEndpoints(projectId);
      if (res.code === 200 && res.data) {
        setApiEndpoints(res.data);
      }
    } catch (e) {
      console.error("?? API Endpoints ??:", e);
    } finally {
      setLoadingApis(false);
    }
  };

  // ????? API ????
  useEffect(() => {
    loadApiProjects();
  }, []);

  const handleSelectPrompt = (content: string) => {
    onRequirementChange(content);
  };

  const handlePromptsChange = () => {
  };

  // ?? API ????
  const handleProjectChange = (val: number | null) => {
    if (onApiProjectChange) onApiProjectChange(val);
    if (onApiEndpointChange) onApiEndpointChange(null);
    setApiEndpoints([]);
    if (val) loadApiEndpoints(val);
  };

  // ?? Endpoint ??
  const handleEndpointChange = (val: number | null) => {
    if (onApiEndpointChange) onApiEndpointChange(val);
  };

  return (
    <div style={{ display: "flex", height: "100%" }}>
      <div style={{ flex: 1, overflow: "auto" }}>
        <Card title="??????" variant="borderless">
          {!selectedSession ? (
            <div style={{ textAlign: "center", padding: "40px 0" }}>
              <Text type="secondary">??????????????</Text>
            </div>
          ) : (
            <div>
              <div style={{ marginBottom: "16px" }}>
                <Text strong>????:</Text> <Text>{selectedSession.name}</Text>
              </div>
              {!selectedModule ? (
                <div style={{ marginBottom: "16px" }}>
                  <Text strong>????:</Text> <Text>?</Text>
                </div>
              ) : (
                <div style={{ marginBottom: "16px" }}>
                  <Text strong>????:</Text> <Text>{modules.find(m => m.id === selectedModule)?.module_name || "?"}</Text>
                </div>
              )}

              {/* ?? API ???? */}
              <div style={{ marginBottom: 16 }}>
                <Text strong>?? API ??????:</Text>
                <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                  <Select
                    style={{ flex: 1 }}
                    placeholder="?? API ??"
                    allowClear
                    value={selectedApiProjectId}
                    onChange={handleProjectChange}
                    options={apiProjects.map(p => ({ label: p.name, value: p.id }))}
                  />
                  <Select
                    style={{ flex: 1 }}
                    placeholder="????"
                    allowClear
                    value={selectedApiEndpointId}
                    onChange={handleEndpointChange}
                    options={apiEndpoints.map(e => ({
                      label: e.tags && e.tags.length > 0 ? e.tags[0] + " " : "" + e.method + " " + e.path,
                      value: e.id,
                    }))}
                    notFoundContent={loadingApis ? <Spin size="small" /> : "???? API ??"}
                    loading={loadingApis}
                  />
                </div>
                <div style={{ fontSize: 12, color: "#888", marginTop: 4 }}>
                  ??????AI ????????/?? Schema ??????????????????? api_call ??????
                </div>
              </div>

              {/* ?????? */}
              <div style={{ marginBottom: "16px" }}>
                <Text strong>??????:</Text>
                <div style={{ marginTop: "8px" }}>
                  <Input.TextArea
                    ref={textAreaRef}
                    rows={14}
                    placeholder="???????????????????????????????????????????..."
                    value={requirement}
                    onChange={(e) => onRequirementChange(e.target.value)}
                    style={{
                      border: "1px solid #d9d9d9",
                      borderRadius: "8px",
                      fontSize: "14px",
                    }}
                  />
                </div>
              </div>

              {/* ???? */}
              <Button
                type="primary"
                size="large"
                onClick={onGenerate}
                loading={loading}
                disabled={!requirement.trim()}
                block
                style={{ borderRadius: "8px", fontSize: "16px", height: "48px" }}
              >
                ??????
              </Button>
            </div>
          )}
        </Card>
      </div>

      {/* ?????????? */}
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
