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

// API响应类型
export interface ApiResponse<T = any> {
  code: number;
  message: string;
  data?: T;
}