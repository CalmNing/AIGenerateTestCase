import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Empty, Input, Modal, Progress, Select, Space, Table, TableProps, Tabs, Tag, Tooltip, message } from 'antd';
import { PlayCircleOutlined, StopOutlined, LinkOutlined, SaveOutlined } from '@ant-design/icons';
import { TestCase, UIBoundAssert, UIBoundStep, UICompileResult, UIExecutionRecord, UIExecutionTaskStatus } from '../../types';
import { globalParameterApi, uiExecutionApi, UILoginConfig } from '../../services/api';

/** 判断用例是否可执行 UI(无 API 调用步骤) */
export const isUiExecutable = (testcase: TestCase): boolean => {
  if (!testcase.steps || testcase.steps.length === 0) return false;
  return !testcase.steps.some((s) => {
    if (typeof s !== 'object' || s === null) return false;
    const dict = s as Record<string, any>;
    return dict.type === 'api_call' || dict.endpoint_id != null || dict.endpoint_ref != null;
  });
};

const LAST_URL_KEY = (id: number) => `ui_exec_last_${id}`;

const statusColorMap: Record<UIExecutionRecord['status'], string> = {
  PASSED: 'success',
  FAILED: 'error',
  ERROR: 'warning',
  STOPPED: 'default',
  RUNNING: 'processing',
};

const levelColorMap: Record<string, string> = {
  L1: 'green',
  L2: 'gold',
  L3: 'default',
};

const formatTime = (value?: string | null) => {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
};

interface UIExecutionModalProps {
  open: boolean;
  testcase: TestCase | null;
  onClose: () => void;
  onFinished?: () => void; // 终态后通知父级刷新用例列表
}

interface TerminalState {
  result_status: 'PASSED' | 'FAILED' | 'ERROR' | 'STOPPED';
  message: string;
}

const ExecutionDetail: React.FC<{ record: UIExecutionRecord }> = ({ record }) => {
  const steps = Array.isArray(record.steps_trace) ? record.steps_trace : [];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: '8px 4px' }}>
      {record.execution_mode === 'hybrid' ? (
        <Tag color="blue" style={{ alignSelf: 'flex-start' }}>Hybrid 混合执行</Tag>
      ) : null}
      {steps.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {steps.map((s, i) => (
            <div key={`${s.step_no}-${i}`} style={{ display: 'flex', gap: 8, fontSize: 13, alignItems: 'flex-start' }}>
              <Tag style={{ flexShrink: 0, marginInlineEnd: 0 }}>#{s.step_no}</Tag>
              {s.level ? (
                <Tag color={levelColorMap[s.level] ?? 'default'} style={{ flexShrink: 0, marginInlineEnd: 0 }}>
                  {s.level}
                </Tag>
              ) : null}
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 500 }}>
                  {s.nl || s.action}
                  {s.healed ? <Tag color="purple" style={{ marginInlineStart: 6 }}>自愈</Tag> : null}
                </div>
                {s.element_key ? (
                  <div style={{ color: 'var(--color-text-tertiary)', fontSize: 12 }}>元素: {s.element_key}</div>
                ) : null}
                {s.url ? (
                  <div style={{ color: 'var(--color-text-tertiary)', fontSize: 12 }}>{s.url}</div>
                ) : null}
                {s.error ? (
                  <div style={{ color: '#cf1322', fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{s.error}</div>
                ) : null}
                {s.result && !s.error ? (
                  <div style={{ fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{s.result}</div>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无步骤轨迹" />
      )}
      {record.agent_summary ? (
        <Alert type="info" showIcon message="Agent 总结" description={record.agent_summary} />
      ) : null}
    </div>
  );
};

/** 绑定表格:操作步 + 断言,可编辑 action / element_key / value */
const BindingTable: React.FC<{
  steps: UIBoundStep[];
  asserts: UIBoundAssert[];
  elements: Array<{ key: string; name?: string; type?: string }>;
  disabled?: boolean;
  onStepsChange: (steps: UIBoundStep[]) => void;
  onAssertsChange: (asserts: UIBoundAssert[]) => void;
}> = ({ steps, asserts, elements, disabled, onStepsChange, onAssertsChange }) => {
  const elementOptions = elements.map((e) => ({
    value: e.key,
    label: e.name ? `${e.name} (${e.key})` : e.key,
  }));

  const actionOptions = [
    { value: 'click', label: 'click' },
    { value: 'fill', label: 'fill' },
    { value: 'check', label: 'check' },
    { value: 'uncheck', label: 'uncheck' },
    { value: 'select_option', label: 'select_option' },
    { value: 'press', label: 'press' },
    { value: 'wait_for', label: 'wait_for' },
    { value: 'navigate', label: 'navigate' },
    { value: 'agent', label: 'agent (L3)' },
  ];

  const assertActionOptions = [
    { value: 'assert_text', label: 'assert_text' },
    { value: 'assert_visible', label: 'assert_visible' },
    { value: 'skip', label: 'skip' },
  ];

  const updateStep = (idx: number, patch: Partial<UIBoundStep>) => {
    const next = steps.map((s, i) => (i === idx ? { ...s, ...patch } : s));
    onStepsChange(next);
  };

  const updateAssert = (idx: number, patch: Partial<UIBoundAssert>) => {
    const next = asserts.map((a, i) => (i === idx ? { ...a, ...patch } : a));
    onAssertsChange(next);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ fontWeight: 500 }}>操作步骤绑定</div>
      {steps.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无步骤" />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {steps.map((s, idx) => (
            <div
              key={s.step_no}
              style={{
                display: 'grid',
                gridTemplateColumns: '48px 1fr 120px 160px 120px 56px',
                gap: 6,
                alignItems: 'center',
                fontSize: 13,
              }}
            >
              <Tag style={{ margin: 0 }}>#{s.step_no}</Tag>
              <Tooltip title={s.nl}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.nl || '-'}</span>
              </Tooltip>
              <Select
                size="small"
                value={s.action}
                options={actionOptions}
                disabled={disabled}
                onChange={(v) => updateStep(idx, { action: v, level: v === 'agent' ? 'L3' : s.level })}
                style={{ width: '100%' }}
              />
              <Select
                size="small"
                allowClear
                showSearch
                optionFilterProp="label"
                placeholder="元素"
                value={s.element_key ?? undefined}
                options={elementOptions}
                disabled={disabled || s.action === 'navigate' || s.action === 'agent'}
                onChange={(v) => updateStep(idx, { element_key: v ?? null })}
                style={{ width: '100%' }}
              />
              <Input
                size="small"
                placeholder="值"
                value={s.value ?? ''}
                disabled={disabled || s.action === 'click' || s.action === 'agent'}
                onChange={(e) => updateStep(idx, { value: e.target.value || null })}
              />
              <Tooltip title={s.reason || `confidence=${(s.confidence ?? 0).toFixed(2)}`}>
                <Tag color={levelColorMap[s.level] ?? 'default'} style={{ margin: 0, textAlign: 'center' }}>
                  {s.level}
                </Tag>
              </Tooltip>
            </div>
          ))}
        </div>
      )}

      {asserts.length > 0 ? (
        <>
          <div style={{ fontWeight: 500, marginTop: 4 }}>预期结果断言</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {asserts.map((a, idx) => (
              <div
                key={a.index}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '48px 1fr 120px 160px 120px 56px',
                  gap: 6,
                  alignItems: 'center',
                  fontSize: 13,
                }}
              >
                <Tag style={{ margin: 0 }}>#{a.index}</Tag>
                <Tooltip title={a.nl}>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.nl || '-'}</span>
                </Tooltip>
                <Select
                  size="small"
                  value={a.action === 'skip' ? 'skip' : a.action}
                  options={assertActionOptions}
                  disabled={disabled}
                  onChange={(v) => updateAssert(idx, { action: v })}
                  style={{ width: '100%' }}
                />
                <Select
                  size="small"
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  placeholder="元素"
                  value={a.element_key ?? undefined}
                  options={elementOptions}
                  disabled={disabled || a.action === 'skip'}
                  onChange={(v) => updateAssert(idx, { element_key: v ?? null })}
                  style={{ width: '100%' }}
                />
                <Input
                  size="small"
                  placeholder="期望值"
                  value={a.value ?? ''}
                  disabled={disabled || a.action === 'skip'}
                  onChange={(e) => updateAssert(idx, { value: e.target.value || null })}
                />
                <Tooltip title={a.reason || `confidence=${(a.confidence ?? 0).toFixed(2)}`}>
                  <Tag color={levelColorMap[a.level] ?? 'default'} style={{ margin: 0, textAlign: 'center' }}>
                    {a.level}
                  </Tag>
                </Tooltip>
              </div>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
};

const UIExecutionModal: React.FC<UIExecutionModalProps> = ({ open, testcase, onClose, onFinished }) => {
  const [siteOrigin, setSiteOrigin] = useState('');
  const [pagePath, setPagePath] = useState('');
  const [hasCredentials, setHasCredentials] = useState<boolean | null>(null);
  const [loginUrl, setLoginUrl] = useState('');
  const [loginMethod, setLoginMethod] = useState('');
  const [loginUsername, setLoginUsername] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [loginCompany, setLoginCompany] = useState('');
  const [running, setRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [currentTaskId, setCurrentTaskId] = useState<string | null>(null);
  const [progress, setProgress] = useState<UIExecutionTaskStatus | null>(null);
  const [terminal, setTerminal] = useState<TerminalState | null>(null);
  const [records, setRecords] = useState<UIExecutionRecord[]>([]);
  const [activeTab, setActiveTab] = useState('run');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // 站点地址是否已被占位(localStorage 或用户手动输入),防止默认环境 web_url 覆盖
  const originTouchedRef = useRef(false);

  // ---- 绑定相关状态 ----
  const [compileResult, setCompileResult] = useState<UICompileResult | null>(null);
  const [bindingSteps, setBindingSteps] = useState<UIBoundStep[]>([]);
  const [bindingAsserts, setBindingAsserts] = useState<UIBoundAssert[]>([]);
  const [compiling, setCompiling] = useState(false);
  const [savingBinding, setSavingBinding] = useState(false);
  const [bindingDirty, setBindingDirty] = useState(false); // 地址变更后绑定过期
  const [finalJudgement, setFinalJudgement] = useState(false);

  const testcaseId = testcase?.id;

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const resetBinding = useCallback(() => {
    setCompileResult(null);
    setBindingSteps([]);
    setBindingAsserts([]);
    setBindingDirty(false);
  }, []);

  // 打开弹窗时:预填 localStorage + 默认环境 web_url、拉取登录凭证状态与执行历史
  useEffect(() => {
    if (!open || testcaseId === undefined) return;
    const last = localStorage.getItem(LAST_URL_KEY(testcaseId));
    let hasLast = false;
    if (last) {
      try {
        const parsed = JSON.parse(last) as { origin?: string; path?: string };
        setSiteOrigin(parsed.origin ?? '');
        setPagePath(parsed.path ?? '');
        hasLast = true;
      } catch {
        /* 忽略损坏缓存 */
      }
    } else {
      setSiteOrigin('');
      setPagePath('');
    }
    originTouchedRef.current = hasLast;
    setProgress(null);
    setRunning(false);
    setStopping(false);
    setCurrentTaskId(null);
    setTerminal(null);
    setHasCredentials(null);
    setLoginUrl('');
    setLoginMethod('');
    setLoginUsername('');
    setLoginPassword('');
    setLoginCompany('');
    setFinalJudgement(false);
    resetBinding();
    // 若用例上已有保存的绑定,直接载入可编辑状态
    const saved = testcase?.ui_binding;
    if (saved && Array.isArray(saved.steps) && saved.steps.length > 0) {
      setCompileResult({
        page_object_id: saved.page_object_id ?? null,
        page_object_name: '',
        elements: [],
        steps: saved.steps,
        asserts: saved.asserts ?? [],
        unmatched_elements: [],
        compile_status: saved.compile_status || 'ok',
        site_origin: saved.site_origin || '',
        page_path: saved.page_path || '',
        message: saved.message || '',
      });
      setBindingSteps(saved.steps);
      setBindingAsserts(saved.asserts ?? []);
      setBindingDirty(false);
    }
    globalParameterApi
      .getEnvironments()
      .then((res) => {
        const envId = localStorage.getItem('currentEnvironmentId');
        const numericId = envId ? Number(envId) : NaN;
        const list = res.data ?? [];
        const env =
          (Number.isFinite(numericId) && numericId > 0
            ? list.find((e) => Number(e.id) === numericId)
            : undefined) ??
          list.find((e) => e.is_default) ??
          list[0];
        if (!env) return;
        // localStorage 缺失/非数字时回写真实 id,发起执行时 environment_id 才不会是 null
        if (!(Number.isFinite(numericId) && numericId > 0) && Number(env.id) > 0) {
          localStorage.setItem('currentEnvironmentId', String(env.id));
        }
        const params = env.parameters;
        const valueOf = (key: string) => params.find((p) => p.key === key)?.value ?? '';
        const webUrl = valueOf('web_url');
        // localStorage 优先;用户已手动输入时不覆盖;仅二者皆无时才用当前环境 web_url 预填
        setSiteOrigin((prev) => (originTouchedRef.current || prev.trim() ? prev : webUrl));
        // 登录配置:从全局参数预填,可在本次执行直接修改/覆盖
        setLoginUrl(valueOf('test_login_url'));
        setLoginMethod(valueOf('test_login_method'));
        setLoginUsername(valueOf('test_username'));
        setLoginPassword(valueOf('test_password'));
        setLoginCompany(valueOf('test_company'));
        setHasCredentials(
          ['test_login_url', 'test_login_method', 'test_username', 'test_password', 'test_company'].some(
            (k) => !!valueOf(k)
          )
        );
      })
      .catch(() => setHasCredentials(null));
    uiExecutionApi
      .getRecords(testcaseId)
      .then((res) => setRecords(res.data ?? []))
      .catch(() => {});
  }, [open, testcaseId, testcase, resetBinding]);

  // 关闭弹窗或组件卸载时停止轮询
  useEffect(() => {
    if (!open) stopPolling();
    return stopPolling;
  }, [open, stopPolling]);

  // 地址变更 → 绑定标记为过期(仅提示,不自动重新编译)
  useEffect(() => {
    if (compileResult) setBindingDirty(true);
  }, [siteOrigin, pagePath]);

  const getEnvironmentId = (): number | null => {
    const envRaw = localStorage.getItem('currentEnvironmentId');
    const envNum = envRaw ? Number(envRaw) : NaN;
    return Number.isFinite(envNum) && envNum > 0 ? envNum : null;
  };

  const refreshRecords = useCallback(async () => {
    if (testcaseId === undefined) return;
    try {
      const res = await uiExecutionApi.getRecords(testcaseId);
      setRecords(res.data ?? []);
    } catch {
      /* 忽略 */
    }
  }, [testcaseId]);

  // 终态处理:停止轮询、刷新历史(所有终态均执行)、展示终态 Alert
  const handleTerminal = useCallback(
    async (task: UIExecutionTaskStatus) => {
      const result = task.result_status;
      // status==='done' 即终态;result_status 为空(旧任务 dict)也按 ERROR 终态处理
      if (!result && task.status !== 'done') return; // 非终态,忽略
      stopPolling();
      setRunning(false);
      setStopping(false);
      setProgress(task);
      // 刷新执行历史:ERROR 终态也刷新,避免 执行历史 Tab 数据陈旧
      let refreshed: UIExecutionRecord[] = [];
      if (testcaseId !== undefined) {
        try {
          const res = await uiExecutionApi.getRecords(testcaseId);
          setRecords(res.data ?? []);
          refreshed = res.data ?? [];
        } catch {
          /* 忽略刷新失败,不影响终态展示 */
        }
      }
      if (result === 'ERROR' || !result) {
        setTerminal({ result_status: 'ERROR', message: task.error || task.message || '执行出错' });
      } else if (result === 'STOPPED') {
        setTerminal({ result_status: 'STOPPED', message: task.message || '用户已停止执行' });
      } else if (testcaseId !== undefined) {
        const rec = task.record_id != null ? refreshed.find((r) => r.id === task.record_id) : undefined;
        setTerminal({
          result_status: result,
          message: rec?.agent_summary || task.message || '执行完成',
        });
      } else {
        setTerminal({ result_status: result, message: task.message || '执行完成' });
      }
      onFinished?.();
    },
    [stopPolling, testcaseId, onFinished]
  );

  const handleCompile = async () => {
    if (testcaseId === undefined) return;
    if (!siteOrigin.trim()) {
      message.warning('请填写站点地址后再生成绑定');
      return;
    }
    setCompiling(true);
    try {
      const res = await uiExecutionApi.compile(testcaseId, siteOrigin.trim(), pagePath.trim(), getEnvironmentId());
      if (res.code !== 200 || !res.data) {
        message.error(res.message || '生成绑定失败');
        return;
      }
      const data = res.data;
      setCompileResult(data);
      setBindingSteps(Array.isArray(data.steps) ? data.steps : []);
      setBindingAsserts(Array.isArray(data.asserts) ? data.asserts : []);
      setBindingDirty(false);
      if (data.compile_status === 'ok') {
        message.success(data.message || '绑定生成成功');
      } else if (data.compile_status === 'no_page_object') {
        message.warning(data.message || '未匹配到页面对象,将整单 Agent 执行');
      } else {
        message.warning(data.message || '绑定编译未完全成功,可手动修正');
      }
      if (data.unmatched_elements?.length) {
        message.info(`未匹配元素: ${data.unmatched_elements.join(', ')}`);
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '生成绑定失败');
    } finally {
      setCompiling(false);
    }
  };

  const handleSaveBinding = async () => {
    if (testcaseId === undefined || !compileResult) return;
    setSavingBinding(true);
    try {
      const payload = {
        page_object_id: compileResult.page_object_id ?? null,
        steps: bindingSteps,
        asserts: bindingAsserts,
        compile_status: compileResult.compile_status || 'ok',
        site_origin: siteOrigin.trim(),
        page_path: pagePath.trim(),
        message: compileResult.message || '',
      };
      const res = await uiExecutionApi.saveBinding(testcaseId, payload);
      if (res.code !== 200 || !res.data?.saved) {
        message.error(res.message || '保存绑定失败');
        return;
      }
      message.success('绑定已保存到用例');
      setBindingDirty(false);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存绑定失败');
    } finally {
      setSavingBinding(false);
    }
  };

  const buildBindingPayload = (): Record<string, any> | null => {
    if (!compileResult) return null;
    return {
      page_object_id: compileResult.page_object_id ?? null,
      steps: bindingSteps,
      asserts: bindingAsserts,
      compile_status: compileResult.compile_status || 'ok',
      site_origin: siteOrigin.trim(),
      page_path: pagePath.trim(),
      message: compileResult.message || '',
    };
  };

  const resolveExecutionMode = (): 'auto' | 'agent' | 'hybrid' => {
    if (!compileResult || bindingSteps.length === 0) return 'agent';
    const hasL12 =
      bindingSteps.some((s) => s.level === 'L1' || s.level === 'L2') ||
      bindingAsserts.some((a) => a.level === 'L1' || a.level === 'L2');
    return hasL12 ? 'auto' : 'agent';
  };

  const startExecution = async () => {
    // 执行中防抖:防止快速双击重复发起
    if (running || pollRef.current) return;
    if (testcaseId === undefined) return;
    if (!siteOrigin.trim()) {
      message.warning('请填写站点地址');
      return;
    }
    if (bindingDirty && compileResult) {
      message.warning('站点地址已变更,绑定可能过期,请重新「生成绑定」后再执行(或忽略继续以 Agent 模式)');
    }
    try {
      const login: UILoginConfig = {};
      if (loginUrl.trim()) login.login_url = loginUrl.trim();
      if (loginMethod.trim()) login.login_method = loginMethod.trim();
      if (loginUsername.trim()) login.login_username = loginUsername.trim();
      if (loginPassword) login.login_password = loginPassword;
      if (loginCompany.trim()) login.login_company = loginCompany.trim();
      const environmentId = getEnvironmentId();
      const uiBinding = buildBindingPayload();
      const res = await uiExecutionApi.run(testcaseId, siteOrigin.trim(), pagePath.trim(), {
        login,
        environmentId,
        ui_binding: uiBinding,
        execution_mode: resolveExecutionMode(),
        final_judgement: finalJudgement,
      });
      if (res.code !== 200 || !res.data) {
        message.error(res.message || '发起执行失败');
        return;
      }
      localStorage.setItem(
        LAST_URL_KEY(testcaseId),
        JSON.stringify({ origin: siteOrigin.trim(), path: pagePath.trim() })
      );
      setTerminal(null);
      setProgress(null);
      setRunning(true);
      setStopping(false);
      const taskId = res.data.task_id;
      setCurrentTaskId(taskId);
      pollRef.current = setInterval(async () => {
        if (testcaseId === undefined) return;
        try {
          const taskRes = await uiExecutionApi.getTask(taskId);
          if (taskRes.code === 200 && taskRes.data) {
            setProgress(taskRes.data);
            // status==='done' 即终态;result_status 为空(旧任务 dict)也终止轮询
            if (taskRes.data.result_status || taskRes.data.status === 'done') {
              await handleTerminal(taskRes.data);
            }
          }
        } catch {
          /* 轮询失败忽略,等待下次轮询 */
        }
      }, 2000);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '发起执行失败');
    }
  };

  const handleStop = async () => {
    if (!currentTaskId || stopping) return;
    setStopping(true);
    try {
      const res = await uiExecutionApi.stop(currentTaskId);
      if (res.code !== 200 || !res.data?.stopped) {
        message.info(res.message || '任务已结束');
        setStopping(false);
      }
      // 成功:保持 stopping,等轮询拿到 STOPPED 终态后由 handleTerminal 复位
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '停止失败');
      setStopping(false);
    }
  };

  const handleTabChange = (key: string) => {
    setActiveTab(key);
    if (key === 'history') {
      refreshRecords();
    }
  };

  const columns: TableProps<UIExecutionRecord>['columns'] = [
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (status: UIExecutionRecord['status']) => (
        <Tag color={statusColorMap[status] ?? 'default'}>{status}</Tag>
      ),
    },
    {
      title: '模式',
      dataIndex: 'execution_mode',
      width: 80,
      render: (mode?: string) =>
        mode === 'hybrid' ? <Tag color="blue">hybrid</Tag> : <Tag>agent</Tag>,
    },
    { title: '站点地址', dataIndex: 'target_url', ellipsis: true },
    {
      title: '结束时间',
      dataIndex: 'finished_at',
      width: 170,
      render: (value: string | null | undefined) => formatTime(value),
    },
    {
      title: 'Agent 总结',
      dataIndex: 'agent_summary',
      ellipsis: { showTitle: false },
      render: (text: string) =>
        text ? (
          <Tooltip title={text}>
            <span>{text}</span>
          </Tooltip>
        ) : (
          '-'
        ),
    },
  ];

  const presetConditions = testcase?.preset_conditions ?? [];
  // 登录是否有效:显式登录入口 或 完整账号密码(与后端 LoginContext.hint 分支一致)
  const loginConfigured = !!loginUrl.trim() || !!(loginUsername && loginPassword);
  const hasBinding = !!compileResult && bindingSteps.length > 0;

  const runTab = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div>
        <div style={{ marginBottom: 6, fontWeight: 500 }}>站点地址</div>
        <Input
          value={siteOrigin}
          onChange={(e) => setSiteOrigin(e.target.value)}
          placeholder="https://example.com 或 {{web_url}}"
          disabled={running}
          allowClear
        />
      </div>
      <div>
        <div style={{ marginBottom: 6, fontWeight: 500 }}>页面路径</div>
        <Input
          value={pagePath}
          onChange={(e) => setPagePath(e.target.value)}
          placeholder="/path/to/page (可留空)"
          disabled={running}
          allowClear
        />
      </div>

      {/* ---- 绑定(编译式为主) ---- */}
      <div
        style={{
          border: '1px solid #f0f0f0',
          borderRadius: 6,
          padding: 12,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        <Space style={{ justifyContent: 'space-between', width: '100%' }} size={8}>
          <span style={{ fontWeight: 500 }}>
            <LinkOutlined style={{ marginInlineEnd: 6 }} />
            步骤绑定
          </span>
          <Space size={8}>
            {bindingDirty && hasBinding ? (
              <Tag color="orange">地址已变更,绑定可能过期</Tag>
            ) : null}
            <Button
              size="small"
              type="primary"
              ghost
              loading={compiling}
              disabled={running || compiling}
              onClick={handleCompile}
            >
              生成绑定
            </Button>
            {hasBinding ? (
              <Button
                size="small"
                icon={<SaveOutlined />}
                loading={savingBinding}
                disabled={running || savingBinding}
                onClick={handleSaveBinding}
              >
                保存绑定
              </Button>
            ) : null}
          </Space>
        </Space>
        {compileResult ? (
          compileResult.compile_status === 'ok' ? (
            <Alert
              type="success"
              showIcon
              message={compileResult.message || `已匹配页面对象「${compileResult.page_object_name}」`}
              description={
                <span>
                  操作步 {bindingSteps.length} · 断言 {bindingAsserts.length}
                  {compileResult.unmatched_elements?.length
                    ? ` · 未匹配元素: ${compileResult.unmatched_elements.join(', ')}`
                    : ''}
                </span>
              }
            />
          ) : (
            <Alert
              type="warning"
              showIcon
              message={compileResult.message || `编译状态: ${compileResult.compile_status}`}
              description="可手动修正绑定后保存;或直接以 Agent 模式执行。"
            />
          )
        ) : (
          <Alert
            type="info"
            showIcon
            message="尚未生成绑定"
            description="点击「生成绑定」将自然语言步骤编译到 PageObject 元素(L1/L2 确定性执行 + L3 Agent 兜底)。"
          />
        )}
        {hasBinding ? (
          <div style={{ maxHeight: 260, overflowY: 'auto', paddingInlineEnd: 4 }}>
            <BindingTable
              steps={bindingSteps}
              asserts={bindingAsserts}
              elements={compileResult?.elements ?? []}
              disabled={running}
              onStepsChange={setBindingSteps}
              onAssertsChange={setBindingAsserts}
            />
          </div>
        ) : null}
        <Checkbox
          checked={finalJudgement}
          disabled={running}
          onChange={(e) => setFinalJudgement(e.target.checked)}
        >
          <Tooltip title="对未结构化为断言的预期结果做 LLM 终判(默认跳过,记入 Agent 总结)">
            对未结构化预期做 LLM 终判
          </Tooltip>
        </Checkbox>
      </div>

      <div
        style={{
          border: '1px solid #f0f0f0',
          borderRadius: 6,
          padding: 12,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        <div style={{ fontWeight: 500 }}>登录配置(可选,留空回退全局参数)</div>
        <Input
          addonBefore="登录入口"
          value={loginUrl}
          onChange={(e) => setLoginUrl(e.target.value)}
          placeholder="SSO/登录页直链,如 http://host/sso/"
          disabled={running}
          allowClear
        />
        <Input
          addonBefore="登录方式"
          value={loginMethod}
          onChange={(e) => setLoginMethod(e.target.value)}
          placeholder="如 鲁班通行证登录"
          disabled={running}
          allowClear
        />
        <Input
          addonBefore="用户名"
          value={loginUsername}
          onChange={(e) => setLoginUsername(e.target.value)}
          placeholder="登录账号"
          disabled={running}
          allowClear
        />
        <Input.Password
          addonBefore="密码"
          value={loginPassword}
          onChange={(e) => setLoginPassword(e.target.value)}
          placeholder="登录密码"
          disabled={running}
        />
        <Input
          addonBefore="企业"
          value={loginCompany}
          onChange={(e) => setLoginCompany(e.target.value)}
          placeholder="登录后企业选择,如 建设集团公司"
          disabled={running}
          allowClear
        />
      </div>
      {presetConditions.length > 0 ? (
        <Alert
          type="info"
          showIcon
          message="前置条件"
          description={
            <div style={{ paddingLeft: 4 }}>
              {presetConditions.map((c, i) => (
                <div key={i}>· {typeof c === 'string' ? c : JSON.stringify(c)}</div>
              ))}
            </div>
          }
        />
      ) : null}
      {hasCredentials === null ? null : loginConfigured ? (
        <Alert
          type="success"
          showIcon
          message="已配置登录参数,Agent 将按配置登录,不会猜测其他账号密码"
        />
      ) : (
        <Alert
          type="warning"
          showIcon
          message="未配置登录参数:目标系统需登录时请填写登录入口/用户名/密码,或先在全局参数中配置"
        />
      )}
      {running ? (
        progress ? (
          <div style={{ textAlign: 'center' }}>
            <Progress percent={progress.progress} status="active" />
            <div style={{ color: 'var(--color-text-tertiary)', fontSize: 12, whiteSpace: 'pre-wrap' }}>
              {progress.message}
            </div>
          </div>
        ) : (
          <Alert type="info" showIcon message="已发起执行,等待 Agent 启动..." />
        )
      ) : null}
      {terminal ? (
        terminal.result_status === 'PASSED' ? (
          <Alert type="success" showIcon message="执行成功" description={terminal.message} />
        ) : terminal.result_status === 'FAILED' ? (
          <Alert type="error" showIcon message="执行失败" description={terminal.message} />
        ) : terminal.result_status === 'STOPPED' ? (
          <Alert type="warning" showIcon message="执行已停止" description={terminal.message} />
        ) : (
          <Alert type="warning" showIcon message="执行出错" description={terminal.message} />
        )
      ) : null}
      <div style={{ display: 'flex', gap: 8 }}>
        <Button
          type="primary"
          icon={<PlayCircleOutlined />}
          loading={running}
          disabled={running}
          block
          onClick={startExecution}
        >
          开始执行
        </Button>
        {running ? (
          <Button danger icon={<StopOutlined />} loading={stopping} disabled={stopping} onClick={handleStop}>
            停止执行
          </Button>
        ) : null}
      </div>
    </div>
  );

  const historyTab = (
    <Table
      size="small"
      rowKey="id"
      dataSource={records}
      columns={columns}
      pagination={{ pageSize: 5, showSizeChanger: false }}
      locale={{ emptyText: '暂无执行记录' }}
      expandable={{ expandedRowRender: (record) => <ExecutionDetail record={record} /> }}
    />
  );

  return (
    <Modal
      title={`UI 执行${testcase?.case_name ? ` - ${testcase.case_name}` : ''}`}
      open={open}
      onCancel={onClose}
      footer={null}
      width={780}
      styles={{ body: { padding: '16px 20px' } }}
    >
      {testcase ? (
        <Tabs
          activeKey={activeTab}
          onChange={handleTabChange}
          items={[
            { key: 'run', label: '执行', children: runTab },
            { key: 'history', label: '执行历史', children: historyTab },
          ]}
        />
      ) : null}
    </Modal>
  );
};

export default UIExecutionModal;
