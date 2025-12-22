// 会话类型
export interface Session {
  id: number;
  name: string;
  created_at: string;
  updated_at: string;
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
  status?: 'pending' | 'completed'; // 测试用例状态：pending（待执行）、completed（已执行）
}

// 测试用例类型
export interface TestCaseResponse {
  items: TestCase[];
  completed: number;
  pending: number;
  totalNumber: number;

}

// API响应类型
export interface ApiResponse<T = any> {
  code: number;
  message: string;
  data?: T;
}
