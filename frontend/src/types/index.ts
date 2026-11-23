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
  /** UI 绑定计划(NL 步骤 → PageObject 元素),由 /ui-execution/compile 生成、前端可修正 */
  ui_binding?: UIBindingPlan | null;
}

// ---------------------------------------------------------------------------
// UI 步骤绑定(NL → PageObject)
// ---------------------------------------------------------------------------

export type UIBindLevel = 'L1' | 'L2' | 'L3';

export interface UIBoundStep {
  step_no: number;
  nl?: string;
  action: string;
  element_key?: string | null;
  value?: string | null;
  confidence?: number;
  level: UIBindLevel;
  reason?: string;
}

export interface UIBoundAssert {
  index: number;
  nl?: string;
  action: string;
  element_key?: string | null;
  value?: string | null;
  confidence?: number;
  level: UIBindLevel;
  reason?: string;
}

export interface UIBindingPlan {
  page_object_id?: number | null;
  steps: UIBoundStep[];
  asserts: UIBoundAssert[];
  compile_status?: string;
  site_origin?: string;
  page_path?: string;
  message?: string;
}

export interface UIElementOption {
  key: string;
  name?: string;
  type?: string;
}

export interface UICompileResult {
  page_object_id?: number | null;
  page_object_name?: string;
  elements: UIElementOption[];
  steps: UIBoundStep[];
  asserts: UIBoundAssert[];
  unmatched_elements: string[];
  compile_status: 'ok' | 'no_page_object' | 'llm_failed' | string;
  site_origin?: string;
  page_path?: string;
  message?: string;
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

// MQTT 发布配置（IoT 数据推送平台）
export interface MqttConfig {
  broker: string;
  /** Broker 端口（留空则使用地址中的端口，或按协议默认 1883/8883/8083/8084） */
  port?: string;
  topic: string;
  payload: string;
  qos: 0 | 1 | 2;
  retain: boolean;
  client_id?: string;
  username?: string;
  password?: string;
  tls?: boolean;
}

// MQTT 发布请求体（POST /api/mqtt/publish）
export interface MqttPublishRequest {
  broker: string;
  topic: string;
  payload: string;
  qos: 0 | 1 | 2;
  retain: boolean;
  client_id?: string;
  username?: string;
  password?: string;
  tls?: boolean;
  environment_id?: number | null;
  /** 本地参数（与环境参数合并，优先级更高），后端按 [{key,value}] 解析 */
  parameters?: Array<{ key: string; value: string }>;
}

// MQTT 发布结果
export interface MqttPublishResult {
  success: boolean;
  broker: string;
  topic: string;
  qos: number;
  retain: boolean;
  payload: string;
  duration_ms: number;
  error?: string | null;
}

// MQTT 连接测试结果（POST /api/mqtt/test-connection）
export interface MqttTestConnectionResult {
  connected: boolean;
  message: string;
  duration_ms: number;
}

// WebSocket 连接配置（IoT 数据推送平台）
export interface WsConfig {
  url: string;
  /** 额外请求头（如 Authorization / Cookie） */
  headers: Array<{ key: string; value: string }>;
  /** 最多接收消息条数 */
  maxMessages: number;
  /** 空闲等待超时（秒） */
  timeoutSeconds: number;
  /** 子协议，多个用英文逗号分隔 */
  protocols?: string;
  /** 连接成功后先发送一条文本消息 */
  sendMessage?: string;
}

// WebSocket 单条消息
export interface WsMessage {
  seq: number;
  type: 'text' | 'binary';
  size: number;
  content: string;
  base64?: string;
  /** 相对连接开始的秒数 */
  ts: number;
}

// WebSocket 连接结果（POST /api/ws/connect）
export interface WsConnectResult {
  success: boolean;
  url: string;
  connected: boolean;
  messages: WsMessage[];
  received: number;
  duration_ms: number;
  closed_by: 'server' | 'client' | 'timeout' | 'max_messages' | 'error';
  error?: string | null;
}

// WebSocket 连通性测试结果（POST /api/ws/test-connection）
export interface WsTestConnectionResult {
  connected: boolean;
  message: string;
  duration_ms: number;
}

// 保存的请求类型
/**
 * 前置步骤：发请求取变量（request）或直接设置变量（set_variable）。
 * 产出的变量只在本次请求链路内生效，不写入环境参数。
 */
/**
 * 前置步骤里自定义请求的请求头 / 查询参数条目。
 */
export type PreStepKv = {
  key: string;
  value: string;
  /** 是否启用（Postman 风格勾选），false 时不参与发送 */
  enabled?: boolean;
  /** 备注说明，仅用于展示 */
  description?: string;
};

export type PreStep = {
  /**
   * set_variable —— 旧版直接设置变量（仅保留兼容展示，新步骤不再提供）；
   * request —— 先发请求再提取变量。参数统一在前置参数中设置，前置步骤只负责发请求。
   */
  type: 'set_variable' | 'request';
  /** 是否启用（关闭则跳过该步骤） */
  enabled?: boolean;
  /** 步骤名称（request 类型用于展示） */
  name?: string;
  /** request 步骤来源：custom 自己配置（默认）；saved 关联已有请求 */
  source?: 'custom' | 'saved';
  /** 关联的已保存请求 ID（source 为 saved 时有效，执行时后端按 id 现取最新配置） */
  saved_request_id?: number;
  /** 变量名（set_variable 为要设置的 key；request 为提取结果的 key） */
  key?: string;
  /** 变量值（set_variable 类型） */
  value?: string;
  /** 请求方法（request 类型） */
  method?: string;
  /** 请求地址（request 类型） */
  url?: string;
  /** 请求头（request 类型，自己配置） */
  headers?: PreStepKv[];
  /** 查询参数（request 类型，自己配置） */
  params?: PreStepKv[];
  /** 请求体（request 类型，自己配置） */
  body?: any;
  /** 提取规则（request 类型）：从响应里按 JSONPath 取值 */
  extracts?: PreStepExtract[];
};

/**
 * 前置步骤的提取规则。
 *
 * - type=json：按 JSONPath 取普通值
 * - type=image_base64：取到的值当作图片（base64 串或 data:image URL），
 *   转成 base64 变量；compress 控制是否压缩转 JPEG，
 *   format 控制输出纯 base64 还是完整 data:image URL
 */
export type PreStepExtract = {
  key: string;
  jsonpath: string;
  type?: 'json' | 'image_base64';
  /** 图片 base64 是否压缩（仅 image_base64 有效，默认 true） */
  compress?: boolean;
  /** 输出格式：raw=纯 base64，data_url=完整 data:image URL（仅 image_base64 有效） */
  format?: 'raw' | 'data_url';
};

/**
 * 前置参数列表（批量执行）。
 *
 * - columns：列定义，每列一个变量名（先定义列，再逐行填值）
 * - rows：数据行，每行是与列一一对应的值列表
 * - on_error：失败策略，continue（继续，默认）/ stop（遇错即停）
 *
 * 每行跑一次主请求，行内变量只在该次循环内生效，不跨行泄漏。
 */
export interface BatchParamsColumn {
  /** 列名（变量名），主请求里用 {{列名}} 引用 */
  name: string;
  /**
   * 列类型：
   * - text：普通文本值
   * - number：数值列，校验为合法数字（非法值跳过该变量）
   * - boolean：布尔列，true/false/1/0/yes/no 归一化（非法值跳过该变量）
   * - json：JSON 列，校验并压缩为单行（非法值跳过该变量）
   * - image：旧版图片列（界面已不再提供，后端仍兼容）
   * - image_base64：该列值已是 base64 字符串，循环时按图片校验并规范化
   */
  type?: 'text' | 'number' | 'boolean' | 'json' | 'image' | 'image_base64';
  /** 图片列是否压缩并转 JPEG（仅 image / image_base64 有效，默认 true） */
  compress?: boolean;
  /** 图片列输出格式：raw=纯 base64，data_url=完整 data:image URL（默认 raw） */
  format?: 'raw' | 'data_url';
}

export interface BatchParams {
  columns: Array<string | BatchParamsColumn>;
  rows: string[][];
  on_error?: 'continue' | 'stop';
}

export interface SavedRequest {
  id: number;
  name: string;
  method: string;
  url: string;
  headers: Array<{ key: string; value: string }>;
  parameters: Array<{ key: string; value: string; type?: 'text' | 'file'; fileId?: string; fileName?: string }>;
  body?: string;
  post_extractions?: Array<{ variable: string; jsonpath: string }>;
  /** 前置步骤：先执行再发主请求。变量只在本次链路内生效，不写环境 */
  pre_steps?: PreStep[];
  /** 前置参数列表：配了列+行就按行循环执行主请求 */
  batch_params?: BatchParams;
  /** 协议：http（默认）| mqtt | websocket */
  protocol?: 'http' | 'mqtt' | 'websocket';
  /** MQTT 配置（protocol 为 mqtt 时有效） */
  mqtt_config?: MqttConfig | null;
  /** WebSocket 配置（protocol 为 websocket 时有效） */
  ws_config?: WsConfig | null;
  folder_id?: number | null;
  /** 所属文件夹内的排序序号（拖拽排序，越小越靠前；为空表示未拖拽过） */
  sort_order?: number | null;
  user_id?: string;
  created_at: string;
  updated_at: string;
}

// 保存的请求文件夹类型（后端返回树结构，children 为嵌套子文件夹）
export interface SavedRequestFolder {
  id: number;
  name: string;
  parent_id?: number | null;
  /** 同级文件夹的排序序号（拖拽排序，越小越靠前；为空表示未拖拽过） */
  sort_order?: number | null;
  user_id?: string;
  children: SavedRequestFolder[];
  created_at: string;
  updated_at: string;
}

// 文件上传结果（后端代理上传返回）
export interface UploadedFileInfo {
  fileName: string;
  fileId: string;
  fileSize: number;
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

/**
 * 侧栏拖拽布局：一个父级（parent_id 为 null 表示根层）下全部子项的完整顺序。
 * 「拖进别的文件夹」与「同级换位置」共用这一个结构。
 */
export interface SavedRequestLayoutGroup {
  parent_id: number | null;
  folder_ids: number[];
  request_ids: number[];
}

// UI 自动化执行记录(browser-use Agent / hybrid)
export interface UIExecutionTraceStep {
  step_no: number;
  action: string;
  result?: string;
  url?: string;
  /** hybrid 执行等级:L1 确定性 / L2 校验后执行 / L3 步进 Agent */
  level?: UIBindLevel | string | null;
  element_key?: string | null;
  healed?: boolean | null;
  error?: string | null;
  nl?: string;
}

export interface UIExecutionRecord {
  id: number;
  testcase_id: number;
  user_id?: string;
  target_url: string;
  status: 'RUNNING' | 'PASSED' | 'FAILED' | 'ERROR' | 'STOPPED';
  steps_trace: UIExecutionTraceStep[];
  agent_summary: string;
  error?: string | null;
  finished_at?: string | null;
  execution_mode?: 'agent' | 'hybrid' | string;
  page_object_id?: number | null;
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
  /** true=有效, false=无效;缺省视为有效。无效元素仍参与验证/导出,但引用它的执行步骤会被拦截 */
  enabled?: boolean;
  fingerprint?: Record<string, string>;
  locators: PageLocator[];
  /** 探针来源触发词(新增/编辑/删除等),前端据此分组展示;基线元素为空 */
  probeTrigger?: string;
  /** 嵌套子元素(触发按钮 → 弹窗 children);基线元素为空/[] */
  children?: PageElement[];
  /** 祖先触发路径(根→叶,不含自身);基线为 [] */
  probePath?: string[];
  /** 虚拟容器(触发按钮未采到时占位,type=trigger_group) */
  synthetic?: boolean;
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
  /** 前端当前选中的环境(解析 {{var}});缺省时后端回退默认环境 */
  environment_id?: number | null;
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
  /** 前置步骤：先执行再发主请求，产出的变量仅本次链路生效 */
  pre_steps?: PreStep[];
  /** 前置参数列表：按行循环执行主请求 */
  batch_params?: BatchParams;
}

// 代理响应类型
export interface ProxyResponse<T = ApiResponse>{
  status_code: number;
  headers: Record<string, string>;
  data?: T;
  /** 批量执行结果（配了前置参数列表时返回） */
  batch?: {
    total: number;
    executed: number;
    passed: number;
    failed: number;
    stopped: boolean;
    results: Array<{
      index: number;
      variables: Record<string, string>;
      status_code: number;
      ok: boolean;
      data?: any;
      error?: string;
    }>;
  };
  /** 前置步骤执行结果 */
  pre_steps?: Array<Record<string, any>>;
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
  /** 任务参数：text=文本变量，file=文件上传(fileId)，image_base64=图片 base64 变量 */
  parameters: Array<{ key: string; value: string; type?: 'text' | 'file' | 'image_base64'; fileId?: string; fileName?: string }>;
  /** 所属分组ID（null = 未分组） */
  group_id?: number | null;
  enabled: boolean;
  last_run_at: string | null;
  last_run_result: string | null;
  user_id?: string;
  created_at: string;
  updated_at: string;
  request_names?: Array<{ id: number; name: string }>;
}

// 定时任务分组
export interface TaskGroup {
  id: number;
  name: string;
  parent_id: number | null;
  /** 分组绑定的执行环境（组内任务默认继承） */
  environment_id: number | null;
  /** 分组级环境变量，组内任务共享 */
  variables: Array<{ key: string; value: string }>;
  /** 该分组下的直属任务数 */
  task_count?: number;
  children: TaskGroup[];
  created_at: string;
  updated_at: string;
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
  // 代理要拦截的真实接口地址（如 http://192.168.13.178:18896/contract/update），命中即返回本 Mock
  proxy_urls?: string[];
  // 定向生效主机（支持 *.example.com），留空=规则覆盖的任意主机
  hosts?: string[];
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
  // 该规则覆盖流量的服务前缀（如 /contract），请求去掉它后按平台相对路径匹配
  service_prefix: string;
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
  platform_configs?: number;
  local_ip?: string;
}

// 接口平台 Mock 在代理侧的覆盖情况
export interface PlatformMockItem {
  id: number;
  name: string;
  method: string;
  url_path: string;
  hosts: string[];
  proxy_urls: string[];
  enabled: boolean;
  covered: boolean;
  bound: boolean;
  covering_rules: Array<{ id: number; name: string; host_pattern: string }>;
}

export interface PlatformMockCoverage {
  items: PlatformMockItem[];
  enabled_configs: number;
  covered_configs: number;
  rules_count: number;
}

// 代理命中预览请求
export interface MockPreviewRequest {
  host: string;
  method: string;
  path: string;
  query?: Record<string, string>;
  body?: string;
  headers?: Record<string, string>;
}

// 代理命中预览结果
export interface MockPreviewResult {
  intercepted: boolean;
  matched: boolean;
  source?: 'config' | 'project' | null;
  rule?: { id: number; name: string; service_prefix?: string } | null;
  candidates?: string[];
  config_id?: number | null;
  config_name?: string;
  status_code?: number;
  body_preview?: string;
  matched_url?: string;
  error?: string;
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
  /** 前置步骤执行记录（每个步骤的请求/响应摘要） */
  pre_steps?: Array<Record<string, any>>;
  duration_ms: number;
  environment_id?: number | null;
  /** 推送协议：http（历史记录缺省按 http 处理）| mqtt | websocket */
  protocol?: 'http' | 'mqtt' | 'websocket';
  mqtt_broker?: string | null;
  mqtt_topic?: string | null;
  mqtt_qos?: number | null;
  mqtt_retain?: boolean | null;
  mqtt_client_id?: string | null;
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

// ===== 需求管理（飞书文档 → 结构化需求 → 测试用例联动） =====

export interface RequirementItem {
  key: string;
  section: string;
  title: string;
  content: string;
  order_index: number;
  status: string;
}

export interface RequirementDiffEntry {
  type: 'added' | 'modified' | 'removed';
  key: string;
  section: string;
  before: string;
  after: string;
}

export interface RequirementDoc {
  id: number;
  title: string;
  url: string;
  feishu_token: string;
  content_snapshot: string;
  structured: RequirementItem[];
  session_id: number | null;
  version: number;
  module_id: number | null;
  source_type: 'feishu' | 'generation' | 'manual';
  created_at: string | null;
  updated_at: string | null;
}

export interface RequirementRevision {
  id: number;
  version: number;
  source: string;
  summary: string;
  differences: RequirementDiffEntry[];
  affected_case_ids: number[];
  content_snapshot: string;
  created_at: string | null;
}

