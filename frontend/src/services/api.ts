import axios from 'axios';
import { message } from 'antd';
import keycloak from './keycloak';
import { Session, TestCase, ApiResponse, TestCaseResponse, Module, UpdateSessionRequest, HistoryPrompt, SavedRequest, GlobalParameter, ProxyRequest, ExtractVariablesRequest, ProxyResponse, MockConfig, McpServer, Skill, ApiProject, ApiEndpoint, ApiEndpointRunPayload, ApiScenario, ApiScenarioResult, ApiScenarioBatchRunRequest, ApiScenarioBatchRunResult, ApiImportResult, ApiSyncResult } from '../types';

// 创建axios实例
const api = axios.create({
  baseURL: '/api',
  timeout: 300000, // 增加超时时间到300秒
  // 移除默认的Content-Type，让axios根据请求数据自动设置
});

// 请求拦截器 - 注入 Bearer Token
api.interceptors.request.use(
  (config) => {
    // 注入 Keycloak Token
    if (keycloak.authenticated && keycloak.token) {
      config.headers.Authorization = `Bearer ${keycloak.token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// 响应拦截器 - 处理 401 自动刷新 Token
api.interceptors.response.use(
  (response) => {
    return response.data;
  },
  async (error) => {
    const originalRequest = error.config;

    if (error.response?.status === 403) {
      console.error('Permission denied for this feature.');
      message.error('无权限访问该功能');
      return Promise.reject(error);
    }

    // 如果是 401 且未重试过，尝试刷新 Token
    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;

      try {
        const refreshed = await keycloak.updateToken(30);
        if (refreshed) {
          // Token 刷新成功，重试原请求
          originalRequest.headers.Authorization = `Bearer ${keycloak.token}`;
          return api(originalRequest);
        }
      } catch (refreshError) {
        // Token 刷新失败，跳转登录
        console.error('Token refresh failed, redirecting to login');
        keycloak.login();
        return Promise.reject(error);
      }
      // Token 未刷新但仍然 401，说明后端验证有问题，不再循环登录
      console.error('Token is valid but server rejected it (401). Check backend auth configuration.');
    }

    return Promise.reject(error);
  }
);

// 会话管理API
export const sessionApi = {
  // 获取所有会话
  getSessions: (): Promise<ApiResponse<Session[]>> => api.get('/sessions/'),

  // 获取单个会话
  getSession: (id: number): Promise<ApiResponse<Session>> => api.get(`/sessions/${id}`),

  // 创建会话
  createSession: (name: string): Promise<ApiResponse<Session>> => api.post('/sessions/', { name }),

  // 更新会话
  updateSession: (id: number, data: UpdateSessionRequest): Promise<ApiResponse<Session>> => api.put(`/sessions/${id}`, data),

  // 删除会话
  deleteSession: (id: number): Promise<ApiResponse> => api.delete(`/sessions/${id}`)
};

// 测试用例管理API
export const testcaseApi = {
  // 获取会话的测试用例
  getTestcases: (sessionId: number | undefined, filters?: { case_name?: string; status?: string; bug_id?: string; exist_bug?: boolean; module_id?: number | string }): Promise<ApiResponse<TestCaseResponse>> => {
    const params = new URLSearchParams();
    if (filters?.case_name) params.append('case_name', filters.case_name);
    if (filters?.status) params.append('status', filters.status);
    if (filters?.bug_id) params.append('bug_id', filters.bug_id);
    if (filters?.exist_bug !== undefined) params.append('exist_bug', filters.exist_bug.toString());
    if (filters?.module_id !== undefined) params.append('module_id', filters.module_id.toString());
    return api.get(`/testcases/${sessionId}/testcases?${params.toString()}`);
  },

  // 生成测试用例
  generateTestcases: (sessionId: number, requirement: string, modelConfig?: {
    model_type: 'api' | 'ollama';
    api_key?: string;
    api_base_url?: string;
    api_proxy_url?: string;
    ollama_url?: string;
    ollama_model?: string;
  }, imageBase64?: string | null,
    moduleId?: number | null,
    selectedSkills?: string[],
    apiEndpointId?: number[] | number | null,
    apiProjectId?: number | null): Promise<ApiResponse<TestCase[]>> => {
    // 创建FormData
    const formData = new FormData();

    // 始终添加requirement，即使为空字符串，与后端预期一致
    formData.append('requirement', requirement);

    // 添加模块ID
    if (moduleId !== null) {
      formData.append('module_id', moduleId?.toString() ?? '');
    }

    // 添加选中的技能
    if (selectedSkills && selectedSkills.length > 0) {
      formData.append('selected_skills', JSON.stringify(selectedSkills));
    }

    // 添加模型配置
    if (modelConfig) {
      formData.append('model_type', modelConfig.model_type);
      if (modelConfig.api_key) {
        formData.append('api_key', modelConfig.api_key);
      }
      if (modelConfig.api_base_url) {
        formData.append('api_base_url', modelConfig.api_base_url);
      }
      if (modelConfig.api_proxy_url) {
        formData.append('api_proxy_url', modelConfig.api_proxy_url);
      }
      if (modelConfig.ollama_url) {
        formData.append('ollama_url', modelConfig.ollama_url);
      }
      if (modelConfig.ollama_model) {
        formData.append('ollama_model', modelConfig.ollama_model);
      }
    }

    // 处理图片数据 - 修复
    if (imageBase64) {
      console.log('准备上传图片，base64长度:', imageBase64.length);
      try {
        // 将base64转换为Blob
        const byteCharacters = atob(imageBase64);
        const byteNumbers = new Array(byteCharacters.length);
        for (let i = 0; i < byteCharacters.length; i++) {
          byteNumbers[i] = byteCharacters.charCodeAt(i);
        }
        const byteArray = new Uint8Array(byteNumbers);
        const blob = new Blob([byteArray], { type: 'image/png' });

        console.log('转换后的Blob:', { size: blob.size, type: blob.type });

        // 添加到FormData
        formData.append('file', blob, 'image.png');
        console.log('图片Blob已添加到FormData');

        // 验证FormData中是否包含file字段
        console.log('FormData中是否包含file:', formData.has('file'));
      } catch (error) {
        console.error('图片转换或添加到FormData失败:', error);
      }
    } else {
      console.log('没有图片数据需要上传');
    }

    // 打印FormData中的所有字段，用于调试
    console.log('FormData内容:');
    formData.forEach((value, key) => {
      if (value instanceof Blob) {
        console.log(`  ${key}: Blob (${value.type}, ${value.size} bytes)`);
      } else {
        console.log(`  ${key}: ${value}`);
      }
    });

    // 发送请求，axios会自动设置正确的Content-Type头
    // 包括multipart/form-data和边界信息
    // ?????API??ID?????????ID?
    if (apiEndpointId !== null && apiEndpointId !== undefined) {
      const endpointStr = Array.isArray(apiEndpointId) ? apiEndpointId.join(",") : String(apiEndpointId);
      if (endpointStr) {
        formData.append("api_endpoint_id", endpointStr);
      }
    }
    if (apiProjectId !== null && apiProjectId !== undefined) {
      formData.append("api_project_id", String(apiProjectId));
    }

    return api.post(`/testcases/${sessionId}/testcases`, formData);
  },

  // 创建测试用例
  createTestcase: (sessionId: number, testcase: Omit<TestCase, 'id' | 'created_at' | 'updated_at'>): Promise<ApiResponse<TestCase>> =>
    api.post(`/testcases/${sessionId}/testcases/create`, testcase),

  // 更新测试用例
  updateTestcase: (sessionId: number, id: number, testcase: Partial<TestCase>): Promise<ApiResponse<TestCase>> =>
    api.put(`/testcases/${sessionId}/testcases/${id}`, testcase),

  // 删除测试用例
  deleteTestcase: (sessionId: number, ids: number[]): Promise<ApiResponse> => api.delete(`/testcases/${sessionId}/testcases`, { data: ids }),

  // 移动测试用例
  moveTestcase: (testcaseId: number, sessionId: number, moduleId: number | null): Promise<ApiResponse> =>
    api.post(`/testcases/${testcaseId}/move`, { session_id: sessionId, module_id: moduleId }),

  // 批量移动测试用例
  batchMoveTestcase: (testcaseIds: number[], sessionId: number, moduleId: number | null): Promise<ApiResponse> =>
    api.post(`/testcases/move`, { testcase_ids: testcaseIds, session_id: sessionId, module_id: moduleId }),

  /** ?????????? API ?? */
  executeTestcase: (sessionId: number, testcaseId: number): Promise<ApiResponse<{ passed: boolean; status: string; result: any }>> =>
    api.post(`/testcases/${sessionId}/testcases/${testcaseId}/execute`),

  // 获取测试用例执行日志
  getExecutionLogs: (sessionId: number, testcaseId: number): Promise<ApiResponse<any[]>> =>
    api.get(`/testcases/${sessionId}/testcases/${testcaseId}/execution-logs`),
};

// 模块管理API
export const moduleApi = {
  // 获取会话的所有模块
  getModules: (sessionId: number): Promise<ApiResponse<Module[]>> => api.get(`/module/${sessionId}/modules`),

  // 获取单个模块
  getModule: (moduleId: number): Promise<ApiResponse<Module>> => api.get(`/module/${moduleId}`),

  // 创建模块
  createModule: (module: Omit<Module, 'id' | 'created_at' | 'updated_at'>): Promise<ApiResponse<Module>> =>
    api.post('/module/', module),

  // 更新模块
  updateModule: (moduleId: number, module: Partial<Module>): Promise<ApiResponse<Module>> =>
    api.put(`/module/${moduleId}`, module),

  // 删除模块
  deleteModule: (moduleId: number): Promise<ApiResponse> => api.delete(`/module/${moduleId}`)
};

// 健康检查
export const healthApi = {
  checkHealth: () => api.get('/health')
};

// 历史提示词API
export const historyPromptApi = {
  // 获取模块下的历史提示词列表
  getPrompts: (moduleId: number): Promise<ApiResponse<HistoryPrompt[]>> =>
    api.get(`/history_prompt/${moduleId}`),

  // 获取会话下的所有历史提示词
  getSessionPrompts: (sessionId: number): Promise<ApiResponse<HistoryPrompt[]>> =>
    api.get(`/history_prompt/session/${sessionId}`),

  // 创建历史提示词
  createPrompt: (data: { content: string; module_id?: number | null; session_id?: number | null }): Promise<ApiResponse<HistoryPrompt>> =>
    api.post('/history_prompt', data),

  // 删除历史提示词
  deletePrompt: (promptId: number): Promise<ApiResponse> =>
    api.delete(`/history_prompt/${promptId}`)
};

// 保存的请求API
export const savedRequestApi = {
  // 获取所有保存的请求
  getRequests: (): Promise<ApiResponse<SavedRequest[]>> => api.get('/saved-requests'),

  // 创建保存的请求
  createRequest: (request: Omit<SavedRequest, 'id' | 'created_at' | 'updated_at'>): Promise<ApiResponse<SavedRequest>> => api.post('/saved-requests', request),

  // 更新保存的请求
  updateRequest: (requestId: number, request: Partial<SavedRequest>): Promise<ApiResponse<SavedRequest>> => api.put(`/saved-requests/${requestId}`, request),

  // 删除保存的请求
  deleteRequest: (requestId: number): Promise<ApiResponse> => api.delete(`/saved-requests/${requestId}`)
};

// 全局参数（环境）API
export const globalParameterApi = {
  // 获取所有环境
  getEnvironments: (): Promise<ApiResponse<GlobalParameter[]>> => api.get('/global-parameters'),

  // 创建环境
  createEnvironment: (environment: Omit<GlobalParameter, 'id' | 'created_at' | 'updated_at'>): Promise<ApiResponse<GlobalParameter>> => api.post('/global-parameters', environment),

  // 更新环境
  updateEnvironment: (environmentId: number, environment: Partial<GlobalParameter>): Promise<ApiResponse<GlobalParameter>> => api.put(`/global-parameters/${environmentId}`, environment),

  // 删除环境
  deleteEnvironment: (environmentId: number): Promise<ApiResponse> => api.delete(`/global-parameters/${environmentId}`),

  // 提取并保存变量
  extractAndSaveVariables: (data: ExtractVariablesRequest): Promise<ApiResponse<Record<string, string>>> => api.post('/global-parameters/extract-and-save', data)
};

interface TestVariableRequest {
  expression: string;
  environment_id?: number;
}

interface TestVariableResponse {
  expression: string;
  result: string;
  unresolved: string[];
}

// 代理转发API
export const proxyApi = {
  // 转发请求
  forwardRequest: (request: ProxyRequest): Promise<ProxyResponse<ApiResponse<any>>> => api.post('/proxy/forward', request),
  // 测试变量
  testVariable: (request: TestVariableRequest): Promise<ApiResponse<TestVariableResponse>> => api.post('/proxy/test-variable', request)
};

// 定时任务API
export const scheduledTaskApi = {
  // 获取所有定时任务
  getTasks: (): Promise<ApiResponse<any[]>> => api.get('/scheduled-tasks'),

  // 创建定时任务
  createTask: (task: any): Promise<ApiResponse<any>> => api.post('/scheduled-tasks', task),

  // 更新定时任务
  updateTask: (taskId: number, task: any): Promise<ApiResponse<any>> => api.put(`/scheduled-tasks/${taskId}`, task),

  // 删除定时任务
  deleteTask: (taskId: number): Promise<ApiResponse> => api.delete(`/scheduled-tasks/${taskId}`),

  // 手动触发定时任务
  runTask: (taskId: number): Promise<ApiResponse> => api.post(`/scheduled-tasks/${taskId}/run`)
};

// MCP 工具 API
export const mcpApi = {
  listTools: (servers: any[]): Promise<ApiResponse<any[]>> => api.post('/mcp/list-tools', servers),
};

// MCP 服务器配置持久化 API
export const mcpServerApi = {
  // 获取当前用户的所有 MCP 服务器配置
  list: (): Promise<ApiResponse<McpServer[]>> => api.get('/mcp/servers'),

  // 创建 MCP 服务器配置
  create: (server: Omit<McpServer, 'id' | 'created_at' | 'updated_at'>): Promise<ApiResponse<McpServer>> => api.post('/mcp/servers', server),

  // 更新 MCP 服务器配置
  update: (serverId: number, server: Partial<McpServer>): Promise<ApiResponse<McpServer>> => api.put(`/mcp/servers/${serverId}`, server),

  // 删除 MCP 服务器配置
  delete: (serverId: number): Promise<ApiResponse> => api.delete(`/mcp/servers/${serverId}`),
};

// Skills Hub API
export const skillsApi = {
  list: (): Promise<ApiResponse<Skill[]>> => api.get('/skills'),
  get: (name: string): Promise<ApiResponse<Skill>> => api.get(`/skills/${name}`),
  install: (url: string): Promise<ApiResponse<Skill>> => api.post('/skills/install', { url }),
  delete: (name: string): Promise<ApiResponse> => api.delete(`/skills/${name}`),
};

// 系统配置 API
export const configApi = {
  getLanhuCookie: (): Promise<ApiResponse<{ cookie: string; has_cookie: boolean }>> => api.get('/config/lanhu-cookie'),
  setLanhuCookie: (cookie: string): Promise<ApiResponse> => api.post('/config/lanhu-cookie', { cookie }),
};

// Mock配置API
export const mockConfigApi = {
  // 获取所有Mock配置
  getConfigs: (): Promise<ApiResponse<MockConfig[]>> => api.get('/mock-configs'),

  // 创建Mock配置
  createConfig: (config: Omit<MockConfig, 'id' | 'created_at' | 'updated_at'>): Promise<ApiResponse<MockConfig>> => api.post('/mock-configs', config),

  // 更新Mock配置
  updateConfig: (configId: number, config: Partial<MockConfig>): Promise<ApiResponse<MockConfig>> => api.put(`/mock-configs/${configId}`, config),

  // 删除Mock配置
  deleteConfig: (configId: number): Promise<ApiResponse> => api.delete(`/mock-configs/${configId}`)
};

export const apiTestApi = {
  getProjects: (): Promise<ApiResponse<ApiProject[]>> => api.get('/api-test/projects'),
  updateProject: (projectId: number, project: Partial<ApiProject>): Promise<ApiResponse<ApiProject>> =>
    api.put(`/api-test/projects/${projectId}`, project),
  deleteProject: (projectId: number): Promise<ApiResponse> => api.delete(`/api-test/projects/${projectId}`),
  syncProject: (projectId: number): Promise<ApiResponse<ApiSyncResult>> => api.post(`/api-test/projects/${projectId}/sync`),
  importOpenApi: (data: { name?: string; url?: string; file?: File | null }): Promise<ApiResponse<ApiImportResult>> => {
    const formData = new FormData();
    if (data.name) formData.append('name', data.name);
    if (data.url) formData.append('url', data.url);
    if (data.file) formData.append('file', data.file);
    return api.post('/api-test/import', formData);
  },
  getEndpoints: (projectId: number): Promise<ApiResponse<ApiEndpoint[]>> =>
    api.get(`/api-test/projects/${projectId}/endpoints`),
  createEndpoint: (projectId: number, endpoint: Partial<ApiEndpoint>): Promise<ApiResponse<ApiEndpoint>> =>
    api.post(`/api-test/projects/${projectId}/endpoints`, endpoint),
  updateEndpoint: (endpointId: number, endpoint: Partial<ApiEndpoint>): Promise<ApiResponse<ApiEndpoint>> =>
    api.put(`/api-test/endpoints/${endpointId}`, endpoint),
  deleteEndpoint: (endpointId: number): Promise<ApiResponse> => api.delete(`/api-test/endpoints/${endpointId}`),
  runEndpoint: (endpointId: number, payload: ApiEndpointRunPayload): Promise<ApiResponse<any>> =>
    api.post(`/api-test/endpoints/${endpointId}/run`, payload),
  generateEndpointBody: (endpointId: number, payload: Record<string, any>): Promise<ApiResponse<{ body: string; used_ai: boolean; message: string }>> =>
    api.post(`/api-test/endpoints/${endpointId}/generate-body`, payload),
  generateEndpointScenarioTests: (endpointId: number): Promise<ApiResponse<ApiScenario>> =>
    api.post(`/api-test/endpoints/${endpointId}/generate-scenario-tests`),
  getScenarios: (projectId: number): Promise<ApiResponse<ApiScenario[]>> =>
    api.get(`/api-test/projects/${projectId}/scenarios`),
  createScenario: (projectId: number, scenario: Partial<ApiScenario>): Promise<ApiResponse<ApiScenario>> =>
    api.post(`/api-test/projects/${projectId}/scenarios`, scenario),
  updateScenario: (scenarioId: number, scenario: Partial<ApiScenario>): Promise<ApiResponse<ApiScenario>> =>
    api.put(`/api-test/scenarios/${scenarioId}`, scenario),
  deleteScenario: (scenarioId: number): Promise<ApiResponse> => api.delete(`/api-test/scenarios/${scenarioId}`),
  getScenarioResults: (scenarioId: number, limit?: number): Promise<ApiResponse<ApiScenarioResult[]>> =>
    api.get(`/api-test/scenarios/${scenarioId}/results${limit ? `?limit=${limit}` : ''}`),
  runScenario: (scenarioId: number): Promise<ApiResponse<ApiScenarioResult>> => api.post(`/api-test/scenarios/${scenarioId}/run`),
  runScenarios: (projectId: number, payload: ApiScenarioBatchRunRequest): Promise<ApiResponse<ApiScenarioBatchRunResult>> =>
    api.post(`/api-test/projects/${projectId}/scenarios/run-batch`, payload),
  matchEndpoint: (data: { requirement: string; project_id?: number }): Promise<ApiResponse<{ matches: any[]; total_matches: number }>> =>
    api.post("/api-test/match-endpoint", data),
};
