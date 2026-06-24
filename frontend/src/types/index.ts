// 会话类型
export interface Session {
  id: number;
  name: string;
  user_id?: string;
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
  created_at: string;
  status?: TestCaseStatus; // 测试用例状态：NOT_RUN（未运行）、PASSED（通过）、FAILED（未通过）
  bug_id?: number;
  session_id: number;
  module_id?: number | null;
  user_id?: string;
  api_endpoint_id?: number | null;
  api_project_id?: number | null;
  steps: (string | Record<string, any>)[];
  expected_results: (string | Record<string, any>)[];
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
  api_config?: {
    headers?: Array<{ key: string; value: string }>;
    environment_id?: number;
  } | null;
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
  user_id?: string;
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
  user_id?: string;
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
  parameters: Array<{ key: string; value: string; type?: 'text' | 'file'; fileId?: string; fileName?: string }>;
  body?: string;
  post_extractions?: Array<{ variable: string; jsonpath: string }>;
  user_id?: string;
  created_at: string;
  updated_at: string;
}

// 全局参数（环境）类型
export interface GlobalParameter {
  id: number;
  name: string;
  parameters: Array<{ key: string; value: string }>;
  is_default: boolean;
  user_id?: string;
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
  file_params?: Array<{ key: string; fileId: string; fileName: string }>; // 文件参数列表
  environment_id: number | null;
}

// 代理响应类型
export interface ProxyResponse<T = ApiResponse>{
  status_code: number;
  headers: Record<string, string>;
  data?: T;
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
  parameters: Array<{ key: string; value: string; type?: 'text' | 'file'; fileId?: string; fileName?: string }>;
  enabled: boolean;
  last_run_at: string | null;
  last_run_result: string | null;
  user_id?: string;
  created_at: string;
  updated_at: string;
  request_names?: Array<{ id: number; name: string }>;
}

// MCP 服务器配置类型（服务端持久化）
export interface McpServer {
  id: number;
  name: string;
  type: string;         // http | stdio
  enabled: boolean;
  url?: string;
  command?: string;
  args?: string[];
  timeout?: number;
  env?: Array<{ key: string; value: string }>;
  user_id?: string;
  created_at: string;
  updated_at: string;
}

// Skill 类型
export interface Skill {
  name: string;
  display_name: string;
  description: string;
  license: string;
  body: string;
  body_preview: string;
}

// Mock 配置类型
export interface MockConfig {
  id: number;
  name: string;
  method: string;
  url_path: string;
  status_code: number;
  response_headers: Array<{ key: string; value: string }>;
  response_body?: string;
  enabled: boolean;
  environment_id: number | null;
  response_count: number;
  page_size?: number;
  json_path?: string;
  user_id?: string;
  created_at: string;
  updated_at: string;
}

export interface ApiProject {
  id: number;
  name: string;
  base_url: string;
  headers: Array<{ key: string; value: string }>;
  environment_id?: number | null;
  source_type: string;
  source_url?: string | null;
  raw_spec?: string;
  user_id?: string;
  created_at: string;
  updated_at: string;
}

export interface ApiEndpoint {
  id: number;
  project_id: number;
  name: string;
  method: string;
  path: string;
  url?: string | null;
  tags: string[];
  headers: Array<{ key: string; value: string; required?: boolean; schema?: Record<string, any> }>;
  parameters: Array<{ key: string; value: string; in?: 'query' | 'path' | 'header'; required?: boolean; schema?: Record<string, any> }>;
  body?: string;
  request_schema?: Record<string, any>;
  response_schema?: Record<string, any>;
  environment_id?: number | null;
  pre_actions: Array<{ type?: string; key?: string; variable?: string; value?: string }>;
  post_actions: Array<{ type?: string; key?: string; variable?: string; jsonpath?: string }>;
  assertions: Array<{ type: string; value?: string | number; min?: number; max?: number; jsonpath?: string }>;
  user_id?: string;
  created_at: string;
  updated_at: string;
}

export interface ApiEndpointRunPayload extends Partial<ApiEndpoint> {
  base_url?: string | null;
  project_headers?: ApiProject['headers'];
  environment_id?: number | null;
  variables?: Array<{ key: string; value: string }>;
}

export interface ApiScenarioStep {
  endpoint_id: number;
  name?: string;
  enabled?: boolean;
  continue_on_failure?: boolean;
  method?: string;
  path?: string;
  url?: string;
  headers?: ApiEndpoint['headers'];
  parameters?: ApiEndpoint['parameters'];
  body?: string;
  pre_actions?: ApiEndpoint['pre_actions'];
  post_actions?: ApiEndpoint['post_actions'];
  assertions?: ApiEndpoint['assertions'];
}

export interface ApiScenario {
  id: number;
  project_id: number;
  name: string;
  description: string;
  environment_id?: number | null;
  variables: Array<{ key: string; value: string }>;
  steps: ApiScenarioStep[];
  user_id?: string;
  created_at: string;
  updated_at: string;
}

export interface ApiScenarioResult {
  id: number;
  scenario_id: number;
  project_id: number;
  scenario_name: string;
  passed: boolean;
  result: Record<string, any>;
  user_id?: string;
  created_at: string;
  updated_at: string;
}

export interface ApiScenarioBatchRunRequest {
  scenario_ids?: number[];
  run_all: boolean;
}

export interface ApiScenarioBatchRunItem {
  scenario_id: number;
  scenario_name: string;
  record_id: number;
  passed: boolean;
  created_at: string;
  result: Record<string, any>;
}

export interface ApiScenarioBatchRunResult {
  total: number;
  passed: number;
  failed: number;
  results: ApiScenarioBatchRunItem[];
}

export interface ApiImportResult {
  project: ApiProject;
  endpoints: ApiEndpoint[];
}

export interface ApiSyncResult {
  project: ApiProject;
  endpoints: ApiEndpoint[];
  created: number;
  updated: number;
  marked_removed: number;
}
