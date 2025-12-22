import axios from 'axios';
import { Session, TestCase, ApiResponse, TestCaseResponse } from '../types';

// 创建axios实例
const api = axios.create({
  baseURL: '/api',
  timeout: 300000, // 增加超时时间到300秒
  headers: {
    'Content-Type': 'application/json'
  }
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
  getSessions: () => api.get<ApiResponse<Session[]>>('/sessions/'),
  
  // 创建会话
  createSession: (name: string) => api.post<ApiResponse<Session>>('/sessions/', { name }),
  
  // 删除会话
  deleteSession: (id: number) => api.delete<ApiResponse>(`/sessions/${id}`)
};

// 测试用例管理API
export const testcaseApi = {
  // 获取会话的测试用例
  getTestcases: (sessionId: number, filters?: { case_name?: string; status?: string }) => {
    const params = new URLSearchParams();
    if (filters?.case_name) params.append('case_name', filters.case_name);
    if (filters?.status) params.append('status', filters.status);
    return api.get<ApiResponse<TestCaseResponse>>(`/testcases/${sessionId}/testcases?${params.toString()}`);
  },
  
  // 生成测试用例
  generateTestcases: (sessionId: number, requirement: string, modelConfig?: {
    model_type: 'api' | 'ollama';
    api_key?: string;
    ollama_url?: string;
    ollama_model?: string;
  }) => {
    const data: any = { requirement };
    if (modelConfig) {
      data.model_type = modelConfig.model_type;
      if (modelConfig.api_key) data.api_key = modelConfig.api_key;
      if (modelConfig.ollama_url) data.ollama_url = modelConfig.ollama_url;
      if (modelConfig.ollama_model) data.ollama_model = modelConfig.ollama_model;
    }
    return api.post<ApiResponse<TestCase[]>>(`/testcases/${sessionId}/testcases`, data);
  },
  
  // 更新测试用例
  updateTestcase: (sessionId: number, id: number, testcase: Partial<TestCase>) => 
    api.put<ApiResponse>(`/testcases/${sessionId}/testcases/${id}`, testcase),
  
  // 删除测试用例
  deleteTestcase: (sessionId: number, id: number) => api.delete<ApiResponse>(`/testcases/${sessionId}/testcases/${id}`)
};

// 健康检查
export const healthApi = {
  checkHealth: () => api.get('/health')
};
