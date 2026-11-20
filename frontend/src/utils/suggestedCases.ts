// AI 补充用例建议解析：把“### 建议：xxx”固定格式小节解析为结构化用例

export interface ParsedSuggestedCase {
  case_name: string;
  case_level: number;
  preset_conditions: string[];
  steps: string[];
  expected_results: string[];
  note?: string;
}

const stripListPrefix = (line: string) => line.replace(/^\s*(?:\d+[.、)]|[-*•])\s*/, '').trim();

const parseLevel = (raw: string): number => {
  const m = raw.match(/[1-4]/);
  return m ? Number(m[0]) : 4;
};
/**
 * 从 AI 回复文本中解析补充用例建议。
 * 识别以“### 建议：”开头的独立小节；字段行以“- 用例级别：/前置条件：/测试步骤：/预期结果：/关联说明：”开头，
 * 其后的编号/列表行为各条目内容。未匹配任何小节时返回空数组。
 */
export const parseSuggestedCases = (text: string): ParsedSuggestedCase[] => {
  if (!text) return [];
  const lines = text.split(/\r?\n/);
  const cases: ParsedSuggestedCase[] = [];
  let current: ParsedSuggestedCase | null = null;
  let field: 'preset' | 'steps' | 'expected' | null = null;

  const pushCurrent = () => {
    if (current && current.case_name.trim()) {
      cases.push(current);
    }
    current = null;
    field = null;
  };

  for (const rawLine of lines) {
    const line = rawLine.replace(/\s+$/, '');
    const headerMatch = line.match(/^#{1,6}\s*建议[:：]\s*(.+)$/);
    if (headerMatch) {
      pushCurrent();
      current = {
        case_name: headerMatch[1].trim(),
        case_level: 4,
        preset_conditions: [],
        steps: [],
        expected_results: [],
      };
      field = null;
      continue;
    }
    if (!current) continue;

    const bold = line.replace(/\*\*/g, '').trim();
    if (/^-\s*用例级别[:：]/.test(bold)) {
      current.case_level = parseLevel(bold);
      field = null;
      continue;
    }
    if (/^-\s*前置条件[:：]/.test(bold)) {
      field = 'preset';
      const inline = bold.replace(/^[-*]\s*前置条件[:：]\s*/, '').trim();
      if (inline && inline !== '无') current.preset_conditions.push(inline);
      continue;
    }
    if (/^-\s*测试步骤[:：]/.test(bold)) {
      field = 'steps';
      continue;
    }
    if (/^-\s*预期结果[:：]/.test(bold)) {
      field = 'expected';
      continue;
    }
    if (/^-\s*关联说明[:：]/.test(bold)) {
      current.note = bold.replace(/^[-*]\s*关联说明[:：]\s*/, '').trim();
      field = null;
      continue;
    }
    const isListItem = /^\s*(?:\d+[.、)]|[-*•])\s*/.test(rawLine);
    const isHeading = bold.startsWith('#');
    const isPlainBullet = bold.startsWith('-') && !isListItem;
    if (isHeading || isPlainBullet) {
      field = null;
      continue;
    }
    if (isListItem && field) {
      const value = stripListPrefix(rawLine);
      if (!value || value === '无') continue;
      if (field === 'preset') current.preset_conditions.push(value);
      else if (field === 'steps') current.steps.push(value);
      else if (field === 'expected') current.expected_results.push(value);
    }
  }
  pushCurrent();
  return cases;
};
