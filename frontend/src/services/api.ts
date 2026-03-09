import axios from 'axios';
import { Session, TestCase, ApiResponse, TestCaseResponse, Module, UpdateSessionRequest, HistoryPrompt } from '../types';

// 创建axios实例
const api = axios.create({
  baseURL: '/api',
  timeout: 300000, // 增加超时时间到300秒
  // 移除默认的Content-Type，让axios根据请求数据自动设置
});

// 请求拦截器
api.interceptors.request.use(
  (config) => {
    // 可以在这里添加认证信息
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// 响应拦截器
api.interceptors.response.use(
  (response) => {
    return response.data;
  },
  (error) => {
    console.error('API请求错误:', error);
    return Promise.reject(error);
  }
);

// 会话管理API
export const sessionApi = {
  // 获取所有会话
  getSessions: (): Promise<ApiResponse<Session[]>> => api.get('/sessions/'),

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
    ollama_url?: string;
    ollama_model?: string;
  }, imageBase64?: string | null,
    moduleId?: number | null): Promise<ApiResponse<TestCase[]>> => {
    // 创建FormData
    const formData = new FormData();

    // 始终添加requirement，即使为空字符串，与后端预期一致
    formData.append('requirement', requirement);

    // 添加模块ID
    if (moduleId !== null) {
      formData.append('module_id', moduleId?.toString() ?? '');
    }

    // 添加模型配置
    if (modelConfig) {
      formData.append('model_type', modelConfig.model_type);
      if (modelConfig.api_key) {
        formData.append('api_key', modelConfig.api_key);
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
    api.post(`/testcases/move`, { testcase_ids: testcaseIds, session_id: sessionId, module_id: moduleId })
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
