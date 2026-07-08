import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Collapse, Drawer, Empty, Input, Popover, Select, Space, Spin, Table, Tabs, Tag } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, DownOutlined, UpOutlined } from '@ant-design/icons';
import { TestCase, TestCaseExecutionLog } from '../../types';
import { configApi, testcaseApi } from '../../services/api';
import TestcaseDetailView from '../test-case/TestcaseDetailView';
import TestcaseScenarioView from '../test-case/TestcaseScenarioView';
import './ViewTestcaseModal.css';

interface ViewTestcaseModalProps {
  visible: boolean;
  sessionId?: number;
  nextButtonDisabled: boolean;
  prevButtonDisabled: boolean;
  selectedTestcase: TestCase | null;
  onCancel: () => void;
  onNext: () => void;
  onPrev: () => void;
  onMarkPassed: (testcase: TestCase | null) => Promise<void>;
  onMarkFailed: (testcase: TestCase | null, bugId: number) => Promise<void>;
}

const ViewTestcaseModal: React.FC<ViewTestcaseModalProps> = ({
  visible,
  sessionId,
  selectedTestcase,
  onCancel,
  onNext,
  nextButtonDisabled,
  prevButtonDisabled,
  onPrev,
  onMarkPassed,
  onMarkFailed,
}) => {
  const tc = selectedTestcase;
  const [activeTab, setActiveTab] = useState('detail');
  const [bugLinkTemplate, setBugLinkTemplate] = useState('');
  const [executionLogs, setExecutionLogs] = useState<TestCaseExecutionLog[]>([]);
  const [executionLogsLoading, setExecutionLogsLoading] = useState(false);
  const [selectedLogId, setSelectedLogId] = useState<number | null>(null);
  const [failPopoverOpen, setFailPopoverOpen] = useState(false);
  const [bugIdInput, setBugIdInput] = useState('');
  const [actionLoading, setActionLoading] = useState<'passed' | 'failed' | null>(null);

  useEffect(() => {
    if (!visible) return;

    setActiveTab('detail');
    setFailPopoverOpen(false);
    setBugIdInput('');
    configApi.getConfig().then((res) => {
      if (res.code === 200 && res.data) {
        setBugLinkTemplate(res.data.bug_link_template || '');
      }
    }).catch(() => {});
  }, [visible, selectedTestcase?.id]);

  useEffect(() => {
    if (!visible || activeTab !== 'logs' || !sessionId || !selectedTestcase?.id) {
      return;
    }

    let canceled = false;
    setExecutionLogsLoading(true);

    testcaseApi.getExecutionLogs(sessionId, selectedTestcase.id)
      .then((res) => {
        if (canceled) return;
        const logs = Array.isArray(res.data) ? res.data : [];
        setExecutionLogs(logs as TestCaseExecutionLog[]);
        setSelectedLogId(logs[0]?.id ?? null);
      })
      .catch(() => {
        if (canceled) return;
        setExecutionLogs([]);
        setSelectedLogId(null);
      })
      .finally(() => {
        if (!canceled) {
          setExecutionLogsLoading(false);
        }
      });

    return () => {
      canceled = true;
    };
  }, [activeTab, sessionId, selectedTestcase?.id, visible]);

  const selectedLog = useMemo(() => {
    if (executionLogs.length === 0) return null;
    return executionLogs.find((item) => item.id === selectedLogId) || executionLogs[0];
  }, [executionLogs, selectedLogId]);

  const steps = Array.isArray(selectedLog?.result?.steps) ? selectedLog.result.steps : [];
  const parsedBugId = Number(bugIdInput);
  const isBugIdValid = Number.isInteger(parsedBugId) && parsedBugId > 0;

  const handleMarkPassed = async () => {
    setActionLoading('passed');
    try {
      await onMarkPassed(tc);
    } finally {
      setActionLoading(null);
    }
  };

  const handleMarkFailed = async () => {
    if (!isBugIdValid) return;
    setActionLoading('failed');
    try {
      await onMarkFailed(tc, parsedBugId);
      setFailPopoverOpen(false);
      setBugIdInput('');
    } finally {
      setActionLoading(null);
    }
  };

  const failPopoverContent = (
    <div className="vtm-fail-popover">
      <div className="vtm-fail-popover__label">填写 Bug ID 后标记为不通过</div>
      <Input
        autoFocus
        value={bugIdInput}
        onChange={(event) => setBugIdInput(event.target.value.replace(/[^\d]/g, ''))}
        placeholder="输入 Bug ID"
        status={bugIdInput !== '' && !isBugIdValid ? 'error' : ''}
        onPressEnter={() => {
          if (isBugIdValid && actionLoading !== 'failed') {
            void handleMarkFailed();
          }
        }}
      />
      <div className="vtm-fail-popover__actions">
        <Button size="small" onClick={() => setFailPopoverOpen(false)}>
          取消
        </Button>
        <Button size="small" danger type="primary" loading={actionLoading === 'failed'} disabled={!isBugIdValid} onClick={() => void handleMarkFailed()}>
          确认不通过
        </Button>
      </div>
    </div>
  );

  return (
    <Drawer
      title="测试用例详情"
      open={visible}
      onClose={onCancel}
      placement="right"
      width={720}
      destroyOnHidden={false}
      className="vtm-drawer"
      styles={{
        header: { padding: '16px 20px 12px' },
        body: { padding: '0 20px 20px' },
        footer: { padding: '12px 20px', borderTop: '1px solid var(--color-border-light)' },
      }}
      footer={(
        <div className="vtm-footer vtm-footer--left">
          <Space size={8} wrap>
            <Button
              className="vtm-footer-btn vtm-footer-btn--success"
              icon={<CheckCircleOutlined style={{ fontSize: 12 }} />}
              loading={actionLoading === 'passed'}
              onClick={() => void handleMarkPassed()}
            >
              通过
            </Button>

            <Popover
              trigger="click"
              placement="topLeft"
              content={failPopoverContent}
              open={failPopoverOpen}
              onOpenChange={(open) => {
                setFailPopoverOpen(open);
                if (!open) setBugIdInput('');
              }}
            >
              <Button className="vtm-footer-btn vtm-footer-btn--danger" icon={<CloseCircleOutlined style={{ fontSize: 12 }} />}>
                不通过
              </Button>
            </Popover>

            <Button className="vtm-footer-btn" onClick={onNext} disabled={nextButtonDisabled} icon={<DownOutlined style={{ fontSize: 11 }} />}>
              下一个
            </Button>

            <Button className="vtm-footer-btn" onClick={onPrev} disabled={prevButtonDisabled} icon={<UpOutlined style={{ fontSize: 11 }} />}>
              上一个
            </Button>
          </Space>
        </div>
      )}
    >
      {tc && (
        <Tabs
          className="vtm-tabs-container"
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: 'detail',
              label: '用例详情',
              children: <TestcaseDetailView testcase={tc} bugLinkTemplate={bugLinkTemplate} />,
            },
            {
              key: 'scenario',
              label: '接口编排',
              children: <TestcaseScenarioView scenarioId={tc.scenario_id ?? undefined} />,
            },
            {
              key: 'logs',
              label: `执行日志${executionLogs.length > 0 ? ` (${executionLogs.length})` : ''}`,
              children: (
                <TestcaseExecutionLogPanel
                  loading={executionLogsLoading}
                  logs={executionLogs}
                  selectedLogId={selectedLogId}
                  onSelectedLogChange={setSelectedLogId}
                  selectedLog={selectedLog}
                  steps={steps}
                />
              ),
            },
          ]}
        />
      )}
    </Drawer>
  );
};

const formatTime = (value?: string) => {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
};

const renderJson = (value: unknown) => (
  <pre className="vtm-log-json">
    {JSON.stringify(value ?? {}, null, 2)}
  </pre>
);

const TestcaseExecutionLogPanel: React.FC<{
  loading: boolean;
  logs: TestCaseExecutionLog[];
  selectedLogId: number | null;
  onSelectedLogChange: (value: number | null) => void;
  selectedLog: TestCaseExecutionLog | null;
  steps: any[];
}> = ({ loading, logs, selectedLogId, onSelectedLogChange, selectedLog, steps }) => {
  if (loading) {
    return (
      <div className="vtm-log-state">
        <Spin />
      </div>
    );
  }

  if (logs.length === 0) {
    return (
      <div className="vtm-log-state">
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无执行日志" />
      </div>
    );
  }

  return (
    <div className="vtm-log-layout">
      <div className="vtm-log-toolbar">
        <Select
          value={selectedLogId ?? undefined}
          style={{ width: '100%' }}
          options={logs.map((log) => ({
            value: log.id,
            label: `${formatTime(log.created_at)} · ${log.passed ? '通过' : '失败'} · ${log.result?.steps?.length || 0} 步`,
          }))}
          onChange={(value) => onSelectedLogChange(value)}
        />
        <Space size={8}>
          <Tag color={selectedLog?.passed ? 'success' : 'error'}>{selectedLog?.passed ? '通过' : '失败'}</Tag>
          <Tag>{logs.length} 条</Tag>
        </Space>
      </div>

      {steps.length === 0 ? (
        <Alert type="info" message="该执行日志没有步骤详情" />
      ) : (
        <Table
          className="vtm-log-table"
          size="small"
          rowKey={(row) => `${row.index}-${row.endpoint_id || row.endpoint_name || row.detail || ''}`}
          pagination={false}
          dataSource={steps}
          columns={[
            { title: '#', dataIndex: 'index', width: 48, align: 'center' },
            {
              title: '步骤 / 接口',
              render: (_: unknown, row: any) => (
                <div>
                  <div className="vtm-log-step-title">{row.endpoint_name || row.name || row.testcase_step?.name || '-'}</div>
                  <div className="vtm-log-step-meta">{row.endpoint_method || ''} {row.endpoint_path || ''}</div>
                  {row.detail && <div className="vtm-log-step-error">{row.detail}</div>}
                </div>
              ),
            },
            {
              title: '结果',
              width: 82,
              align: 'center',
              render: (_: unknown, row: any) => <Tag color={row.status === 'passed' ? 'success' : 'error'}>{row.status === 'passed' ? '通过' : '失败'}</Tag>,
            },
            {
              title: '状态码',
              width: 90,
              align: 'center',
              render: (_: unknown, row: any) => row.response?.status_code ? <Tag>{row.response.status_code}</Tag> : '-',
            },
            {
              title: '耗时',
              width: 90,
              align: 'right',
              render: (_: unknown, row: any) => row.response?.elapsed_ms != null ? `${row.response.elapsed_ms} ms` : '-',
            },
          ]}
          expandable={{
            expandedRowRender: (row) => (
              <Collapse
                size="small"
                className="vtm-log-collapse"
                defaultActiveKey={['assertions', 'response-body']}
                items={[
                  { key: 'assertions', label: '断言结果', children: renderJson(row.assertions || []) },
                  {
                    key: 'response-body',
                    label: '响应 Body',
                    children: row.response?.body !== undefined ? renderJson(row.response.body) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无响应内容" />,
                  },
                  {
                    key: 'request-body',
                    label: '请求 Body',
                    children: row.request?.body !== undefined ? renderJson(row.request.body) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无请求内容" />,
                  },
                  { key: 'raw', label: '原始数据', children: renderJson(row) },
                ]}
              />
            ),
          }}
        />
      )}
    </div>
  );
};

export default ViewTestcaseModal;
