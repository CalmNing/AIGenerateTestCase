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
  preset_conditions: (string | Record<string, any>)[];
  created_at: string;
  updated_at?: string;
  status?: TestCaseStatus; // 测试用例状态：NOT_RUN（未运行）、PASSED（通过）、FAILED（未通过）
  bug_id?: number;
  session_id: number;
  module_id?: number | null;
  user_id?: string;
  api_endpoint_id?: number | string | null;
  api_project_id?: number | null;
  assertions?: Record<string, any>[] | null;
  scenario_id?: number | null;
  scenario_steps?: { method: string; path: string; name: string }[];
  test_point_id?: number | null; // 关联的测试点 ID（4 阶段 pipeline 生成时回填）
  // 准确性保证字段
  consistency_report?: string | null;
  confidence_score?: number | null;
  confidence_level?: 'high' | 'medium' | 'low' | null;
  validation_status?: 'pending' | 'passed' | 'failed' | 'error' | null;
  validation_detail?: string | null;
  validation_at?: string | null;
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

export interface TestCaseExecutionLog {
  id: number;
  testcase_id: number;
  session_id: number;
  case_name: string;
  passed: boolean;
  status: string;
  result: Record<string, any>;
  user_id?: string;
  created_at: string;
  updated_at: string;
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
  pagination?: { total: number; page: number; page_size: number };
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
  folder_id?: number | null;
  user_id?: string;
  created_at: string;
  updated_at: string;
}

// 保存的请求文件夹类型（后端返回树结构，children 为嵌套子文件夹）
export interface SavedRequestFolder {
  id: number;
  name: string;
  parent_id?: number | null;
  user_id?: string;
  children: SavedRequestFolder[];
  created_at: string;
  updated_at: string;
}

// 全局参数（环境）类型
export interface GlobalParameter {
  id: number;
  name: string;
  parameters: Array<{ key: string; value: string; description?: string }>;
  is_default: boolean;
  user_id?: string;
  created_at: string;
  updated_at: string;
}

// UI 自动化执行记录(browser-use Agent)
export interface UIExecutionRecord {
  id: number;
  testcase_id: number;
  user_id?: string;
  target_url: string;
  status: 'RUNNING' | 'PASSED' | 'FAILED' | 'ERROR' | 'STOPPED';
  steps_trace: Array<{ step_no: number; action: string; result?: string; url?: string }>;
  agent_summary: string;
  error?: string | null;
  finished_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface UIExecutionTaskStatus {
  task_id: string;
  status: 'running' | 'done';
  phase: 'queued' | 'launching_browser' | 'executing' | 'judging' | 'persisting' | 'done';
  progress: number;
  message: string;
  record_id: number;
  result_status: 'PASSED' | 'FAILED' | 'ERROR' | 'STOPPED' | null;
  error: string | null;
  created_at: string;
}

// UI 自动化工具:页面对象与元素
export interface PageLocator {
  strategy: 'testid' | 'role' | 'label' | 'aria-label' | 'placeholder' | 'name' | 'id' | 'text' | 'css' | 'xpath';
  value: string;
  stability: number;
}

export interface PageElement {
  key: string;
  name: string;
  type: string;
  description?: string;
  enabled?: boolean;
  fingerprint?: Record<string, string>;
  locators: PageLocator[];
  /** 探针来源触发词(新增/编辑/删除等),前端据此分组展示;基线元素为空 */
  probeTrigger?: string;
  heal_count?: number;
  last_healed_at?: string | null;
}

export interface PageObject {
  id: number;
  name: string;
  site_origin: string;
  page_path: string;
  url: string;
  description: string;
  elements: PageElement[];
  /** 执行(自愈)步骤(持久化):登录页对象据此完成登录,执行时无需每次重填 */
  steps: Array<Record<string, any>>;
  /** 作为登录页对象:其他页面对象可关联它复用登录态,并调用其步骤完成登录 */
  is_login_page: boolean;
  /** 关联的登录页对象ID:抽取/验证/执行前确保其登录态可用(没有就先执行它的步骤登录) */
  login_page_object_id?: number | null;
  session_status: 'NONE' | 'READY' | 'FAILED';
  session_cookie_count: number;
  session_origin_count: number;
  session_path?: string;
  session_updated_at?: string | null;
  last_login_at?: string | null;
  last_login_error?: string | null;
  user_id?: string;
  created_at: string;
  updated_at: string;
}

/** 页面对象登录态视图(接口返回,不含 cookie/token 明文) */
export interface PageObjectSession {
  page_object_id: number;
  is_login_page: boolean;
  login_page_object_id?: number | null;
  session_path: string;
  session_status: 'NONE' | 'READY' | 'FAILED';
  session_ready: boolean;
  session_cookie_count: number;
  session_origin_count: number;
  session_updated_at?: string | null;
  last_login_at?: string | null;
  last_login_error?: string | null;
}

export interface UIAutomationRun {
  id: number;
  page_object_id?: number | null;
  user_id?: string;
  run_type: 'extract' | 'validate' | 'execute';
  target_url: string;
  status: 'RUNNING' | 'PASSED' | 'FAILED' | 'ERROR' | 'STOPPED';
  steps: Array<Record<string, any>>;
  steps_trace: Array<Record<string, any>>;
  heal_events: Array<Record<string, any>>;
  result: Record<string, any>;
  summary: string;
  error?: string | null;
  finished_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface UIAutomationTaskStatus {
  task_id: string;
  status: 'running' | 'done';
  phase: 'queued' | 'launching_browser' | 'logging_in' | 'extracting' | 'naming' | 'validating' | 'executing' | 'persisting' | 'done';
  progress: number;
  message: string;
  record_id: number;
  run_type: 'extract' | 'validate' | 'execute' | 'login';
  result_status: 'PASSED' | 'FAILED' | 'ERROR' | 'STOPPED' | null;
  error: string | null;
  page_object_id: number | null;
  /** 抽取时发现「界面上可见却没被采集」的元素(诊断提示,最多 20 条) */
  uncollected?: Array<{
    tag: string;
    class?: string;
    text?: string;
    name?: string;
    cssPath?: string;
    frame?: string;
  }>;
  uncollected_count?: number;
  created_at: string;
}

export interface UIAutomationLoginConfig {
  login_url?: string;
  login_method?: string;
  login_username?: string;
  login_password?: string;
  login_company?: string;
  /** 关联的登录页对象ID:有登录态直接复用,没有就先执行它的登录步骤再继续 */
  login_page_object_id?: number | null;
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

// 定时任务创建/更新载荷（不含服务端生成字段）
export type ScheduledTaskInput = Partial<Omit<ScheduledTask, 'id' | 'created_at' | 'updated_at' | 'user_id' | 'request_names'>>;

// MCP 工具信息（list-tools 返回的单个服务器条目）
export interface McpToolInfo {
  name: string;
  description?: string;
  tools?: Array<{ name: string; description?: string }>;
  error?: string;
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

// Mock 正向代理转发规则
export interface MockProxyRule {
  id: number;
  name: string;
  host_pattern: string;
  path_pattern: string;
  enabled: boolean;
  user_id?: string;
  created_at: string;
  updated_at: string;
}

// Mock 正向代理运行状态
export interface MockProxyStatus {
  running: boolean;
  port: number | null;
  rules_count: number;
  local_ip?: string;
}

// Mock 调用日志（含代理流量）
export interface MockLogItem {
  id: number;
  config_id: number | null;
  config_name: string;
  request_method: string;
  request_path: string;
  request_headers: Array<{ key: string; value: string }>;
  request_query_params?: string | null;
  request_body?: string | null;
  response_status_code: number;
  response_headers: Array<{ key: string; value: string }>;
  response_body?: string | null;
  matched: boolean;
  user_id?: string | null;
  created_at: string;
  updated_at: string;
}

// IoT 推送请求记录（原始请求 + 变量替换后的解析结果）
export interface PushLogItem {
  id: number;
  request_method: string;
  source_url: string;
  final_url: string;
  request_headers: Record<string, string>;
  final_headers: Record<string, string>;
  request_params: Record<string, string>;
  final_params: Record<string, string>;
  request_body?: string | null;
  final_body?: string | null;
  response_status: number;
  response_headers: Record<string, string>;
  response_body?: string | null;
  mocked: boolean;
  error?: string | null;
  duration_ms: number;
  environment_id?: number | null;
  user_id?: string | null;
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
  mock_enabled?: boolean;
  mock_prefix?: string;
  user_id?: string;
  created_at: string;
  updated_at: string;
}

export interface ApiEndpoint {
  id: number;
  project_id: number;
  name: string;
  description?: string;   // 接口业务描述（生成接口测试用例时使用）
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
  removed: number;
}

// ===== 资源导航（接口文档 / 快捷工具 等共享书签） =====
export interface NavCategory {
  id: number;
  name: string;
  icon?: string | null;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface NavLink {
  id: number;
  category_id: number;
  title: string;
  url: string;
  description?: string | null;
  icon?: string | null;
  open_type: string;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface NavTreeNode {
  category: NavCategory;
  links: NavLink[];
}
