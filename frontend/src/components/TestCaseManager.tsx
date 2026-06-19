import React from 'react';
import { Card, Typography, Button, Input, Select, notification, Switch, Empty } from 'antd';
import { DownloadOutlined, FileTextOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { Session, TestCase, TestCaseResponse, TestCaseStatus, Module, TestCaseFilters } from '../types';
import TestCaseTable from './TestCaseTable';
import * as XLSX from 'xlsx';

const { Text } = Typography;

interface TestCaseManagerProps {
  selectedSession: Session | null;
  modules: Module[];
  selectedModule: number | string;
  testcases: TestCase[];
  testcasesResponse: TestCaseResponse;
  filters?: TestCaseFilters;
  onFiltersChange: (filters: TestCaseFilters) => void;
  onLoadTestcases: (sessionId: number | undefined, filters?: TestCaseFilters) => void;
  onView: (testcase: TestCase) => void;
  onEdit: (testcase: TestCase) => void;
  onComplete: (testcase: TestCase) => void;
  onDelete: (id: number) => void;
  onBatchDelete: (ids: number[]) => void;
  onBatchMove: (ids: number[]) => void;
  onAdd: () => void;
  onApiExecute?: (testcase: TestCase) => void;
  onMove: (testcase: TestCase) => void;
}

const statItems = (resp: TestCaseResponse, filters?: TestCaseFilters) => [
  { key: 'all', label: '全部', value: resp.totalNumber, color: 'var(--color-primary)', bg: 'var(--color-primary-bg)', active: !filters?.status && !filters?.exist_bug, status: undefined, exist_bug: false },
  { key: 'passed', label: '已通过', value: resp.passed, color: 'var(--color-success)', bg: 'var(--color-success-bg)', active: filters?.status === TestCaseStatus.PASSED, status: TestCaseStatus.PASSED, exist_bug: false },
  { key: 'not_run', label: '未执行', value: resp.not_run, color: 'var(--color-warning)', bg: 'var(--color-warning-bg)', active: filters?.status === TestCaseStatus.NOT_RUN, status: TestCaseStatus.NOT_RUN, exist_bug: false },
  { key: 'failed', label: '未通过', value: resp.failed, color: 'var(--color-danger)', bg: 'var(--color-danger-bg)', active: filters?.status === TestCaseStatus.FAILED, status: TestCaseStatus.FAILED, exist_bug: false },
  { key: 'bug', label: 'Bug', value: resp.totalBugs, color: 'var(--color-danger)', bg: 'var(--color-danger-bg)', active: !!filters?.exist_bug, status: undefined, exist_bug: true },
];

const TestCaseManager: React.FC<TestCaseManagerProps> = ({
  selectedSession,
  modules,
  selectedModule,
  testcases,
  testcasesResponse,
  filters,
  onFiltersChange,
  onLoadTestcases,
  onView,
  onEdit,
  onComplete,
  onDelete,
  onBatchDelete,
  onAdd,
  onApiExecute,
  onMove,
  onBatchMove
}) => {
  const handleExportExcel = () => {
    if (testcases.length === 0) {
      notification.warning({ message: '导出失败', description: '当前没有可导出的测试用例', placement: 'topRight' });
      return;
    }
    try {
      const exportData = testcases.map(tc => ({
        '模块名称': tc.module_id ? modules.find(m => m.id === tc.module_id)?.module_name || '未知模块' : '未分配模块',
        '用例名称': tc.case_name,
        '用例级别': `P${tc.case_level}`,
        '前置条件': tc.preset_conditions.map((item, idx) => `${idx + 1}. ${item}`).join('\n'),
        '测试步骤': tc.steps.map((item, idx) => `${idx + 1}. ${item}`).join('\n'),
        '预期结果': tc.expected_results.map((item, idx) => `${idx + 1}. ${item}`).join('\n'),
        '状态': tc.status === TestCaseStatus.PASSED ? '已通过' : tc.status === TestCaseStatus.FAILED ? '未通过' : '未执行',
        'bug': tc.bug_id ? `http://zt.luban.fit/index.php?m=bug&f=view&bugID=${tc.bug_id}` : '',
      }));
      const ws = XLSX.utils.json_to_sheet(exportData);
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, '测试用例');
      XLSX.writeFile(wb, `测试用例_${new Date().toISOString().slice(0, 10)}.xlsx`);
      notification.success({ message: '导出成功', description: `已导出 ${testcases.length} 条测试用例`, placement: 'topRight' });
    } catch (error) {
      console.error('导出Excel失败:', error);
      notification.error({ message: '导出失败', description: '导出Excel时发生错误，请重试', placement: 'topRight' });
    }
  };

  const setFilter = (status: TestCaseStatus | undefined, exist_bug: boolean) => {
    onFiltersChange({
      case_name: '', bug_id: '', exist_bug, status,
      module_id: selectedModule === 0 ? undefined : Number(selectedModule),
    });
  };

  return (
    <Card variant="borderless" styles={{ body: { padding: 0 } }}>
      {!selectedSession ? (
        <Empty
          image={<FileTextOutlined style={{ fontSize: 48, color: 'var(--color-text-disabled)' }} />}
          description="请先从左侧选择一个会话"
          style={{ padding: '60px 0' }}
        />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {/* Stats bar */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 12, padding: '6px 8px', background: 'var(--color-bg)', borderRadius: 'var(--radius-lg)', border: '1px solid var(--color-border-light)' }}>
            {statItems(testcasesResponse, filters).map(item => (
              <div
                key={item.key}
                onClick={() => setFilter(item.status, item.exist_bug)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '6px 14px', borderRadius: 'var(--radius-md)',
                  cursor: 'pointer', transition: 'all 180ms ease',
                  background: item.active ? item.bg : 'transparent',
                  color: item.active ? item.color : 'var(--color-text-secondary)',
                  fontWeight: item.active ? 600 : 400,
                  fontSize: 13,
                }}
                onMouseEnter={(e) => { if (!item.active) e.currentTarget.style.background = 'rgba(0,0,0,0.03)'; }}
                onMouseLeave={(e) => { if (!item.active) e.currentTarget.style.background = 'transparent'; }}
              >
                <span>{item.label}</span>
                <span style={{ fontWeight: 700, color: item.active ? item.color : 'var(--color-text)' }}>{item.value}</span>
              </div>
            ))}
            <div style={{ flex: 1 }} />
            <Button size="small" type="primary" icon={<PlusOutlined />} onClick={onAdd}>新增</Button>
            <Button size="small" icon={<DownloadOutlined />} onClick={handleExportExcel}>导出</Button>
          </div>

          {/* Filter bar */}
          <div className="filter-bar">
            <label>名称</label>
            <Input
              size="small"
              placeholder="搜索用例名称"
              value={filters?.case_name ?? ''}
              onChange={(e) => onFiltersChange({ ...filters, case_name: e.target.value } as TestCaseFilters)}
              onPressEnter={() => selectedSession && onLoadTestcases(selectedSession.id, filters)}
              style={{ width: 160 }}
              allowClear
            />
            <label>状态</label>
            <Select
              size="small"
              placeholder="全部"
              allowClear
              value={filters?.status ?? undefined}
              onChange={(value) => onFiltersChange({ ...filters, status: value } as TestCaseFilters)}
              style={{ width: 100 }}
              options={[
                { label: '未执行', value: TestCaseStatus.NOT_RUN },
                { label: '已通过', value: TestCaseStatus.PASSED },
                { label: '未通过', value: TestCaseStatus.FAILED },
              ]}
            />
            <label>Bug ID</label>
            <Input
              size="small"
              placeholder="Bug ID"
              value={filters?.bug_id ?? ''}
              onChange={(e) => onFiltersChange({ ...filters, bug_id: e.target.value } as TestCaseFilters)}
              onPressEnter={() => selectedSession && onLoadTestcases(selectedSession.id, filters)}
              style={{ width: 120 }}
              allowClear
            />
            <label>仅Bug</label>
            <Switch
              size="small"
              checked={filters?.exist_bug ?? false}
              onChange={(checked) => onFiltersChange({ ...filters, exist_bug: checked } as TestCaseFilters)}
            />
            <div style={{ flex: 1 }} />
            <Button size="small" type="primary" onClick={() => selectedSession && onLoadTestcases(selectedSession.id, filters)}>
              筛选
            </Button>
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => {
                const reset = { case_name: '', status: undefined, bug_id: '', exist_bug: false, module_id: filters?.module_id ?? undefined };
                onFiltersChange(reset as TestCaseFilters);
                selectedSession && onLoadTestcases(selectedSession.id, reset as TestCaseFilters);
              }}
            >
              重置
            </Button>
          </div>

          {/* Table */}
          <TestCaseTable
            testcases={testcases}
            onView={onView}
            onEdit={onEdit}
            onComplete={onComplete}
            onDelete={onDelete}
            onBatchDelete={onBatchDelete}
            onMove={onMove}
            onApiExecute={onApiExecute}
            onBatchMove={onBatchMove}
          />
        </div>
      )}
    </Card>
  );
};

export default TestCaseManager;
