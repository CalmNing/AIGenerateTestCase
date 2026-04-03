// 会话类型
export interface Session {
  id: number;
  name: string;
  created_at: string;
  updated_at: string;
}

// 测试用例状态枚举
export enum TestCaseStatus {
  NOT_RUN = 'NOT_RUN', // 未运行
  PASSED = 'PASSED',   // 通过
  FAILED = 'FAILED',   // 未通过
}

// 测试用例类型
export interface TestCase {
  id: number;
  case_name: string;
  case_level: number;
  preset_conditions: string[];
  steps: string[];
  expected_results: string[];
  created_at: string;
  status?: TestCaseStatus; // 测试用例状态：NOT_RUN（未运行）、PASSED（通过）、FAILED（未通过）
  bug_id?: number;
  session_id: number;
  module_id?: number | null;
}

// 测试用例类型
export interface TestCaseResponse {
  items: TestCase[];
  passed: number;
  failed: number;
  not_run: number;
  totalNumber: number;
  totalBugs: number;

}

// 更新会话请求类型
export interface UpdateSessionRequest {
  name: string;
}

// API响应类型
export interface ApiResponse<T = any> {
  code: number;
  message: string;
  data?: T;
}

// 模块类型
export interface Module {
  id: number | null;
  module_name: string;
  session_id: number | null;
  parent_id: number | null;
  created_at: string;
  updated_at: string;
}

// 树形模块类型
export interface ModuleTree {
  id: number;
  module_name: string;
  session_id: number;
  parent_id: number | null;
  children: ModuleTree[];
}

// 刷选条件参数类型
export interface TestCaseFilters {
  case_name?: string;
  status?: TestCaseStatus;
  bug_id?: string;
  exist_bug?: boolean;
  module_id?: number | string;
}

// 历史提示词类型
export interface HistoryPrompt {
  id: number;
  content: string;
  module_id: number | null;
  session_id: number | null;
  created_at: string;
  updated_at: string;
}

// 保存的请求类型
export interface SavedRequest {
  id: number;
  name: string;
  method: string;
  url: string;
  headers: Array<{ key: string; value: string }>;
  parameters: Array<{ key: string; value: string }>;
  body?: string;
  post_extractions?: Array<{ variable: string; jsonpath: string }>;
  created_at: string;
  updated_at: string;
}

// 全局参数（环境）类型
export interface GlobalParameter {
  id: number;
  name: string;
  parameters: Array<{ key: string; value: string }>;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

// 代理请求类型
export interface ProxyRequest {
  url: string;
  method: string;
  headers: Record<string, string>;
  data?: any;
  params: Record<string, string>;
  environment_id: number | null;
}

// 提取变量请求类型
export interface ExtractVariablesRequest {
  environment_id: number;
  response_data: any;
  extractions: Array<{ variable: string; jsonpath: string }>;
}

// 定时任务类型
export interface ScheduledTask {
  id: number;
  name: string;
  schedule_type: string;
  interval_seconds: number;
  cron_expression: string | null;
  request_ids: number[];
  environment_id: number | null;
  enabled: boolean;
  last_run_at: string | null;
  last_run_result: string | null;
  created_at: string;
  updated_at: string;
  request_names?: Array<{ id: number; name: string }>;
}