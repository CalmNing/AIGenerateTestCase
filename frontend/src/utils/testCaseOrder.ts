import type { TestCase } from '../types';

/**
 * 与后端 GET /testcases/{session_id}/testcases 的排序规则保持一致：
 * 按创建时间倒序（最新创建的排在最前），创建时间相同时按 id 倒序。
 * case_level 只作为展示字段，不参与任何排序。
 */

const createdAtMs = (tc: TestCase): number => {
  const ms = Date.parse(tc?.created_at ?? '');
  return Number.isNaN(ms) ? 0 : ms;
};

/** 升序比较（早 → 晚），供 antd sorter 使用；defaultSortOrder='descend' 时即为最新在前 */
export const compareTestcaseByCreatedAt = (a: TestCase, b: TestCase): number => {
  const diff = createdAtMs(a) - createdAtMs(b);
  return diff !== 0 ? diff : (a.id ?? 0) - (b.id ?? 0);
};

/** 列表数据兜底排序：创建时间倒序，同时间按 id 倒序 */
export const sortTestcasesByCreatedAtDesc = (list: TestCase[]): TestCase[] =>
  [...list].sort((a, b) => -compareTestcaseByCreatedAt(a, b));
