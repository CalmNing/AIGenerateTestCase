import axios from 'axios';
import { message } from 'antd';
import keycloak from './keycloak';
import { Session, TestCase, ApiResponse, TestCaseResponse, Module, UpdateSessionRequest, HistoryPrompt, SavedRequest, SavedRequestFolder, GlobalParameter, ProxyRequest, ExtractVariablesRequest, ProxyResponse, MockConfig, McpServer, Skill, ApiProject, ApiEndpoint, ApiEndpointRunPayload, ApiScenario, ApiScenarioResult, ApiScenarioBatchRunRequest, ApiScenarioBatchRunResult, ApiImportResult, ApiSyncResult, ScheduledTask, ScheduledTaskInput, McpToolInfo, NavCategory, NavLink, NavTreeNode, MockProxyRule, MockProxyStatus, MockLogItem } from '../types';

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
  getTestcases: (sessionId: number | undefined, filters?: { case_name?: string; status?: string; bug_id?: string; exist_bug?: boolean; module_id?: number | string; test_point_id?: number }, pagination?: { offset?: number; limit?: number }): Promise<ApiResponse<TestCaseResponse>> => {
    const params = new URLSearchParams();
    if (filters?.case_name) params.append('case_name', filters.case_name);
    if (filters?.status) params.append('status', filters.status);
    if (filters?.bug_id) params.append('bug_id', filters.bug_id);
    if (filters?.exist_bug !== undefined) params.append('exist_bug', filters.exist_bug.toString());
    if (filters?.module_id !== undefined) params.append('module_id', filters.module_id.toString());
    if (filters?.test_point_id !== undefined) params.append('test_point_id', filters.test_point_id.toString());
    if (pagination?.offset !== undefined) params.append('offset', String(pagination.offset));
    if (pagination?.limit !== undefined) params.append('limit', String(pagination.limit));
    return api.get(`/testcases/${sessionId}/testcases?${params.toString()}`);
  },

  // 生成测试用例
  generateTestcases: (sessionId: number, requirement: string, modelConfig?: {
    model_type: 'api' | 'ollama';
    api_key?: string;
    api_base_url?: string;
    api_proxy_url?: string;
    api_model?: string;
    ollama_url?: string;
    ollama_model?: string;
  }, imageBase64?: string | null,
    moduleId?: number | null,
    selectedSkills?: string[],
    apiEndpointId?: number[] | number | null,
    apiProjectId?: number | null,
    apiEndpointOverrides?: Record<number, any> | null): Promise<ApiResponse<TestCase[]>> => {
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
      if (modelConfig.api_model) {
        formData.append('api_model', modelConfig.api_model);
      }
      if (modelConfig.ollama_url) {
        formData.append('ollama_url', modelConfig.ollama_url);
      }
      if (modelConfig.ollama_model) {
        formData.append('ollama_model', modelConfig.ollama_model);
      }
    }

    // 处理图片数据
    if (imageBase64) {
      try {
        // 将base64转换为Blob
        const byteCharacters = atob(imageBase64);
        const byteNumbers = new Array(byteCharacters.length);
        for (let i = 0; i < byteCharacters.length; i++) {
          byteNumbers[i] = byteCharacters.charCodeAt(i);
        }
        const byteArray = new Uint8Array(byteNumbers);
        const blob = new Blob([byteArray], { type: 'image/png' });

        // 添加到FormData
        formData.append('file', blob, 'image.png');
      } catch (error) {
        console.error('图片转换或添加到FormData失败:', error);
      }
    }

    // 发送请求，axios会自动设置正确的Content-Type头
    // 包括multipart/form-data和边界信息
    // 追加 API 接口 ID 与项目 ID 到 FormData
    if (apiEndpointId !== null && apiEndpointId !== undefined) {
      const endpointStr = Array.isArray(apiEndpointId) ? apiEndpointId.join(",") : String(apiEndpointId);
      if (endpointStr) {
        formData.append("api_endpoint_id", endpointStr);
      }
    }
    if (apiProjectId !== null && apiProjectId !== undefined) {
      formData.append("api_project_id", String(apiProjectId));
    }

    // 添加端点覆盖配置
    if (apiEndpointOverrides && Object.keys(apiEndpointOverrides).length > 0) {
      formData.append("api_endpoint_overrides", JSON.stringify(apiEndpointOverrides));
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

  /** 执行测试用例对应的 API 调用 */
  executeTestcase: (sessionId: number, testcaseId: number): Promise<ApiResponse<{ passed: boolean; status: string; result: any }>> =>
    api.post(`/testcases/${sessionId}/testcases/${testcaseId}/execute`),

  // 获取测试用例执行日志
  getExecutionLogs: (sessionId: number, testcaseId: number): Promise<ApiResponse<any[]>> =>
    api.get(`/testcases/${sessionId}/testcases/${testcaseId}/execution-logs`),

  // 推断测试用例的接口依赖关系
  inferDependencies: (sessionId: number, testcaseId: number): Promise<ApiResponse<{ added_post_actions: number; replaced_fields: number; extra_endpoint_ids: number[] }>> =>
    api.post(`/testcases/${sessionId}/testcases/${testcaseId}/infer-dependencies`),

  /** 手动触发真实执行验证，回写一致性/置信度/验证结果 */
  validateTestcase: (testcaseId: number, environmentId?: number): Promise<ApiResponse<{ validation_status: string; validation_detail: string; validation_at: string }>> =>
    api.post(`/testcases/${testcaseId}/validate`, environmentId ? { environment_id: environmentId } : {}),


  // ========== 4阶段 Pipeline 深度生成 ==========

  /** 启动 4 阶段深度生成（支持接口测试和功能测试） */
  generateV2: (
    sessionId: number,
    requirementText: string,
    options?: {
      selectedSkills?: string[];
      apiEndpointIds?: number[];
      apiProjectId?: number | null;
      apiEndpointOverrides?: Record<number, { body?: string; headers?: any[]; parameters?: any[] }>;
      moduleId?: number | string;
      file?: File | null;
      pipelineMode?: 'auto' | 'manual';
    }
  ): Promise<ApiResponse<{ task_id: string }>> => {
    const formData = new FormData();
    formData.append("session_id", sessionId.toString());
    formData.append("requirement_text", requirementText);
    formData.append("pipeline_mode", options?.pipelineMode || "auto");
    if (options?.moduleId !== undefined && options.moduleId !== '' && options.moduleId !== 0) {
      formData.append("module_id", options.moduleId.toString());
    }
    if (options?.selectedSkills && options.selectedSkills.length > 0) {
      formData.append("selected_skills", JSON.stringify(options.selectedSkills));
    }
    if (options?.apiEndpointIds && options.apiEndpointIds.length > 0) {
      formData.append("api_endpoint_id", options.apiEndpointIds.join(","));
    }
    if (options?.apiProjectId) {
      formData.append("api_project_id", options.apiProjectId.toString());
    }
    if (options?.apiEndpointOverrides && Object.keys(options.apiEndpointOverrides).length > 0) {
      formData.append("api_endpoint_overrides", JSON.stringify(options.apiEndpointOverrides));
    }
    if (options?.file) {
      formData.append("file", options.file);
    }
    return api.post('/testcases/generate-v2', formData);
  },

  /** 批量为会话所有测试点生成用例（异步，返回 task_id） */
  batchGenerateCases: (
    sessionId: number,
    moduleId?: number | null
  ): Promise<ApiResponse<{ task_id: string }>> => {
    const params = new URLSearchParams();
    if (moduleId) params.append('module_id', String(moduleId));
    const qs = params.toString();
    return api.post(`/testcases/sessions/${sessionId}/batch-generate-cases${qs ? `?${qs}` : ''}`);
  },

  /** 查询 pipeline 进度 */
  getPipelineStatus: (taskId: string): Promise<ApiResponse<{
    task_id: string;
    status: string;
    current_phase: number;
    total_phases: number;
    phase_name: string;
    progress: number;
    message: string;
    result: any;
    error: string | null;
  }>> => api.get(`/testcases/pipeline/${taskId}/status`),

  /** 获取需求点列表（分页，可按模块过滤，附带每个需求点的测试点计数） */
  getRequirementPoints: (sessionId: number, pagination?: { offset?: number; limit?: number; module_id?: number | null }): Promise<ApiResponse<{
    items: Array<{
      id: number;
      content: string;
      module_id?: number | null;
      module: string;
      priority: string;
      order_index: number;
      test_point_count: number;
      api_project_id?: number | null;
      api_endpoint_ids?: string | null;
      created_at: string;
    }>;
    totalNumber: number;
  }>> => {
    const params = new URLSearchParams();
    if (pagination?.offset !== undefined) params.append('offset', String(pagination.offset));
    if (pagination?.limit !== undefined) params.append('limit', String(pagination.limit));
    if (pagination?.module_id !== undefined && pagination?.module_id !== null) params.append('module_id', String(pagination.module_id));
    return api.get(`/testcases/sessions/${sessionId}/requirement-points?${params.toString()}`);
  },

  /** 获取测试点列表（可按需求点过滤、分页，附带每个测试点的用例计数） */
  getTestPoints: (sessionId: number, opts?: { requirement_point_id?: number; offset?: number; limit?: number }): Promise<ApiResponse<{
    items: Array<{
      id: number;
      requirement_point_id: number;
      content: string;
      test_type: string;
      design_method: string;
      priority: string;
      case_count: number;
      api_project_id?: number | null;
      api_endpoint_ids?: string | null;
      created_at: string;
    }>;
    totalNumber: number;
  }>> => {
    const params = new URLSearchParams();
    if (opts?.requirement_point_id !== undefined) params.append('requirement_point_id', String(opts.requirement_point_id));
    if (opts?.offset !== undefined) params.append('offset', String(opts.offset));
    if (opts?.limit !== undefined) params.append('limit', String(opts.limit));
    return api.get(`/testcases/sessions/${sessionId}/test-points?${params.toString()}`);
  },

  // ========== 需求点 / 测试点 手动管理 ==========

  /** 手动新增需求点 */
  createRequirementPoint: (data: {
    session_id: number;
    content: string;
    module_id?: number | null;
    priority?: string;
    api_project_id?: number | null;
    api_endpoint_ids?: string | null;
  }): Promise<ApiResponse<{ id: number }>> => api.post('/testcases/requirement-points', data),

  /** 编辑需求点 */
  updateRequirementPoint: (id: number, data: {
    content?: string; module_id?: number | null; priority?: string;
  }): Promise<ApiResponse<{ id: number }>> => api.put(`/testcases/requirement-points/${id}`, data),

  /** 删除需求点（级联） */
  deleteRequirementPoint: (id: number): Promise<ApiResponse> =>
    api.delete(`/testcases/requirement-points/${id}`),

  /** 需求点级联重生成测试点（异步，返回 task_id） */
  regenerateRequirementPoint: (id: number): Promise<ApiResponse<{ task_id: string }>> =>
    api.post(`/testcases/requirement-points/${id}/regenerate`),

  /** 手动新增测试点 */
  createTestPoint: (data: {
    session_id: number;
    requirement_point_id: number;
    content: string;
    test_type?: string;
    design_method?: string;
    priority?: string;
    api_project_id?: number | null;
    api_endpoint_ids?: string | null;
  }): Promise<ApiResponse<{ id: number }>> => api.post('/testcases/test-points', data),

  /** 编辑测试点 */
  updateTestPoint: (id: number, data: {
    content?: string; test_type?: string; design_method?: string; priority?: string;
  }): Promise<ApiResponse<{ id: number }>> => api.put(`/testcases/test-points/${id}`, data),

  /** 删除测试点（级联） */
  deleteTestPoint: (id: number): Promise<ApiResponse> =>
    api.delete(`/testcases/test-points/${id}`),

  /** 测试点重生成用例（异步，返回 task_id） */
  regenerateTestPoint: (id: number): Promise<ApiResponse<{ task_id: string }>> =>
    api.post(`/testcases/test-points/${id}/regenerate`),
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
  getRequests: (folderId?: number | null): Promise<ApiResponse<SavedRequest[]>> => {
    const params = folderId !== undefined && folderId !== null ? { folder_id: folderId } : {};
    return api.get('/saved-requests', { params });
  },

  // 创建保存的请求
  createRequest: (request: Omit<SavedRequest, 'id' | 'created_at' | 'updated_at'>): Promise<ApiResponse<SavedRequest>> => api.post('/saved-requests', request),

  // 更新保存的请求
  updateRequest: (requestId: number, request: Partial<SavedRequest>): Promise<ApiResponse<SavedRequest>> => api.put(`/saved-requests/${requestId}`, request),

  // 删除保存的请求
  deleteRequest: (requestId: number): Promise<ApiResponse> => api.delete(`/saved-requests/${requestId}`)
};

// 保存的请求文件夹API
export const savedRequestFolderApi = {
  getFolders: (): Promise<ApiResponse<SavedRequestFolder[]>> => api.get('/saved-request-folders'),
  createFolder: (data: { name: string; parent_id?: number | null }): Promise<ApiResponse<SavedRequestFolder>> => api.post('/saved-request-folders', data),
  updateFolder: (folderId: number, data: Partial<SavedRequestFolder>): Promise<ApiResponse<SavedRequestFolder>> => api.put(`/saved-request-folders/${folderId}`, data),
  deleteFolder: (folderId: number): Promise<ApiResponse> => api.delete(`/saved-request-folders/${folderId}`)
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
  getTasks: (): Promise<ApiResponse<ScheduledTask[]>> => api.get('/scheduled-tasks'),

  // 创建定时任务
  createTask: (task: ScheduledTaskInput): Promise<ApiResponse<ScheduledTask>> => api.post('/scheduled-tasks', task),

  // 更新定时任务
  updateTask: (taskId: number, task: ScheduledTaskInput): Promise<ApiResponse<ScheduledTask>> => api.put(`/scheduled-tasks/${taskId}`, task),

  // 删除定时任务
  deleteTask: (taskId: number): Promise<ApiResponse> => api.delete(`/scheduled-tasks/${taskId}`),

  // 手动触发定时任务
  runTask: (taskId: number): Promise<ApiResponse> => api.post(`/scheduled-tasks/${taskId}/run`)
};

// MCP 工具 API
export const mcpApi = {
  listTools: (servers: McpServer[]): Promise<ApiResponse<McpToolInfo[]>> => api.post('/mcp/list-tools', servers),
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

  // 从 JSON 数据导入 MCP 配置
  importJson: (data: Record<string, any>): Promise<ApiResponse<McpServer[]>> => api.post('/mcp/servers/import', data),

  // 从 JSON 文件导入 MCP 配置
  importJsonFile: (file: File): Promise<ApiResponse<McpServer[]>> => {
    const formData = new FormData();
    formData.append('file', file);
    return api.post('/mcp/servers/import/file', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },

  // 导出为标准 JSON 格式
  exportJson: (): Promise<{ mcpServers: Record<string, any> }> => api.get('/mcp/servers/export'),
};

// Skills Hub API
export const skillsApi = {
  list: (): Promise<ApiResponse<Skill[]>> => api.get('/skills'),
  get: (name: string): Promise<ApiResponse<Skill>> => api.get(`/skills/${name}`),
  install: (url: string): Promise<ApiResponse<Skill>> => api.post('/skills/install', { url }),
  delete: (name: string): Promise<ApiResponse> => api.delete(`/skills/${name}`),
};

// AI 对话 API
export const chatApi = {
  send: (
    messages: Array<{ role: string; content: string }>,
    selectedSkills?: string[]
  ): Promise<ApiResponse<{ reply: string }>> =>
    api.post('/chat', { messages, selected_skills: selectedSkills }),
};

// 系统配置 API
export const configApi = {
  getConfig: (): Promise<ApiResponse<{
    model_type: string;
    api_key: string;
    api_base_url: string;
    api_model: string;
    api_proxy_url: string;
    ollama_url: string;
    ollama_model: string;
    lanhu_cookie: string;
    bug_link_template: string;
  }>> => api.get('/config'),
  updateConfig: (config: {
    model_type?: string;
    api_key?: string;
    api_base_url?: string;
    api_model?: string;
    api_proxy_url?: string;
    ollama_url?: string;
    ollama_model?: string;
    lanhu_cookie?: string;
    bug_link_template?: string;
  }): Promise<ApiResponse> => api.put('/config', config),
  listModels: (apiKey: string, apiBaseUrl: string): Promise<ApiResponse<{ id: string; owned_by: string }[]>> =>
    api.get('/config/models', { params: { api_key: apiKey, api_base_url: apiBaseUrl } }),
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

// Mock 正向代理 API
export const mockProxyApi = {
  // 获取代理运行状态
  getStatus: (): Promise<ApiResponse<MockProxyStatus>> => api.get('/mock-proxy/status'),

  // 启动代理（可指定端口，默认取后端配置）
  start: (port?: number): Promise<ApiResponse<MockProxyStatus>> => api.post('/mock-proxy/start', { port }),

  // 停止代理
  stop: (): Promise<ApiResponse<MockProxyStatus>> => api.post('/mock-proxy/stop'),

  // 获取全部转发规则
  getRules: (): Promise<ApiResponse<MockProxyRule[]>> => api.get('/mock-proxy/rules'),

  // 创建转发规则
  createRule: (rule: Partial<MockProxyRule>): Promise<ApiResponse<MockProxyRule>> =>
    api.post('/mock-proxy/rules', rule),

  // 更新转发规则
  updateRule: (ruleId: number, rule: Partial<MockProxyRule>): Promise<ApiResponse<MockProxyRule>> =>
    api.put(`/mock-proxy/rules/${ruleId}`, rule),

  // 删除转发规则
  deleteRule: (ruleId: number): Promise<ApiResponse> => api.delete(`/mock-proxy/rules/${ruleId}`),

  // 获取 Mock 调用/代理流量日志
  getLogs: (params?: { page?: number; page_size?: number; matched?: boolean }): Promise<ApiResponse<MockLogItem[]>> =>
    api.get('/mock-logs', { params }),
};

// CA 证书下载地址（免认证，用原生下载而非 axios）
export const MOCK_PROXY_CA_CERT_URL = '/api/mock-proxy/ca-cert';

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
  toggleProjectMock: (projectId: number, payload: { mock_enabled?: boolean; mock_prefix?: string }): Promise<ApiResponse<ApiProject>> =>
    api.post(`/api-test/projects/${projectId}/mock`, payload),
  generateEndpointScenarioTests: (endpointId: number): Promise<ApiResponse<ApiScenario>> =>
    api.post(`/api-test/endpoints/${endpointId}/generate-scenario-tests`),
  getScenarios: (projectId: number): Promise<ApiResponse<ApiScenario[]>> =>
    api.get(`/api-test/projects/${projectId}/scenarios`),
  createScenario: (projectId: number, scenario: Partial<ApiScenario>): Promise<ApiResponse<ApiScenario>> =>
    api.post(`/api-test/projects/${projectId}/scenarios`, scenario),
  updateScenario: (scenarioId: number, scenario: Partial<ApiScenario>): Promise<ApiResponse<ApiScenario>> =>
    api.put(`/api-test/scenarios/${scenarioId}`, scenario),
  deleteScenario: (scenarioId: number): Promise<ApiResponse> => api.delete(`/api-test/scenarios/${scenarioId}`),
  getScenario: (scenarioId: number): Promise<ApiResponse<ApiScenario>> =>
    api.get(`/api-test/scenarios/${scenarioId}`),
  getScenarioResults: (scenarioId: number, limit?: number): Promise<ApiResponse<ApiScenarioResult[]>> =>
    api.get(`/api-test/scenarios/${scenarioId}/results${limit ? `?limit=${limit}` : ''}`),
  runScenario: (scenarioId: number): Promise<ApiResponse<ApiScenarioResult>> => api.post(`/api-test/scenarios/${scenarioId}/run`),
  runScenarios: (projectId: number, payload: ApiScenarioBatchRunRequest): Promise<ApiResponse<ApiScenarioBatchRunResult>> =>
    api.post(`/api-test/projects/${projectId}/scenarios/run-batch`, payload),
  matchEndpoint: (data: { requirement: string; project_id?: number }): Promise<ApiResponse<{ matches: any[]; total_matches: number }>> =>
    api.post("/api-test/match-endpoint", data),
  inferProjectDependencies: (projectId: number): Promise<ApiResponse<any>> =>
    api.post(`/api-test/projects/${projectId}/infer-dependencies`),
  inferScenarioDependencies: (scenarioId: number): Promise<ApiResponse<any>> =>
    api.post(`/api-test/scenarios/${scenarioId}/infer-dependencies`),
};

// 资源导航 API（接口文档 / 快捷工具 等共享书签）
export const navLinkApi = {
  // 获取分类 + 链接树（读，全员可用）
  getTree: (): Promise<ApiResponse<NavTreeNode[]>> => api.get('/nav-links'),

  // 分类管理（写，需 CONFIG_MANAGE）
  createCategory: (data: { name: string; icon?: string | null; sort_order?: number }): Promise<ApiResponse<NavCategory>> =>
    api.post('/nav-links/categories', data),
  updateCategory: (id: number, data: { name: string; icon?: string | null; sort_order?: number }): Promise<ApiResponse<NavCategory>> =>
    api.put(`/nav-links/categories/${id}`, data),
  deleteCategory: (id: number): Promise<ApiResponse> => api.delete(`/nav-links/categories/${id}`),

  // 链接管理（写，需 CONFIG_MANAGE）
  createLink: (data: {
    category_id: number;
    title: string;
    url: string;
    description?: string | null;
    icon?: string | null;
    open_type?: string;
    sort_order?: number;
  }): Promise<ApiResponse<NavLink>> => api.post('/nav-links/links', data),
  updateLink: (id: number, data: {
    category_id: number;
    title: string;
    url: string;
    description?: string | null;
    icon?: string | null;
    open_type?: string;
    sort_order?: number;
  }): Promise<ApiResponse<NavLink>> => api.put(`/nav-links/links/${id}`, data),
  deleteLink: (id: number): Promise<ApiResponse> => api.delete(`/nav-links/links/${id}`),
};
