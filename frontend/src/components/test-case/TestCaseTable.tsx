import React, { useState } from 'react';
import { Table, Button, TableProps, message, Modal, Tooltip, Tag } from 'antd';
import {
  EyeOutlined,
  EditOutlined,
  CheckCircleOutlined,
  ApiOutlined,
  DragOutlined,
  ExclamationCircleOutlined,
  HistoryOutlined,
  BranchesOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import { TestCase, TestCaseStatus } from '../../types';

interface TestCaseTableProps {
  testcases: TestCase[];
  selectedTestcaseId?: number | null;
  onView: (testcase: TestCase) => void;
  onEdit: (testcase: TestCase) => void;
  onComplete: (testcase: TestCase) => void;
  onDelete: (id: number) => void;
  onBatchDelete: (ids: number[]) => void;
  onMove: (testcase: TestCase) => void;
  onApiExecute?: (testcase: TestCase) => void;
  onViewExecutionLogs?: (testcase: TestCase) => void;
  onInferDependencies?: (testcase: TestCase) => void;
  onValidate?: (testcase: TestCase) => void;
  onBatchMove: (ids: number[]) => void;
  bugLinkTemplate?: string;
}

const statusConfig: Record<string, { label: string; dotClass: string; textClass: string }> = {
  PASSED: { label: '已通过', dotClass: 'tcm-status-dot--passed', textClass: 'tcm-status-text--passed' },
  FAILED: { label: '未通过', dotClass: 'tcm-status-dot--failed', textClass: 'tcm-status-text--failed' },
  NOT_RUN: { label: '未执行', dotClass: 'tcm-status-dot--not_run', textClass: 'tcm-status-text--not_run' },
};

const safeParse = (text?: string | null): any => {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
};

const validationStatusMeta: Record<string, { label: string; color: string }> = {
  passed: { label: '验证通过', color: 'green' },
  failed: { label: '验证未通过', color: 'red' },
  error: { label: '验证错误', color: 'volcano' },
  pending: { label: '待验证', color: 'default' },
};

const TestCaseTable: React.FC<TestCaseTableProps> = ({
  testcases,
  selectedTestcaseId,
  onView,
  onEdit,
  onComplete,
  onDelete,
  onBatchDelete,
  onMove,
  onApiExecute,
  onViewExecutionLogs,
  onInferDependencies,
  onValidate,
  onBatchMove,
  bugLinkTemplate,
}) => {
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [isBatchDeleteModalVisible, setIsBatchDeleteModalVisible] = useState(false);

  const stopRowClick = (event: React.MouseEvent<HTMLElement>) => {
    event.stopPropagation();
  };

  const testcaseColumns = [
    {
      title: '#',
      dataIndex: 'index',
      key: 'index',
      width: 48,
      align: 'center' as const,
      render: (_: unknown, __: unknown, idx: number) => <span className="tcm-row-index">{idx + 1}</span>,
    },
    {
      title: '用例名称',
      dataIndex: 'case_name',
      key: 'case_name',
      ellipsis: true,
      render: (name: string) => <span className="tcm-case-name">{name}</span>,
    },
    {
      title: '级别',
      dataIndex: 'case_level',
      key: 'case_level',
      width: 64,
      align: 'center' as const,
      render: (level: number) => {
        const cls = `tcm-level-badge tcm-level-badge--${level || 4}`;
        return <span className={cls}>P{level || 4}</span>;
      },
    },
    {
      title: '接口',
      key: 'api_steps',
      width: 260,
      render: (_: unknown, record: TestCase) => {
        const scenarioSteps = record.scenario_steps || [];
        if (scenarioSteps.length > 0) {
          return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {scenarioSteps.map((step, i: number) => {
                const method = step.method ? step.method.toUpperCase() : '';
                const path = step.path || '';
                const label = method && path ? `${method} ${path}` : (step.name || `步骤 ${i + 1}`);
                return (
                  <Tooltip key={i} title={label}>
                    <Tag color="blue" style={{ fontSize: 11, margin: 0, cursor: 'pointer', maxWidth: 230, display: 'inline-flex', alignItems: 'center' }}>
                      <ApiOutlined style={{ marginRight: 4, flexShrink: 0 }} />
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>{label}</span>
                    </Tag>
                  </Tooltip>
                );
              })}
            </div>
          );
        }

        const allSteps = [
          ...((record.preset_conditions || []).filter((step: any) => typeof step === 'object' && step.type === 'api_call')),
          ...((record.steps || []).filter((step: any) => typeof step === 'object' && step.type === 'api_call')),
        ];

        if (allSteps.length === 0) {
          if (!record.api_endpoint_id) {
            return <span style={{ color: 'var(--color-text-disabled)', fontSize: 12 }}>-</span>;
          }

          const ids = String(record.api_endpoint_id).split(',').map((item) => item.trim()).filter(Boolean);
          return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {ids.map((id, i) => (
                <Tag key={i} color="default" style={{ fontSize: 11, margin: 0 }}>
                  <ApiOutlined style={{ marginRight: 4 }} />
                  接口 #{id}
                </Tag>
              ))}
            </div>
          );
        }

        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {allSteps.map((step: any, i: number) => {
              const method = step.method ? step.method.toUpperCase() : '';
              const path = step.path || step.url || '';
              const label = method && path ? `${method} ${path}` : (step.description || step.name || `步骤 ${i + 1}`);
              return (
                <Tooltip key={i} title={label}>
                  <Tag color="blue" style={{ fontSize: 11, margin: 0, cursor: 'pointer', maxWidth: 230, display: 'inline-flex', alignItems: 'center' }}>
                    <ApiOutlined style={{ marginRight: 4, flexShrink: 0 }} />
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>{label}</span>
                  </Tag>
                </Tooltip>
              );
            })}
          </div>
        );
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      align: 'center' as const,
      render: (status: TestCaseStatus) => {
        const cfg = statusConfig[status] || statusConfig.NOT_RUN;
        return (
          <span className="tcm-status">
            <span className={`tcm-status-dot ${cfg.dotClass}`} />
            <span className={cfg.textClass}>{cfg.label}</span>
          </span>
        );
      },
    },
    {
      title: '准确性',
      key: 'accuracy',
      width: 150,
      align: 'center' as const,
      render: (_: unknown, record: TestCase) => {
        if (!record.api_endpoint_id) {
          return <span style={{ color: 'var(--color-text-disabled)', fontSize: 12 }}>-</span>;
        }
        const report = safeParse(record.consistency_report);
        const passRate = report?.pass_rate;
        const confLevel = record.confidence_level;
        const valStatus = record.validation_status;
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'center' }}>
            {passRate !== undefined && passRate !== null ? (
              <Tooltip title="一致性通过率（对照 Swagger）">
                <Tag color={passRate >= 1 ? 'green' : passRate >= 0.6 ? 'orange' : 'red'} style={{ margin: 0 }}>
                  一致 {Math.round(passRate * 100)}%
                </Tag>
              </Tooltip>
            ) : null}
            {confLevel ? (
              <Tooltip title="置信度等级：高/中/低（人工复核优先级）">
                <Tag color={confLevel === 'high' ? 'green' : confLevel === 'medium' ? 'gold' : 'red'} style={{ margin: 0 }}>
                  {confLevel === 'high' ? '高置信' : confLevel === 'medium' ? '中置信' : '低置信'}
                </Tag>
              </Tooltip>
            ) : null}
            {valStatus ? (
              <Tooltip title="真实执行验证结果">
                <Tag color={validationStatusMeta[valStatus]?.color || 'default'} style={{ margin: 0 }}>
                  {validationStatusMeta[valStatus]?.label || valStatus}
                </Tag>
              </Tooltip>
            ) : null}
          </div>
        );
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 110,
      align: 'center' as const,
      render: (time: string) => {
        const date = new Date(time);
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        const hours = String(date.getHours()).padStart(2, '0');
        const minutes = String(date.getMinutes()).padStart(2, '0');
        return <span className="tcm-created-time">{month}-{day} {hours}:{minutes}</span>;
      },
    },
    {
      title: 'Bug',
      dataIndex: 'bug_id',
      key: 'bug_id',
      width: 64,
      align: 'center' as const,
      render: (bugId: number | undefined) => {
        if (!bugId) {
          return <span style={{ color: 'var(--color-text-disabled)', fontSize: 12 }}>-</span>;
        }

        const bugUrl = bugLinkTemplate
          ? bugLinkTemplate.replace('{bug_id}', String(bugId))
          : `http://zt.luban.fit/index.php?m=bug&f=view&bugID=${bugId}`;

        return (
          <a
            href={bugUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="tcm-bug-link"
            onClick={stopRowClick}
          >
            #{bugId}
          </a>
        );
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 224,
      align: 'center' as const,
      render: (_: unknown, record: TestCase) => (
        <div className="tcm-actions">
          <Tooltip title="查看" placement="top">
            <button className="tcm-action-btn tcm-action-btn--view" onClick={(event) => { stopRowClick(event); onView(record); }}>
              <EyeOutlined />
            </button>
          </Tooltip>
          <Tooltip title="编辑" placement="top">
            <button className="tcm-action-btn tcm-action-btn--edit" onClick={(event) => { stopRowClick(event); onEdit(record); }}>
              <EditOutlined />
            </button>
          </Tooltip>
          <Tooltip title="执行" placement="top">
            <button className="tcm-action-btn tcm-action-btn--execute" onClick={(event) => { stopRowClick(event); onComplete(record); }}>
              <CheckCircleOutlined />
            </button>
          </Tooltip>
          {onApiExecute && record.api_endpoint_id && (
            <Tooltip title="API 执行" placement="top">
              <button className="tcm-action-btn tcm-action-btn--api" onClick={(event) => { stopRowClick(event); onApiExecute(record); }}>
                <ApiOutlined />
              </button>
            </Tooltip>
          )}
          {onViewExecutionLogs && record.api_endpoint_id && (
            <Tooltip title="执行日志" placement="top">
              <button className="tcm-action-btn tcm-action-btn--log" onClick={(event) => { stopRowClick(event); onViewExecutionLogs(record); }}>
                <HistoryOutlined />
              </button>
            </Tooltip>
          )}
          {onInferDependencies && record.api_endpoint_id && (
            <Tooltip title="推断依赖" placement="top">
              <button className="tcm-action-btn tcm-action-btn--infer" onClick={(event) => { stopRowClick(event); onInferDependencies(record); }}>
                <BranchesOutlined />
              </button>
            </Tooltip>
          )}
          {onValidate && record.api_endpoint_id && (
            <Tooltip title="验证（真实执行）" placement="top">
              <button className="tcm-action-btn tcm-action-btn--validate" onClick={(event) => { stopRowClick(event); onValidate!(record); }}>
                <SafetyCertificateOutlined />
              </button>
            </Tooltip>
          )}
          <Tooltip title="移动" placement="top">
            <button className="tcm-action-btn tcm-action-btn--move" onClick={(event) => { stopRowClick(event); onMove(record); }}>
              <DragOutlined />
            </button>
          </Tooltip>
        </div>
      ),
    },
  ];

  const rowSelection: TableProps<TestCase>['rowSelection'] = {
    selectedRowKeys,
    onChange: (keys: React.Key[]) => setSelectedRowKeys(keys),
  };

  const handleBatchDelete = () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请选择要删除的测试用例');
      return;
    }
    setIsBatchDeleteModalVisible(true);
  };

  const handleConfirmBatchDelete = () => {
    onBatchDelete(selectedRowKeys.map(Number));
    setSelectedRowKeys([]);
    setIsBatchDeleteModalVisible(false);
  };

  return (
    <>
      {selectedRowKeys.length > 0 && (
        <div className="tcm-batch-bar">
          <span className="tcm-batch-count">
            已选 <strong>{selectedRowKeys.length}</strong> 项
          </span>
          <Button size="small" danger icon={<ExclamationCircleOutlined />} onClick={handleBatchDelete}>
            批量删除
          </Button>
          <Button size="small" onClick={() => onBatchMove(selectedRowKeys.map(Number))}>
            批量移动
          </Button>
        </div>
      )}

      <div className="tcm-table">
        <Table
          rowSelection={{ type: 'checkbox', ...rowSelection }}
          columns={testcaseColumns}
          dataSource={testcases}
          rowKey="id"
          onRow={(record) => ({
            onClick: () => onView(record),
            className: record.id === selectedTestcaseId ? 'tcm-table-row is-active' : 'tcm-table-row',
          })}
          pagination={{
            position: ['bottomRight'],
            defaultPageSize: 20,
            size: 'small',
            showTotal: (total) => <span style={{ color: 'var(--color-text-tertiary)', fontSize: 12 }}>共 {total} 条</span>,
          }}
          scroll={{ x: 700, y: '47vh' }}
          tableLayout="fixed"
          size="small"
          locale={{ emptyText: '暂无测试用例' }}
        />
      </div>

      <Modal
        title="批量删除确认"
        open={isBatchDeleteModalVisible}
        onOk={handleConfirmBatchDelete}
        onCancel={() => setIsBatchDeleteModalVisible(false)}
        okText="确认删除"
        cancelText="取消"
        okType="danger"
      >
        <p>确定要删除选中的 {selectedRowKeys.length} 个测试用例吗？</p>
        <p style={{ color: 'var(--color-danger)', fontSize: 13 }}>此操作不可恢复。</p>
      </Modal>
    </>
  );
};

export default TestCaseTable;
