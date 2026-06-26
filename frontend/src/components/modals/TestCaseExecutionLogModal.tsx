import React, { useMemo, useState } from 'react';
import { Alert, Button, Collapse, Descriptions, Empty, Modal, Select, Table, Tag } from 'antd';
import { CopyOutlined } from '@ant-design/icons';
import { TestCase, TestCaseExecutionLog } from '../../types';

const assertionTypeLabels: Record<string, string> = {
  status_code_range: '状态码范围',
  status_code: '状态码等于',
  response_time_lt: '响应时间小于',
  jsonpath_exists: '响应字段存在',
  jsonpath_equals: '响应字段等于',
};

interface TestCaseExecutionLogModalProps {
  visible: boolean;
  testcase: TestCase | null;
  logs: TestCaseExecutionLog[];
  loading: boolean;
  onCancel: () => void;
}

const formatTime = (value?: string) => {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
};

const renderValue = (value: unknown) => {
  if (value === undefined || value === null || value === '') {
    return <span style={{ color: 'var(--color-text-disabled)' }}>暂无</span>;
  }
  if (typeof value === 'object') {
    return (
      <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 12 }}>
        {JSON.stringify(value, null, 2)}
      </pre>
    );
  }
  return <span style={{ wordBreak: 'break-word' }}>{String(value)}</span>;
};

const renderJsonBlock = (value: unknown) => (
  <pre style={{ maxHeight: 320, overflow: 'auto', background: 'var(--color-bg)', padding: 12, borderRadius: 6, fontSize: 12, whiteSpace: 'pre-wrap' }}>
    {JSON.stringify(value ?? {}, null, 2)}
  </pre>
);

const renderKeyValueTable = (value: unknown) => {
  const record = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
  const rows = Object.entries(record).map(([key, item]) => ({ key, value: item }));
  if (rows.length === 0) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据" />;
  return (
    <Table
      size="small"
      pagination={false}
      dataSource={rows}
      columns={[
        { title: '字段', dataIndex: 'key', width: 220 },
        { title: '值', dataIndex: 'value', render: renderValue },
      ]}
    />
  );
};

const renderAssertionTable = (assertions: any[] | undefined) => {
  const rows = Array.isArray(assertions) ? assertions.map((item, index) => ({ key: index, ...item })) : [];
  if (rows.length === 0) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无断言结果" />;
  return (
    <Table
      size="small"
      pagination={false}
      dataSource={rows}
      columns={[
        {
          title: '',
          dataIndex: 'passed',
          width: 36,
          render: (passed: boolean) => <Tag color={passed ? 'success' : 'error'}>{passed ? '通过' : '失败'}</Tag>,
        },
        { title: '类型', dataIndex: 'type', width: 150, render: (type: string) => assertionTypeLabels[type] || type || '-' },
        { title: '期望', dataIndex: 'expected', render: renderValue },
        { title: '实际', dataIndex: 'actual', render: renderValue },
      ]}
    />
  );
};

const StepDetail: React.FC<{ step: any }> = ({ step }) => {
  const request = step.request || {};
  const response = step.response || {};
  const method = request.method || step.endpoint_method || '-';
  const endpointName = step.endpoint_name || step.name || step.testcase_step?.name || '-';
  const statusCode = response.status_code;
  const statusCodeOk = statusCode !== undefined && statusCode >= 200 && statusCode < 400;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Descriptions size="small" column={1} bordered>
        <Descriptions.Item label="接口">{endpointName}</Descriptions.Item>
        <Descriptions.Item label="Method"><Tag color="blue">{method}</Tag></Descriptions.Item>
        <Descriptions.Item label="URL">{renderValue(request.url)}</Descriptions.Item>
        <Descriptions.Item label="响应状态">
          {statusCode !== undefined ? <Tag color={statusCodeOk ? 'success' : 'error'}>{statusCode}</Tag> : '暂无响应'}
        </Descriptions.Item>
        <Descriptions.Item label="耗时">{response.elapsed_ms !== undefined ? `${response.elapsed_ms} ms` : '暂无'}</Descriptions.Item>
        {step.detail && <Descriptions.Item label="错误">{step.detail}</Descriptions.Item>}
      </Descriptions>
      <Collapse
        size="small"
        defaultActiveKey={['assertions', 'response-body']}
        items={[
          { key: 'assertions', label: '断言结果', children: renderAssertionTable(step.assertions) },
          { key: 'response-body', label: '响应 Body', children: response.body !== undefined ? renderJsonBlock(response.body) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无响应" /> },
          { key: 'request-body', label: '请求 Body', children: request.body !== undefined ? renderJsonBlock(request.body) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无请求体" /> },
          { key: 'request-headers', label: '请求头', children: renderKeyValueTable(request.headers) },
          { key: 'request-params', label: '请求参数', children: renderKeyValueTable(request.params) },
          { key: 'response-headers', label: '响应头', children: renderKeyValueTable(response.headers) },
          { key: 'pre-vars', label: '前置变量', children: renderKeyValueTable(step.pre_updates) },
          { key: 'post-extract', label: '后置提取', children: renderKeyValueTable(step.extracted) },
          { key: 'raw', label: '原始数据', children: renderJsonBlock(step) },
        ]}
      />
    </div>
  );
};

const TestCaseExecutionLogModal: React.FC<TestCaseExecutionLogModalProps> = ({
  visible,
  testcase,
  logs,
  loading,
  onCancel,
}) => {
  const [selectedLogId, setSelectedLogId] = useState<number | null>(null);
  const selectedLog = useMemo(() => {
    if (logs.length === 0) return null;
    return logs.find((item) => item.id === selectedLogId) || logs[0];
  }, [logs, selectedLogId]);
  const steps = Array.isArray(selectedLog?.result?.steps) ? selectedLog!.result.steps : [];

  const handleCopy = async () => {
    if (!selectedLog) return;
    await navigator.clipboard.writeText(JSON.stringify(selectedLog.result, null, 2));
  };

  return (
    <Modal
      title={`执行日志${testcase?.case_name ? ` - ${testcase.case_name}` : ''}`}
      open={visible}
      onCancel={onCancel}
      width={980}
      footer={<Button onClick={onCancel}>关闭</Button>}
      styles={{ body: { padding: '16px 20px' } }}
    >
      {logs.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={loading ? '正在加载执行日志' : '暂无执行日志'} />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Select
              size="small"
              style={{ minWidth: 360, flex: 1 }}
              value={selectedLog?.id}
              options={logs.map((log) => ({
                value: log.id,
                label: `${formatTime(log.created_at)} · ${log.passed ? '通过' : '失败'} · ${log.result?.steps?.length || 0} 步`,
              }))}
              onChange={setSelectedLogId}
            />
            <Tag color={selectedLog?.passed ? 'success' : 'error'}>{selectedLog?.passed ? '通过' : '失败'}</Tag>
            <Tag>{logs.length}/10</Tag>
            <Button size="small" icon={<CopyOutlined />} onClick={handleCopy}>复制</Button>
          </div>
          {selectedLog?.result?.steps ? (
            <Table
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
                      <div style={{ fontWeight: 500 }}>{row.endpoint_name || row.name || row.testcase_step?.name || '-'}</div>
                      <div style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>{row.endpoint_method || ''} {row.endpoint_path || ''}</div>
                      {row.detail && <div style={{ fontSize: 12, color: 'var(--color-error)' }}>{row.detail}</div>}
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
                expandedRowRender: (row) => <StepDetail step={row} />,
              }}
            />
          ) : (
            <Alert type="info" message="该日志没有步骤详情" />
          )}
        </div>
      )}
    </Modal>
  );
};

export default TestCaseExecutionLogModal;
