import React, { useEffect, useState } from 'react';
import { Button, Input, Select, message, Switch } from 'antd';
import { DownloadOutlined, FileTextOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { Session, TestCase, TestCaseResponse, TestCaseStatus, Module, TestCaseFilters } from '../../types';
import { configApi, testcaseApi } from '../../services/api';
import TestCaseTable from './TestCaseTable';
import * as XLSX from 'xlsx';
import { formatStep } from '../../utils/stepUtils';
import './TestCaseManager.css';

interface TestCaseManagerProps {
  selectedSession: Session | null;
  modules: Module[];
  selectedModule: number | string;
  testcases: TestCase[];
  selectedTestcaseId?: number | null;
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
  onViewExecutionLogs?: (testcase: TestCase) => void;
  onInferDependencies?: (testcase: TestCase) => void;
  onValidate?: (testcase: TestCase) => void;
  onMove: (testcase: TestCase) => void;
}

const showMessage = (
  type: 'success' | 'error' | 'warning',
  title: string,
  description?: string,
) => {
  message.open({
    type,
    content: description ? `${title}: ${description}` : title,
  });
};

const statItems = (resp: TestCaseResponse, filters?: TestCaseFilters) => [
  { key: 'all', label: '全部', value: resp.totalNumber, color: 'var(--color-primary)', bg: 'var(--color-primary-bg)', active: !filters?.status && !filters?.exist_bug, status: undefined, exist_bug: false },
  { key: 'passed', label: '已通过', value: resp.passed, color: 'var(--color-success)', bg: 'var(--color-success-bg)', active: filters?.status === TestCaseStatus.PASSED, status: TestCaseStatus.PASSED, exist_bug: false },
  { key: 'not_run', label: '未执行', value: resp.not_run, color: 'var(--color-warning)', bg: 'var(--color-warning-bg)', active: filters?.status === TestCaseStatus.NOT_RUN, status: TestCaseStatus.NOT_RUN, exist_bug: false },
  { key: 'failed', label: '未通过', value: resp.failed, color: 'var(--color-danger)', bg: 'var(--color-danger-bg)', active: filters?.status === TestCaseStatus.FAILED, status: TestCaseStatus.FAILED, exist_bug: false },
  { key: 'bug', label: '有 Bug', value: resp.totalBugs, color: 'var(--color-danger)', bg: 'var(--color-danger-bg)', active: !!filters?.exist_bug, status: undefined, exist_bug: true },
];

const TestCaseManager: React.FC<TestCaseManagerProps> = ({
  selectedSession,
  modules,
  selectedModule,
  testcases,
  selectedTestcaseId,
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
  onViewExecutionLogs,
  onInferDependencies,
  onValidate,
  onMove,
  onBatchMove,
}) => {
  const handleValidate = async (testcase: TestCase) => {
    if (onValidate) {
      onValidate(testcase);
      return;
    }
    try {
      const res = await testcaseApi.validateTestcase(testcase.id);
      if (res.code === 200 && res.data) {
        const status = res.data.validation_status;
        if (status === 'passed') {
          message.success(`测试用例 ${testcase.case_name} 真实执行通过`);
        } else if (status === 'failed') {
          message.warning(`测试用例 ${testcase.case_name} 真实执行未通过`);
        } else {
          message.error(`测试用例 ${testcase.case_name} 验证执行出错`);
        }
      } else {
        message.error(res.message || '验证失败');
      }
    } catch (error: any) {
      message.error(error?.response?.data?.message || error.message || '验证失败');
    } finally {
      if (selectedSession) {
        onLoadTestcases(selectedSession.id, filters);
      }
    }
  };

  const [bugLinkTemplate, setBugLinkTemplate] = useState('');

  useEffect(() => {
    configApi.getConfig().then((res) => {
      if (res.code === 200 && res.data) {
        setBugLinkTemplate(res.data.bug_link_template || '');
      }
    }).catch(() => {});
  }, []);

  const handleExportExcel = () => {
    if (testcases.length === 0) {
      showMessage('warning', '导出失败', '当前没有可导出的测试用例');
      return;
    }

    try {
      const exportData = testcases.map((tc) => ({
        模块名称: tc.module_id ? modules.find((m) => m.id === tc.module_id)?.module_name || '未知模块' : '未分配模块',
        用例名称: tc.case_name,
        用例级别: `P${tc.case_level}`,
        前置条件: tc.preset_conditions.map((item: any, idx) => {
          const text = typeof item === 'string' ? item : formatStep(item);
          return `${idx + 1}. ${text}`;
        }).join('\n'),
        测试步骤: tc.steps.map((item: any, idx) => `${idx + 1}. ${typeof item === 'string' ? item : formatStep(item)}`).join('\n'),
        预期结果: tc.expected_results.map((item, idx) => `${idx + 1}. ${typeof item === 'string' ? item : formatStep(item)}`).join('\n'),
        状态: tc.status === TestCaseStatus.PASSED ? '已通过' : tc.status === TestCaseStatus.FAILED ? '未通过' : '未执行',
        Bug: tc.bug_id
          ? (bugLinkTemplate ? bugLinkTemplate.replace('{bug_id}', String(tc.bug_id)) : `http://zt.luban.fit/index.php?m=bug&f=view&bugID=${tc.bug_id}`)
          : '',
      }));

      const ws = XLSX.utils.json_to_sheet(exportData);
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, '测试用例');
      XLSX.writeFile(wb, `测试用例_${new Date().toISOString().slice(0, 10)}.xlsx`);
      showMessage('success', '导出成功', `已导出 ${testcases.length} 条测试用例`);
    } catch (error) {
      console.error('导出 Excel 失败:', error);
      showMessage('error', '导出失败', '导出 Excel 时发生错误，请重试');
    }
  };

  const setFilter = (status: TestCaseStatus | undefined, existBug: boolean) => {
    onFiltersChange({
      case_name: '',
      bug_id: '',
      exist_bug: existBug,
      status,
      module_id: selectedModule === 0 ? undefined : Number(selectedModule),
    });
  };

  return (
    <div className="tcm-container">
      {!selectedSession ? (
        <div className="tcm-empty">
          <FileTextOutlined className="tcm-empty-icon" />
          <span className="tcm-empty-text">请先从左侧选择一个会话</span>
        </div>
      ) : (
        <>
          <div className="tcm-stats-bar">
            {statItems(testcasesResponse, filters).map((item) => (
              <div
                key={item.key}
                className={`tcm-stat-pill ${item.active ? 'is-active' : ''}`}
                onClick={() => setFilter(item.status, item.exist_bug)}
                style={{
                  background: item.active ? item.bg : 'transparent',
                  color: item.active ? item.color : 'var(--color-text-secondary)',
                }}
              >
                <span>{item.label}</span>
                <span className="tcm-stat-pill-count" style={{ color: item.active ? item.color : 'var(--color-text)' }}>
                  {item.value}
                </span>
              </div>
            ))}
            <div className="tcm-stats-actions">
              <Button size="small" type="primary" icon={<PlusOutlined />} onClick={onAdd}>新增</Button>
              <Button size="small" icon={<DownloadOutlined />} onClick={handleExportExcel}>导出</Button>
            </div>
          </div>

          <div className="tcm-filter-bar">
            <label>名称</label>
            <Input
              size="small"
              placeholder="搜索用例名称"
              value={filters?.case_name ?? ''}
              onChange={(e) => onFiltersChange({ ...filters, case_name: e.target.value } as TestCaseFilters)}
              onPressEnter={() => selectedSession && onLoadTestcases(selectedSession.id, filters)}
              style={{ width: 180 }}
              allowClear
            />
            <label>状态</label>
            <Select
              size="small"
              placeholder="全部"
              allowClear
              value={filters?.status ?? undefined}
              onChange={(value) => onFiltersChange({ ...filters, status: value } as TestCaseFilters)}
              style={{ width: 110 }}
              options={[
                { label: '未执行', value: TestCaseStatus.NOT_RUN },
                { label: '已通过', value: TestCaseStatus.PASSED },
                { label: '未通过', value: TestCaseStatus.FAILED },
              ]}
            />
            <label>Bug ID</label>
            <Input
              size="small"
              placeholder="输入 Bug ID"
              value={filters?.bug_id ?? ''}
              onChange={(e) => onFiltersChange({ ...filters, bug_id: e.target.value } as TestCaseFilters)}
              onPressEnter={() => selectedSession && onLoadTestcases(selectedSession.id, filters)}
              style={{ width: 120 }}
              allowClear
            />
            <label>仅看有 Bug</label>
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
                const reset = {
                  case_name: '',
                  status: undefined,
                  bug_id: '',
                  exist_bug: false,
                  module_id: filters?.module_id ?? undefined,
                };
                onFiltersChange(reset as TestCaseFilters);
                selectedSession && onLoadTestcases(selectedSession.id, reset as TestCaseFilters);
              }}
            >
              重置
            </Button>
          </div>

          <TestCaseTable
            testcases={testcases}
            selectedTestcaseId={selectedTestcaseId}
            onView={onView}
            onEdit={onEdit}
            onComplete={onComplete}
            onDelete={onDelete}
            onBatchDelete={onBatchDelete}
            onMove={onMove}
            onApiExecute={onApiExecute}
            onViewExecutionLogs={onViewExecutionLogs}
            onInferDependencies={onInferDependencies}
            onValidate={onValidate ?? handleValidate}
            onBatchMove={onBatchMove}
            bugLinkTemplate={bugLinkTemplate}
          />
        </>
      )}
    </div>
  );
};

export default TestCaseManager;
