import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Empty, Input, Modal, Progress, Table, TableProps, Tabs, Tag, Tooltip, message } from 'antd';
import { PlayCircleOutlined, StopOutlined } from '@ant-design/icons';
import { TestCase, UIExecutionRecord, UIExecutionTaskStatus } from '../../types';
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
      {steps.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {steps.map((s) => (
            <div key={s.step_no} style={{ display: 'flex', gap: 8, fontSize: 13 }}>
              <Tag style={{ flexShrink: 0, marginInlineEnd: 0 }}>#{s.step_no}</Tag>
              <div>
                <div style={{ fontWeight: 500 }}>{s.action}</div>
                {s.url ? (
                  <div style={{ color: 'var(--color-text-tertiary)', fontSize: 12 }}>{s.url}</div>
                ) : null}
                {s.result ? (
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

  const testcaseId = testcase?.id;

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
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
    globalParameterApi
      .getEnvironments()
      .then((res) => {
        const env = (res.data ?? []).find((e) => e.is_default) ?? (res.data ?? [])[0];
        if (!env) return;
        const params = env.parameters;
        const valueOf = (key: string) => params.find((p) => p.key === key)?.value ?? '';
        const webUrl = valueOf('web_url');
        // localStorage 优先;用户已手动输入时不覆盖;仅二者皆无时才用默认环境 web_url 预填
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
  }, [open, testcaseId]);

  // 关闭弹窗或组件卸载时停止轮询
  useEffect(() => {
    if (!open) stopPolling();
    return stopPolling;
  }, [open, stopPolling]);

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

  const startExecution = async () => {
    // 执行中防抖:防止快速双击重复发起
    if (running || pollRef.current) return;
    if (testcaseId === undefined) return;
    if (!siteOrigin.trim()) {
      message.warning('请填写站点地址');
      return;
    }
    try {
      const login: UILoginConfig = {};
      if (loginUrl.trim()) login.login_url = loginUrl.trim();
      if (loginMethod.trim()) login.login_method = loginMethod.trim();
      if (loginUsername.trim()) login.login_username = loginUsername.trim();
      if (loginPassword) login.login_password = loginPassword;
      if (loginCompany.trim()) login.login_company = loginCompany.trim();
      const res = await uiExecutionApi.run(testcaseId, siteOrigin.trim(), pagePath.trim(), login);
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
      width={720}
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
