import React, { useEffect, useMemo, useState } from 'react';
import { Button, Input, Select, message, Switch, Modal, Radio, Tooltip, Input as TextInput } from 'antd';
import { CommentOutlined, DownloadOutlined, FileTextOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { Session, TestCase, TestCaseResponse, TestCaseStatus, Module, TestCaseFilters } from '../../types';
import { configApi, testcaseApi } from '../../services/api';
import TestCaseTable from './TestCaseTable';
import ChatAssistant from '../chat/ChatAssistant';
import { ParsedSuggestedCase } from '../../utils/suggestedCases';
import * as XLSX from 'xlsx';
import { formatStep } from '../../utils/stepUtils';
import { formatTestCaseLevel } from '../../utils/testCaseLevel';
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
  onValidate?: (testcase: TestCase) => void;
  onUiExecute?: (testcase: TestCase) => void;
  onMove: (testcase: TestCase) => void;
}

// B7：驳回理由选项（与后端 REJECT_REASONS 对齐）
const REJECT_OPTIONS = [
  { value: 'duplicate', label: '与已有用例重复' },
  { value: 'vague_steps', label: '步骤笼统不可执行' },
  { value: 'unverifiable_expect', label: '预期结果不可验证' },
  { value: 'missing_branch', label: '遗漏分支/场景' },
  { value: 'fact_error', label: '事实/业务错误' },
  { value: 'other', label: '其他' },
];

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
  onValidate,
  onUiExecute,
  onMove,
  onBatchMove,
}) => {
  // B7：驳回弹窗状态
  const [rejectTarget, setRejectTarget] = useState<TestCase | null>(null);
  const [rejectReason, setRejectReason] = useState<string>('duplicate');
  const [rejectDetail, setRejectDetail] = useState<string>('');

  // AI 用例对话：以当前会话/模块/筛选下的用例清单为上下文
  const [chatOpen, setChatOpen] = useState(false);
  // 引入 AI 补充用例：预览确认后再批量创建
  const [importOpen, setImportOpen] = useState(false);
  const [importCases, setImportCases] = useState<ParsedSuggestedCase[]>([]);
  const [importing, setImporting] = useState(false);
  const [importExcluded, setImportExcluded] = useState<Set<number>>(new Set());

  const openReject = (testcase: TestCase) => {
    setRejectTarget(testcase);
    setRejectReason('duplicate');
    setRejectDetail('');
  };

  const confirmReject = async () => {
    if (!rejectTarget || !selectedSession) return;
    try {
      const res = await testcaseApi.rejectTestcase(
        selectedSession.id,
        rejectTarget.id,
        rejectReason,
        rejectDetail.trim() || undefined,
      );
      if (res.code === 200) {
        message.success('已驳回，理由将用于改进生成质量');
        setRejectTarget(null);
        onLoadTestcases(selectedSession.id, filters);
      } else {
        message.error(res.message || '驳回失败');
      }
    } catch (e: any) {
      message.error('驳回失败: ' + (e?.message || '未知错误'));
    }
  };

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
        用例级别: formatTestCaseLevel(tc.case_level),
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

  // 引入 AI 建议用例：预览 → 可勾选排除 → 批量创建 → 刷新列表
  const handleImportCases = (cases: ParsedSuggestedCase[]) => {
    if (cases.length === 0) return;
    setImportCases(cases);
    setImportExcluded(new Set());
    setImportOpen(true);
  };

  const handleImportConfirm = async () => {
    if (!selectedSession) return;
    const toCreate = importCases.filter((_, idx) => !importExcluded.has(idx));
    if (toCreate.length === 0) {
      message.warning('请至少选择一条要引入的用例');
      return;
    }
    setImporting(true);
    let ok = 0;
    const failures: string[] = [];
    for (const tc of toCreate) {
      try {
        const res = await testcaseApi.createTestcase(selectedSession.id, {
          case_name: tc.case_name,
          case_level: tc.case_level,
          preset_conditions: tc.preset_conditions,
          steps: tc.steps,
          expected_results: tc.expected_results,
          module_id: selectedModule === 0 ? undefined : Number(selectedModule),
          session_id: selectedSession.id,
        } as any);
        if (res.code === 200) ok += 1;
        else failures.push(tc.case_name);
      } catch {
        failures.push(tc.case_name);
      }
    }
    setImporting(false);
    if (ok > 0) {
      message.success('已引入 ' + ok + ' 条用例' + (failures.length > 0 ? '，' + failures.length + ' 条失败' : ''));
      setImportOpen(false);
      onLoadTestcases(selectedSession.id, filters);
    } else {
      message.error('引入失败：' + (failures[0] || '未知错误'));
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

  // ===== AI 用例对话上下文 =====

  // 状态中文映射（避免引入循环依赖，就地映射）
  const statusLabel = (tc: TestCase) =>
    tc.status === TestCaseStatus.PASSED ? '已通过'
      : tc.status === TestCaseStatus.FAILED ? '未通过' : '未执行';

  // 选中用例的完整详情（多行文本，供 AI 引用步骤/预期）
  const selectedTestcase = useMemo(
    () => (selectedTestcaseId ? testcases.find((tc) => tc.id === selectedTestcaseId) ?? null : null),
    [selectedTestcaseId, testcases],
  );

  const testcaseDetailText = (tc: TestCase) => {
    const lines = [
      '【当前选中用例】',
      '用例名称：' + tc.case_name,
      '用例级别：' + formatTestCaseLevel(tc.case_level),
      '状态：' + statusLabel(tc) + (tc.bug_id ? ' · Bug ID: ' + tc.bug_id : ''),
      '前置条件：',
      ...tc.preset_conditions.map((item: any, idx: number) => '  ' + (idx + 1) + '. ' + (typeof item === 'string' ? item : formatStep(item))),
      '测试步骤：',
      ...tc.steps.map((item: any, idx: number) => '  ' + (idx + 1) + '. ' + (typeof item === 'string' ? item : formatStep(item))),
      '预期结果：',
      ...tc.expected_results.map((item: any, idx: number) => '  ' + (idx + 1) + '. ' + (typeof item === 'string' ? item : formatStep(item))),
    ];
    return lines.join('\n');
  };

  const chatContext = useMemo(() => {
    if (!selectedSession) return '';
    const moduleName = selectedModule !== 0
      ? (modules.find((m) => m.id === Number(selectedModule))?.module_name || ('模块 ' + selectedModule))
      : '全部模块';
    const lines: string[] = [
      '【测试会话】' + (selectedSession.name || ('会话 ' + selectedSession.id)),
      '【模块范围】' + moduleName,
      '【用例总数】' + testcases.length + ' 条（当前筛选结果）',
      '',
      '【用例清单' + (testcases.length > 60 ? '（仅前 60 条）' : '') + '】',
    ];
    if (testcases.length === 0) {
      lines.push('（当前筛选下没有测试用例）');
    } else {
      testcases.slice(0, 60).forEach((tc, idx) => {
        lines.push(
          (idx + 1) + '. [' + formatTestCaseLevel(tc.case_level) + '] ' + tc.case_name
          + ' — ' + statusLabel(tc) + (tc.bug_id ? ' — Bug ' + tc.bug_id : ''),
        );
      });
    }
    if (selectedTestcase) {
      lines.push('', testcaseDetailText(selectedTestcase));
    }
    return lines.join('\n');
  }, [selectedSession, selectedModule, modules, testcases, selectedTestcase]);

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
              <Tooltip title="基于当前用例清单的多轮 AI 对话：分析覆盖缺口、给出补充用例建议">
                <Button size="small" icon={<CommentOutlined />} onClick={() => setChatOpen(true)}>AI 对话</Button>
              </Tooltip>
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
            onValidate={onValidate ?? handleValidate}
            onUiExecute={onUiExecute}
            onReject={openReject}
            onBatchMove={onBatchMove}
            bugLinkTemplate={bugLinkTemplate}
          />

          {/* B7：驳回理由弹窗 */}
          <Modal
            title={`驳回用例：${rejectTarget?.case_name ?? ''}`}
            open={!!rejectTarget}
            onOk={confirmReject}
            onCancel={() => setRejectTarget(null)}
            okText="确认驳回"
            cancelText="取消"
            width={480}
          >
            <div style={{ marginBottom: 12 }}>
              <div style={{ marginBottom: 6, fontWeight: 500 }}>驳回理由（用于改进生成质量）</div>
              <Radio.Group
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
              >
                {REJECT_OPTIONS.map((opt) => (
                  <Radio key={opt.value} value={opt.value}>{opt.label}</Radio>
                ))}
              </Radio.Group>
            </div>
            <TextInput.TextArea
              rows={2}
              placeholder="补充说明（可选）"
              value={rejectDetail}
              onChange={(e) => setRejectDetail(e.target.value)}
            />
          </Modal>
        </>
      )}

      {/* 引入 AI 建议用例：预览确认 */}
      <Modal
        title={'引入 AI 补充用例（' + (importCases.length - importExcluded.size) + '/' + importCases.length + ' 条）'}
        open={importOpen}
        onCancel={() => setImportOpen(false)}
        onOk={() => void handleImportConfirm()}
        confirmLoading={importing}
        okText={'引入所选 ' + (importCases.length - importExcluded.size) + ' 条'}
        cancelText="取消"
        width={860}
      >
        <div style={{ marginBottom: 8, fontSize: 12, color: 'var(--color-text-tertiary)' }}>
          取消勾选的用例不会被引入；引入后用例出现在当前会话的用例列表中（模块取当前筛选）。
        </div>
        <div style={{ maxHeight: 480, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 8 }}>
          {importCases.map((tc, idx) => {
            const checked = !importExcluded.has(idx);
            return (
              <div
                key={idx}
                style={{
                  border: '1px solid ' + (checked ? '#d9d9d9' : '#f0f0f0'),
                  borderRadius: 8,
                  padding: '10px 12px',
                  opacity: checked ? 1 : 0.5,
                }}
              >
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => {
                      setImportExcluded((prev) => {
                        const next = new Set(prev);
                        if (next.has(idx)) next.delete(idx);
                        else next.add(idx);
                        return next;
                      });
                    }}
                  />
                  <strong>{tc.case_name}</strong>
                  <span style={{ fontSize: 12, color: '#888' }}>
                    级别 {tc.case_level} · 步骤 {tc.steps.length} · 预期 {tc.expected_results.length}
                  </span>
                </label>
                {tc.note && <div style={{ fontSize: 12, color: '#888', marginTop: 4 }}>关联说明：{tc.note}</div>}
                <div style={{ fontSize: 12, marginTop: 6, color: '#555' }}>
                  {tc.preset_conditions.length > 0 && <div>前置：{tc.preset_conditions.join('；')}</div>}
                  {tc.steps.slice(0, 3).map((s, i) => <div key={i}>{i + 1}. {s}</div>)}
                  {tc.steps.length > 3 && <div>… 共 {tc.steps.length} 步</div>}
                </div>
              </div>
            );
          })}
        </div>
      </Modal>

      {/* AI 用例对话：以当前会话/模块/筛选下的用例清单为上下文 */}
      <ChatAssistant
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        title="AI 用例对话"
        mode="testcase_optimize"
        context={chatContext}
        scopeKey={selectedSession ? 'case-opt-' + selectedSession.id : undefined}
        welcomeHint={
          selectedSession
            ? '我会基于当前会话「' + (selectedSession.name || '') + '」的用例清单与你讨论：分析覆盖缺口（功能/边界/异常/场景）、评审步骤与预期可执行性、给出补充用例建议。'
            : '请先从左侧选择一个会话。'
        }
        quickActions={[
          { label: '分析覆盖缺口', prompt: '请分析当前用例清单的覆盖缺口（功能/边界/异常/场景四类），按优先级列出，并引用用例名称定位问题。' },
          { label: '给出补充用例建议（可引入）', prompt: '请针对刚才指出的覆盖缺口给出补充用例建议，每条建议严格按“### 建议：用例名称”小节格式输出（含用例级别、前置条件、测试步骤、预期结果、关联说明），我会在回复下方一键引入这些用例。' },
        ]}
        onImportCases={handleImportCases}
        width="66vw"
      />
    </div>
  );
};

export default TestCaseManager;