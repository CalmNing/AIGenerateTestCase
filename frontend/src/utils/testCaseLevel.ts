export const TEST_CASE_LEVEL_OPTIONS: { label: string; value: number }[] = [
  { label: 'P0', value: 1 },
  { label: 'P1', value: 2 },
  { label: 'P2', value: 3 },
  { label: 'P3', value: 4 },
];

export const formatTestCaseLevel = (level?: number | null): string => {
  const normalized = level === 1 || level === 2 || level === 3 || level === 4 ? level : 4;
  return `P${normalized - 1}`;
};
